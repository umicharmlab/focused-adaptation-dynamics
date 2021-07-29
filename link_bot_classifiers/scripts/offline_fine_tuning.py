#!/usr/bin/env python
import argparse
import logging
import pathlib
import time

import numpy as np
import tensorflow as tf

from arc_utilities import ros_init
from link_bot_classifiers.fine_tune_classifier import fine_tune_classifier
from link_bot_pycommon.args import int_tuple_arg
from moonshine.filepath_tools import load_hjson


@ros_init.with_ros(f"offline_fine_tuning_{int(time.time())}")
def main():
    tf.get_logger().setLevel(logging.ERROR)

    np.set_printoptions(linewidth=250, precision=4, suppress=True)

    parser = argparse.ArgumentParser()
    parser.add_argument('log')
    parser.add_argument('fb', type=int)
    parser.add_argument('seed', type=int)
    parser.add_argument('--batch-size', type=int, default=24)
    parser.add_argument('--debug', action='store_true')
    parser.add_argument('--epochs', type=int, default=10)
    parser.add_argument('--profile', type=int_tuple_arg, default=None)
    parser.add_argument('--take', type=int)
    parser.add_argument('--skip', type=int)
    parser.add_argument('--save-inputs', action='store_true')
    parser.add_argument('--val-take', type=int)
    parser.add_argument('--threshold', type=float, default=None)

    args = parser.parse_args()

    dataset_dirs = [pathlib.Path("/media/shared/classifier_data/val_floating_boxes_1622170084+fix-op/")]
    val_dataset_dirs = [pathlib.Path("/media/shared/classifier_data/val_car_feasible_1614981888+op2/")]

    params = pathlib.Path("hparams/aug.hjson")
    pretransfer_config_dir = pathlib.Path("/media/shared/pretransfer_initial_configs/car")
    model_hparams_update = load_hjson(params)

    if args.debug:
        validate_first = False
    else:
        validate_first = True

    def _get_param(name, default):
        if model_hparams_update is None:
            return default
        elif name not in model_hparams_update:
            return default
        else:
            return model_hparams_update[name]

    fine_tune_conv = _get_param('fine_tune_conv', False)
    fine_tune_lstm = _get_param('fine_tune_lstm', False)
    fine_tune_dense = _get_param('fine_tune_dense', False)
    fine_tune_output = _get_param('fine_tune_output', True)
    learning_rate = _get_param('learning_rate', 1e-4)

    checkpoint_dir = pathlib.Path(f"/media/shared/cl_trials/val_floating_boxes{args.fb}")
    checkpoint = list(checkpoint_dir.iterdir())[-1] / 'best_checkpoint'

    fine_tune_classifier(train_dataset_dirs=dataset_dirs,
                         val_dataset_dirs=val_dataset_dirs,
                         checkpoint=checkpoint,
                         log=f"{args.log}_fb2car_offline_aug-{args.fb}-{args.seed}",
                         batch_size=args.batch_size,
                         early_stopping=True,
                         epochs=args.epochs,
                         validate_first=validate_first,
                         take=args.take,
                         skip=args.skip,
                         seed=args.seed,
                         model_hparams_update=model_hparams_update,
                         val_every_n_batches=None,
                         mid_epoch_val_batches=None,
                         learning_rate=learning_rate,
                         fine_tune_conv=fine_tune_conv,
                         fine_tune_lstm=fine_tune_lstm,
                         fine_tune_dense=fine_tune_dense,
                         fine_tune_output=fine_tune_output,
                         augmentation_config_dir=pretransfer_config_dir,
                         profile=args.profile,
                         val_take=args.val_take,
                         save_inputs=args.save_inputs,
                         )


if __name__ == '__main__':
    main()