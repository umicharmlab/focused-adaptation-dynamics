import pathlib
from time import perf_counter
from typing import Optional, List, Dict, Union

import numpy as np
import tensorflow as tf
from matplotlib import cm

import rospy
from arc_utilities.algorithms import nested_dict_update
from link_bot_classifiers import classifier_utils
from link_bot_data.dataset_utils import tf_write_example
from link_bot_data.files_dataset import FilesDataset
from link_bot_data.recovery_dataset import RecoveryDatasetLoader, compute_recovery_probabilities
from link_bot_data.recovery_dataset_utils import batch_stateless_sample_action, \
    predict_and_classify_for_recovery_dataset, visualize_recovery_generation
from link_bot_gazebo.gazebo_services import GazeboServices
from link_bot_planning.analysis import results_utils
from link_bot_planning.analysis.results_utils import NoTransitionsError, get_recovery_transitions, \
    classifier_params_from_planner_params, dynamics_dataset_params_from_classifier_params
from link_bot_planning.results_to_classifier_dataset import compute_example_idx
from link_bot_pycommon.job_chunking import JobChunker
from link_bot_pycommon.marker_index_generator import marker_index_generator
from link_bot_pycommon.pycommon import try_make_dict_tf_float32, pathify, log_scale_0_to_1
from link_bot_pycommon.serialization import my_hdump
from merrrt_visualization.rviz_animation_controller import RvizAnimationController
from moonshine.filepath_tools import load_hjson
from moonshine.indexing import index_batch_time
from moonshine.moonshine_utils import repeat, remove_batch, \
    add_batch
from state_space_dynamics import dynamics_utils


