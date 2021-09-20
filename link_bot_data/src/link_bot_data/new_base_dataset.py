import pathlib
from multiprocessing import get_context
from typing import List, Dict, Optional, Callable

import numpy as np
import tensorflow as tf

from link_bot_data.dataset_utils import batch_sequence, merge_hparams_dicts, pprint_example
from link_bot_data.new_dataset_utils import get_filenames, UNUSED_COMPAT, load_single
from link_bot_pycommon.get_scenario import get_scenario
from link_bot_pycommon.scenario_with_visualization import ScenarioWithVisualization
from moonshine.moonshine_utils import batch_examples_dicts


class NewBaseDataset:

    def __init__(self, loader, filenames: List, mode, post_process: Optional[List[Callable]] = None, n_prefetch=2):
        self.loader = loader
        self.mode = mode
        self.filenames = filenames
        if post_process is None:
            self._post_process = []
        else:
            self._post_process = post_process
        self.n_prefetch = n_prefetch

    def __iter__(self):
        if self.n_prefetch is None or self.n_prefetch == 0:
            generator = self.iter_serial()
        else:
            generator = self.iter_multiprocessing()

        for example in generator:
            # NOTE: I don't like this, it's inconsistent about calling post_process with batched/non-batched inputs
            example = self.loader.post_process(example)
            for p in self._post_process:
                example = p(example)

            yield example

    def iter_serial(self):
        print("Using slow, serial iteration")
        for filenames in self.filenames:
            if isinstance(filenames, list):
                examples_i = [load_single(metadata_filename_i) for metadata_filename_i in filenames]
                example = batch_examples_dicts(examples_i)
            else:
                example = load_single(filenames)

            yield example

    def iter_multiprocessing(self):
        assert self.n_prefetch > 0
        for idx, filenames_i in enumerate(self.filenames):
            if isinstance(filenames_i, list):
                examples_i = list(self.loader.pool.map(load_single, filenames_i))
                example = batch_examples_dicts(examples_i)
            else:
                example = load_single(filenames_i)
            yield example

    def get_example(self, idx: int):
        filename = self.filenames[idx]
        return load_single(filename)

    def __len__(self):
        return len(self.filenames)

    def batch(self, batch_size: int, drop_remainder: bool = False):
        if batch_size is None:
            return self.__class__(self.loader, self.filenames, self.mode, self._post_process, self.n_prefetch)

        filenames_batched = list(batch_sequence(self.filenames, batch_size, drop_remainder))

        def _include_batch_size(example: Dict):
            actual_batch_size = len(example['rope'])
            example['batch_size'] = actual_batch_size
            return example

        # use self.__class__ here so that derived dataset classes return instances of themselves not the base class
        batched = self.__class__(self.loader, filenames_batched, self.mode, self._post_process, self.n_prefetch)
        return batched.map(_include_batch_size)

    def shuffle(self, buffer_size=UNUSED_COMPAT, seed: Optional[int] = 0, reshuffle_each_iteration=UNUSED_COMPAT):
        # FIXME: actually implementing this would be nice
        shuffled_filenames = self.filenames.copy()
        rng = np.random.RandomState(seed)
        rng.shuffle(shuffled_filenames)
        return self.__class__(self.loader, shuffled_filenames, self.mode, self._post_process, self.n_prefetch)

    def skip(self, skip: int):
        return self.__class__(self.loader, self.filenames[skip:], self.mode, self._post_process, self.n_prefetch)

    def shard(self, shard: int):
        return self.__class__(self.loader, self.filenames[::shard], self.mode, self._post_process, self.n_prefetch)

    def take(self, take: int):
        return self.__class__(self.loader, self.filenames[:take], self.mode, self._post_process, self.n_prefetch)

    def map(self, _post_process: Callable):
        return self.__class__(self.loader, self.filenames, self.mode, self._post_process + [_post_process],
                              self.n_prefetch)

    def serial(self):
        self.n_prefetch = 0
        return self

    def prefetch(self, n_prefetch: int):
        if n_prefetch == tf.data.experimental.AUTOTUNE:
            n_prefetch = 2
        self.n_prefetch = n_prefetch
        return self

    def pprint_example(self):
        for k, v in self.get_example(0).items():
            try:
                print(k, v.shape)
            except AttributeError:
                print(k, type(v))


class NewBaseDatasetLoader:

    def __init__(self, dataset_dirs: List[pathlib.Path],
                 scenario: Optional[ScenarioWithVisualization] = None):
        self.dataset_dirs = dataset_dirs
        self.hparams = merge_hparams_dicts(dataset_dirs)
        self.scenario = scenario
        self.batch_metadata = {}

        self.pool = get_context("spawn").Pool()
        print(f"Created pool with {self.pool._processes} workers")

    def __del__(self):
        self.pool.terminate()

    def post_process(self, e):
        return e

    def get_scenario(self):
        if self.scenario is None:
            self.scenario = get_scenario(self.hparams['scenario'])

        return self.scenario

    def get_datasets(self, mode: str, shuffle: Optional[int] = 0, take: int = None):
        filenames = get_filenames(self.dataset_dirs, mode)
        assert len(filenames) > 0
        dataset = NewBaseDataset(loader=self, filenames=filenames, mode=mode)
        if shuffle:
            dataset = dataset.shuffle(seed=shuffle)
        if take:
            dataset = dataset.take(take)
        return dataset

    def pprint_example(self):
        dataset = self.get_datasets(mode='val', take=1)
        example = dataset.get_example(0)
        pprint_example(example)
