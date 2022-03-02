import logging
import pathlib
from typing import Dict

import torch
from torch.utils.data import Dataset, DataLoader

from link_bot_data.dataset_utils import pprint_example
from link_bot_data.new_dataset_utils import get_filenames, load_single, DynamicsDatasetParams
from link_bot_data.visualization import dynamics_viz_t, init_viz_env
from link_bot_pycommon.get_scenario import get_scenario
from merrrt_visualization.rviz_animation_controller import RvizAnimation
from moonshine.indexing import index_time_batched, index_time
from moonshine.moonshine_utils import get_num_workers
from moonshine.numpify import numpify
from moonshine.torch_and_tf_utils import remove_batch
from moonshine.torch_datasets_utils import take_subset, my_collate

logger = logging.getLogger(__file__)


def remove_keys(*keys):
    def _remove_keys(example):
        for k in keys:
            if k in example:
                example.pop(k)
        return example

    return _remove_keys


def add_stats_to_example(example: Dict, stats: Dict):
    for k, stats_k in stats.items():
        example[f'{k}/mean'] = stats_k[0]
        example[f'{k}/std'] = stats_k[1]
        example[f'{k}/n'] = stats_k[2]
    return example


class TorchLoaderWrapped:
    """ this class is an attempt to make a pytorch dataset look like a NewBaseDataset objects """

    def __init__(self, dataset):
        self.dataset = dataset

    def take(self, take: int):
        dataset_subset = take_subset(self.dataset, take)
        return TorchLoaderWrapped(dataset=dataset_subset)

    def batch(self, batch_size: int):
        loader = DataLoader(dataset=self.dataset,
                            batch_size=batch_size,
                            shuffle=True,
                            collate_fn=my_collate,
                            num_workers=get_num_workers(batch_size=batch_size))
        for example in loader:
            actual_batch_size = list(example.values())[0].shape[0]
            example['batch_size'] = actual_batch_size
            yield example

    def __len__(self):
        return len(self.dataset)

    def __iter__(self):
        return iter(DataLoader(dataset=self.dataset,
                               batch_size=None,
                               shuffle=True,
                               num_workers=get_num_workers(batch_size=1)))


class TorchDynamicsDataset(Dataset, DynamicsDatasetParams):

    def __init__(self, dataset_dir: pathlib.Path, mode: str, transform=None, add_stats=False):
        DynamicsDatasetParams.__init__(self, dataset_dir)
        self.dataset_dir = dataset_dir
        self.mode = mode
        self.metadata_filenames = get_filenames([dataset_dir], mode)
        self.add_stats = add_stats

        self.transform = transform
        self.scenario = None

    def __len__(self):
        return len(self.metadata_filenames)

    def __getitem__(self, idx):
        if torch.is_tensor(idx):
            idx = idx.tolist()

        metadata_filename = self.metadata_filenames[idx]
        example = load_single(metadata_filename)

        if self.transform:
            example = self.transform(example)

        return example

    def get_scenario(self):
        if self.scenario is None:
            self.scenario = get_scenario(self.params['scenario'], self.scenario_params)

        return self.scenario

    def get_datasets(self, mode=None):
        if mode != self.mode:
            raise RuntimeError("the mode must be set when constructing the Dataset, not when calling get_datasets")
        return TorchLoaderWrapped(dataset=self)

    def index_time_batched(self, example_batched, t: int):
        e_t = numpify(remove_batch(index_time_batched(example_batched, self.time_indexed_keys, t, False)))
        return e_t

    def index_time(self, example, t: int):
        e_t = numpify(index_time(example, self.time_indexed_keys, t, False))
        return e_t

    def pprint_example(self):
        pprint_example(self[0])

    def dynamics_viz_t(self):
        return dynamics_viz_t(metadata={},
                              state_metadata_keys=self.state_metadata_keys,
                              state_keys=self.state_keys,
                              action_keys=self.action_keys)

    def anim_rviz(self, example: Dict):
        anim = RvizAnimation(self.get_scenario(),
                             n_time_steps=example['time_idx'].size,
                             init_funcs=[
                                 init_viz_env
                             ],
                             t_funcs=[
                                 init_viz_env,
                                 self.dynamics_viz_t()
                             ])
        anim.play(example)


def get_batch_size(batch):
    batch_size = len(batch['time_idx'])
    return batch_size