class ResultsToRecoveryDataset:

    def __init__(self,
                 results_dir: pathlib.Path,
                 outdir: pathlib.Path,
                 labeling_params: Optional[Union[pathlib.Path, Dict]] = None,
                 trial_indices: Optional[List[int]] = None,
                 visualize: bool = False,
                 regenerate: bool = False,
                 test_split: Optional[float] = None,
                 val_split: Optional[float] = None,
                 verbose: int = 1,
                 **kwargs):
        self.rng = np.random.RandomState(0)
        self.service_provider = GazeboServices()
        self.results_dir = results_dir
        self.outdir = outdir
        self.trial_indices = trial_indices
        self.verbose = verbose
        self.regenerate = regenerate

        print(f"Writing to {outdir.as_posix()}")

        if labeling_params is None:
            labeling_params = pathlib.Path('labeling_params/recovery/dual.json')

        if isinstance(labeling_params, Dict):
            self.labeling_params = labeling_params
        else:
            self.labeling_params = load_hjson(labeling_params)

        self.threshold = self.labeling_params['threshold']

        self.visualize = visualize
        self.scenario, self.metadata = results_utils.get_scenario_and_metadata(results_dir)

        self.planner_params_for_results = self.metadata['planner_params']
        fwd_model_dirs = pathify(self.planner_params_for_results['fwd_model_dir'])
        classifier_model_dir = pathify(self.planner_params_for_results['classifier_model_dir'])[0]
        # we assume index [0] is the learned classifier, this assert tries to catch this
        assert 'logdir' in classifier_model_dir.as_posix()
        self.fwd_model, _ = dynamics_utils.load_generic_model(fwd_model_dirs, self.scenario)
        self.classifier = classifier_utils.load_generic_model(classifier_model_dir, self.scenario)

        self.action_rng = np.random.RandomState(0)
        self.action_params = self.classifier.data_collection_params
        self.action_params.update(self.planner_params_for_results)
        self.action_params.update(self.planner_params_for_results['action_params'])

        self.example_idx = None
        self.files = FilesDataset(outdir, val_split, test_split)

        outdir.mkdir(exist_ok=True, parents=True)

    def run(self):
        planner_params = self.metadata['planner_params']
        classifier_params = classifier_params_from_planner_params(planner_params)
        phase2_dataset_params = dynamics_dataset_params_from_classifier_params(classifier_params)
        dataset_hparams = phase2_dataset_params
        dataset_hparams_update = {
            'from_results':           self.results_dir.as_posix(),
            'seed':                   None,
            'state_keys':             self.fwd_model.state_keys,
            'labeling_params':        self.labeling_params,
            'data_collection_params': {
                'steps_per_traj': 2,
            },
        }
        dataset_hparams = nested_dict_update(dataset_hparams, dataset_hparams_update)
        with (self.outdir / 'hparams.hjson').open('w') as dataset_hparams_file:
            my_hdump(dataset_hparams, dataset_hparams_file, indent=2)

        self.results_to_recovery_dataset()

    def results_to_recovery_dataset(self):
        logfilename = self.outdir / 'logfile.hjson'
        job_chunker = JobChunker(logfilename)

        # basically this just loads the hparams file (that we just wrote) describing the states/actions in the dataset,
        # so we can call the visualization functions it defines using that info
        dataset_for_viz = RecoveryDatasetLoader([self.outdir])

        t0 = perf_counter()
        last_t = t0
        total_examples = 0
        for trial_idx, datum in results_utils.trials_generator(self.results_dir, self.trial_indices):
            self.scenario.heartbeat()

            if job_chunker.has_result(str(trial_idx)) and not self.regenerate:
                rospy.loginfo(f"Found existing recovery data for trial {trial_idx}")
                continue

            self.clear_markers()
            self.before_state_idx = marker_index_generator(0)
            self.after_state_idx = marker_index_generator(3)
            self.action_idx = marker_index_generator(5)

            example_idx_for_trial = 0

            self.example_idx = compute_example_idx(trial_idx, example_idx_for_trial)
            try:
                for example in self.result_datum_to_recovery_dataset(datum, trial_idx):
                    now = perf_counter()
                    dt = now - last_t
                    total_dt = now - t0
                    last_t = now

                    if self.visualize:
                        pass
                        # TODO: make this visualization work? do we really need this?
                        # visualize_recovery_generation(self.scenario, dataset_for_viz, self.fwd_model, example,
                        #                               self.labeling_params)

                    self.example_idx = compute_example_idx(trial_idx, example_idx_for_trial)
                    total_examples += 1
                    if self.verbose >= 0:
                        msg = ' '.join([f'Trial {trial_idx}',
                                        f'Example {self.example_idx}',
                                        f'dt={dt:.3f},',
                                        f'total time={total_dt:.3f},',
                                        f'{total_examples=}'])
                        print(msg)
                    example = try_make_dict_tf_float32(example)
                    full_filename = tf_write_example(self.outdir, example, self.example_idx)
                    self.files.add(full_filename)
                    example_idx_for_trial += 1

                    job_chunker.store_result(trial_idx, {'trial':              trial_idx,
                                                         'examples for trial': example_idx_for_trial})
            except NoTransitionsError:
                rospy.logerr(f"Trial {trial_idx} had no transitions")
                pass

            job_chunker.store_result(trial_idx, {'trial':              trial_idx,
                                                 'examples for trial': example_idx_for_trial})

        self.files.split()

    def result_datum_to_recovery_dataset(self, datum: Dict, trial_idx: int):
        for t, transition in enumerate(get_recovery_transitions(datum)):
            environment, action, before_state, after_state, _ = transition
            if self.visualize:
                self.visualize_example(action=action,
                                       after_state=after_state,
                                       before_state=before_state,
                                       environment=environment)

            yield from self.generate_example(
                environment=environment,
                action=action,
                before_state=before_state,
                after_state=after_state,
                start_t=t,
            )

    def generate_example(self,
                         environment: Dict,
                         action: Dict,
                         before_state: Dict,
                         after_state: Dict,
                         start_t: int):
        # sample actions starting from after_state
        # run them through the forward model
        # run them through the classifier
        # generate the recovery_probability label

        classifier_horizon = 2  # this script only handles this case
        batch_size = 1
        n_action_samples = self.labeling_params['n_action_samples']
        n_actions = classifier_horizon - 1
        # actions will be of shape [1, n_action_samples, 1, n]

        start_state_batch = {k: tf.tile(v[tf.newaxis], [n_action_samples, 1]) for k, v in after_state.items()}
        start_state_batch_time = {k: tf.tile(v[:, tf.newaxis], [1, 1, 1]) for k, v in start_state_batch.items()}
        random_actions_dict = batch_stateless_sample_action(scenario=self.scenario,
                                                            environment=environment,
                                                            state=start_state_batch,
                                                            batch_size=batch_size,
                                                            n_action_samples=n_action_samples,
                                                            n_actions=n_actions,
                                                            action_params=self.action_params,
                                                            action_rng=self.action_rng)
        random_actions_dict = remove_batch(random_actions_dict)
        bs = batch_size * n_action_samples
        environment = environment.copy()
        scene_msg = environment.pop("scene_msg")
        environment_batched = repeat(environment, n_action_samples, 0, True)
        environment_batched['scene_msg'] = [scene_msg] * n_action_samples

        predictions, accept_probabilities = predict_and_classify_for_recovery_dataset(
            self.fwd_model,
            self.classifier,
            environment_batched,  # [b*nas, ...]
            start_state_batch_time,  # [b*nas, 1, ...]
            random_actions_dict,  # [b*nas, 1, ...]
            bs,
            classifier_horizon)

        if self.visualize:
            anim = RvizAnimationController(n_time_steps=n_action_samples)
            while not anim.done:
                i = anim.t()
                p_i = remove_batch(compute_recovery_probabilities(add_batch(accept_probabilities), n_action_samples))
                a_i = index_batch_time(random_actions_dict, self.fwd_model.action_keys, i, 0)
                self.scenario.plot_recovery_probability(p_i)
                temp = log_scale_0_to_1(tf.squeeze(p_i), k=100)
                self.scenario.plot_action_rviz(after_state, a_i, label='proposed', color=cm.Greens(temp), idx=1)
                self.scenario.plot_accept_probability(accept_probabilities[i])

                anim.step()

        example = {
            'start_t':              start_t,
            'end_t':                start_t + classifier_horizon,
            'traj_idx':             self.example_idx,
            'accept_probabilities': accept_probabilities,
        }
        example.update(environment)
        example.update(before_state)
        example.update(action)

        yield example

    def visualize_example(self,
                          action: Dict,
                          after_state: Dict,
                          before_state: Dict,
                          environment: Dict):
        self.scenario.plot_environment_rviz(environment)
        self.scenario.plot_state_rviz(before_state, idx=next(self.before_state_idx), label='actual')
        self.scenario.plot_action_rviz(before_state, action, idx=next(self.action_idx), label='actual', color='pink')
        self.scenario.plot_state_rviz(after_state, idx=next(self.after_state_idx), label='actual')

    def clear_markers(self):
        self.scenario.reset_planning_viz()
