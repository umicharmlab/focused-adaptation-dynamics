import pathlib
from copy import deepcopy
from typing import Dict, Callable, List

from tqdm import tqdm

from augmentation.aug_opt import AugmentationOptimization
from learn_invariance.new_dynamics_dataset import NewDynamicsDatasetLoader
from link_bot_data.dataset_utils import write_example, add_predicted
from link_bot_data.local_env_helper import LocalEnvHelper
from link_bot_data.modify_dataset import modify_hparams
from link_bot_data.new_base_dataset import NewBaseDatasetLoader
from link_bot_data.new_classifier_dataset import NewClassifierDatasetLoader
from link_bot_data.split_dataset import split_dataset
from link_bot_data.visualization import classifier_transition_viz_t, DebuggingViz, init_viz_env, dynamics_viz_t
from link_bot_pycommon.debugging_utils import debug_viz_batch_indices
from link_bot_pycommon.scenario_with_visualization import ScenarioWithVisualization
from merrrt_visualization.rviz_animation_controller import RvizAnimation
from moonshine.indexing import try_index_batched_dict
from moonshine.moonshine_utils import remove_batch, numpify


def unbatch_examples(example, actual_batch_size):
    example_copy = deepcopy(example)
    if 'batch_size' in example_copy:
        example_copy.pop('batch_size')
    if 'time' in example_copy:
        example_copy.pop('time')
    if 'metadata' in example_copy:
        example_copy.pop('metadata')
    for b in range(actual_batch_size):
        out_example_b = {k: v[b] for k, v in example_copy.items()}
        if 'error' in out_example_b:
            out_example_b['metadata'] = {
                'error': out_example_b['error'],
            }
        yield out_example_b


def augment_dynamics_dataset(dataset_dir: pathlib.Path,
                             hparams: Dict,
                             outdir: pathlib.Path,
                             n_augmentations: int,
                             scenario=None,
                             visualize: bool = False,
                             batch_size: int = 32,
                             save_format='pkl'):
    dataset_loader = NewDynamicsDatasetLoader([dataset_dir])
    if scenario is None:
        scenario = dataset_loader.get_scenario()

    # current needed because mujoco IK requires a fully setup simulation...
    scenario.on_before_data_collection(dataset_loader.data_collection_params)

    def viz_f(_scenario, example, **kwargs):
        example = numpify(example)
        state_keys = list(filter(lambda k: k in example, dataset_loader.state_keys))
        anim = RvizAnimation(_scenario,
                             n_time_steps=example['time_idx'].size,
                             init_funcs=[
                                 init_viz_env
                             ],
                             t_funcs=[
                                 init_viz_env,
                                 dynamics_viz_t(metadata={},
                                                label='aug',
                                                state_metadata_keys=dataset_loader.state_metadata_keys,
                                                state_keys=state_keys,
                                                action_keys=dataset_loader.action_keys),
                             ])
        anim.play(example)

    debug_state_keys = dataset_loader.state_keys
    return augment_dataset_from_loader(dataset_loader,
                                       viz_f,
                                       dataset_dir,
                                       hparams,
                                       outdir,
                                       n_augmentations,
                                       debug_state_keys,
                                       scenario,
                                       visualize,
                                       batch_size,
                                       save_format)


def augment_classifier_dataset(dataset_dir: pathlib.Path,
                               hparams: Dict,
                               outdir: pathlib.Path,
                               n_augmentations: int,
                               scenario,
                               visualize: bool = False,
                               batch_size: int = 128,
                               save_format='pkl'):
    dataset_loader = NewClassifierDatasetLoader([dataset_dir])
    viz_f = classifier_transition_viz_t(metadata={},
                                        state_metadata_keys=dataset_loader.state_metadata_keys,
                                        predicted_state_keys=dataset_loader.predicted_state_keys,
                                        true_state_keys=None)
    debug_state_keys = [add_predicted(k) for k in dataset_loader.state_keys]
    return augment_dataset_from_loader(dataset_loader,
                                       viz_f,
                                       dataset_dir,
                                       hparams,
                                       outdir,
                                       n_augmentations,
                                       debug_state_keys,
                                       scenario,
                                       visualize,
                                       batch_size,
                                       save_format)


def augment_dataset_from_loader(dataset_loader: NewBaseDatasetLoader,
                                viz_f: Callable,
                                dataset_dir: pathlib.Path,
                                hparams: Dict,
                                outdir: pathlib.Path,
                                n_augmentations: int,
                                debug_state_keys,
                                scenario,
                                visualize: bool = False,
                                batch_size: int = 128,
                                save_format='pkl'):
    aug = make_aug_opt(scenario, dataset_loader, hparams, debug_state_keys, batch_size)

    def augment(inputs):
        actual_batch_size = inputs['batch_size']
        if visualize:
            scenario.reset_viz()

            inputs_viz = remove_batch(inputs)
            viz_f(scenario, inputs_viz, idx=0, color='g')
            # viz_f(scenario, inputs_viz, t=1, idx=1, color='g')

        time = inputs['time_idx'].shape[1]

        for k in range(n_augmentations):
            output = aug.aug_opt(inputs, batch_size=actual_batch_size, time=time)
            output['augmented_from'] = inputs['full_filename']

            if visualize:
                for b in debug_viz_batch_indices(actual_batch_size):
                    output_b = try_index_batched_dict(output, b)
                    viz_f(scenario, output_b, idx=k, color='#0000ff88')
                    # viz_f(scenario, remove_batch(output), t=1, idx=2 * k + 3, color='#0000ff88')

            yield output

    def out_examples_gen():
        for example in dataset:
            # the original example should also be included!
            actual_batch_size = example['batch_size']
            for out_example in augment(example):
                yield from unbatch_examples(out_example, actual_batch_size)
            yield from unbatch_examples(example, actual_batch_size)

    modify_hparams(dataset_dir, outdir, update={'used_augmentation': True})
    dataset = dataset_loader.get_datasets(mode='all')
    expected_total = (1 + n_augmentations) * len(dataset)

    dataset = dataset.batch(batch_size)
    total_count = 0
    for out_example in tqdm(out_examples_gen(), total=expected_total):
        write_example(outdir, out_example, total_count, save_format)
        total_count += 1
    split_dataset(outdir, val_split=0, test_split=0)

    return outdir


def make_aug_opt(scenario: ScenarioWithVisualization,
                 dataset_loader: NewBaseDatasetLoader,
                 hparams: Dict,
                 debug_state_keys: List[str],
                 batch_size: int):
    debug = DebuggingViz(scenario, debug_state_keys, dataset_loader.action_keys)
    local_env_helper = LocalEnvHelper(h=hparams['local_env_h_rows'],
                                      w=hparams['local_env_w_cols'],
                                      c=hparams['local_env_c_channels'])
    aug = AugmentationOptimization(scenario=scenario,
                                   debug=debug,
                                   local_env_helper=local_env_helper,
                                   hparams=hparams,
                                   batch_size=batch_size,
                                   state_keys=dataset_loader.state_keys,
                                   action_keys=dataset_loader.action_keys,
                                   points_state_keys=dataset_loader.points_state_keys,
                                   )
    return aug