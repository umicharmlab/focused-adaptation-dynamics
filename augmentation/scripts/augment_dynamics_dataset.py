#!/usr/bin/env python
import argparse
import logging
import pathlib
from time import time

import numpy as np
import tensorflow as tf
from colorama import Fore

from arc_utilities import ros_init
from arc_utilities.algorithms import nested_dict_update
from augmentation.augment_dataset import augment_dynamics_dataset
from moonshine.filepath_tools import load_hjson
from moonshine.gpu_config import limit_gpu_mem

limit_gpu_mem(None)


@ros_init.with_ros("augment_dynamics_dataset")
def main():
    np.set_printoptions(suppress=True, precision=4)

    tf.get_logger().setLevel(logging.FATAL)
    parser = argparse.ArgumentParser()
    parser.add_argument('dataset_dir', type=pathlib.Path, help='dataset directory')
    parser.add_argument('--n-augmentations', type=int, default=25)
    parser.add_argument('--batch-size', type=int, default=32)
    parser.add_argument('--hparams', type=pathlib.Path, default=pathlib.Path("aug_hparams/cylinders.hjson"))
    parser.add_argument('--visualize', action='store_true')

    args = parser.parse_args()

    suffix = f"aug-{args.n_augmentations}-{int(time())}"
    dataset_dir = args.dataset_dir

    outdir = dataset_dir.parent / f"{dataset_dir.name}+{suffix}"

    common_hparams = load_hjson(pathlib.Path("aug_hparams/common.hjson"))
    hparams = load_hjson(args.hparams)
    hparams = nested_dict_update(common_hparams, hparams)

    outdir = augment_dynamics_dataset(dataset_dir=dataset_dir,
                                      hparams=hparams,
                                      outdir=outdir,
                                      n_augmentations=args.n_augmentations,
                                      visualize=args.visualize,
                                      batch_size=args.batch_size)

    print(Fore.CYAN + outdir.as_posix() + Fore.RESET)


if __name__ == '__main__':
    main()