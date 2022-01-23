#!/usr/bin/env python
import argparse
import logging
import pathlib
import sys
from copy import deepcopy
from time import sleep

import numpy as np
import pyautogui as pyautogui
import tensorflow as tf

from arc_utilities import ros_init
from arc_utilities.algorithms import nested_dict_update
from augmentation.augment_dataset import make_aug_opt
from link_bot_data.dataset_utils import add_predicted
from link_bot_data.load_dataset import guess_dataset_loader
from link_bot_data.visualization import classifier_transition_viz_t
from link_bot_pycommon.get_scenario import get_scenario
from link_bot_pycommon.serialization import my_hdump
from moonshine.filepath_tools import load_hjson
from moonshine.gpu_config import limit_gpu_mem
from moonshine.moonshine_utils import remove_batch
from tf.transformations import quaternion_from_euler

limit_gpu_mem(None)


@ros_init.with_ros("augmentation_anim")
def main():
    tf.get_logger().setLevel(logging.FATAL)
    parser = argparse.ArgumentParser()
    parser.add_argument('dataset_dir', type=pathlib.Path, help='dataset directory')
    parser.add_argument('name')
    parser.add_argument('hparams', type=pathlib.Path)

    args = parser.parse_args()

    dataset_dir = args.dataset_dir
    loader = guess_dataset_loader(dataset_dir)
    scenario = loader.get_scenario()

    figures_info = np.loadtxt(f"{args.name}_figures_info.txt")
    root = pathlib.Path('anims') / args.name

    out_info = {}
    for figure_info_i in figures_info:
        in_idx, aug_seed, tx, ty, tz, r, p, y = figure_info_i

        in_idx = int(in_idx)
        aug_seed = int(aug_seed)

        q = f"ex{in_idx}_aug{aug_seed}"
        outdir = root / f"{dataset_dir.name}_{q}"
        outdir.mkdir(exist_ok=True, parents=True)

        original_filename, output_filename = take_screenshots(outdir, q, loader, scenario, args.hparams, in_idx,
                                                              aug_seed, tx, ty, tz, r, p, y)
        if in_idx not in out_info:
            out_info[in_idx] = {}
            out_info[in_idx]['outputs'] = []
        out_info[in_idx]['original'] = original_filename
        out_info[in_idx]['outputs'].append(output_filename)

    with (root / 'out_info.txt').open("w") as f:
        my_hdump(out_info, f)


def take_screenshots(outdir, q, loader, scenario, hparams_filename, in_idx, aug_seed, tx, ty, tz, r, p, y):
    for _ in range(10):
        scenario.tf.send_transform([tx, ty, tz], quaternion_from_euler(r, p, y), 'robot_root', 'anim_camera')
        sleep(0.1)

    common_hparams = load_hjson(pathlib.Path("aug_hparams/common.hjson"))
    hparams = load_hjson(hparams_filename)
    hparams = nested_dict_update(common_hparams, hparams)
    hparams['augmentation']['seed'] = aug_seed
    debug_state_keys = [add_predicted(k) for k in loader.state_keys]
    with (outdir / 'args.txt').open("w") as f:
        f.write(' '.join(sys.argv))
        f.write('\n')

    def screenshot(filename):
        region = (400, 100, 1000, 900)
        sleep(0.5)
        full_filename = outdir / filename
        full_filename.unlink(missing_ok=True)
        pyautogui.screenshot(full_filename, region=region)
        return full_filename

    def post_init_cb():
        screenshot(f"post_init_{q}.png")

    def post_step_cb(i):
        screenshot(f"post_step_{i}_{q}.png")

    def post_project_cb(i):
        screenshot(f"post_project_{i}_{q}.png")

    aug = make_aug_opt(scenario, loader, hparams, debug_state_keys, 1, post_init_cb, post_step_cb, post_project_cb)

    original = next(iter(loader.get_datasets('all').skip(in_idx).batch(1)))
    original_no_batch = remove_batch(deepcopy(original))

    classifier_viz(original_no_batch, scenario, loader)

    original_img_filename = screenshot(f"original_{q}.png")

    scenario.reset_viz()
    time = original['time_idx'].shape[1]
    output = aug.aug_opt(original, batch_size=1, time=time)
    if not output['is_valid']:
        print("WARNING!!!! NO AUGMENTATION OCCURED")
    output = remove_batch(output)
    aug.delete_state_action_markers()
    classifier_viz(output, scenario, loader)

    output_img_filename = screenshot(f"output_{q}.png")

    return original_img_filename, output_img_filename


def classifier_viz(original_no_batch, scenario, loader):
    scenario.reset_viz()
    for _ in range(3):
        scenario.plot_environment_rviz(original_no_batch)
        viz_f(scenario, original_no_batch, t=0, label='0')

    # # plot the environment, rope at t=0, and rope at t=1
    # viz_f = classifier_transition_viz_t(metadata={},
    #                                     state_metadata_keys=loader.state_metadata_keys,
    #                                     predicted_state_keys=loader.predicted_state_keys,
    #                                     true_state_keys=None)
    # scenario.reset_viz()
    # for _ in range(3):
    #     scenario.plot_environment_rviz(original_no_batch)
    #     viz_f(scenario, original_no_batch, t=0, label='0')
    #     viz_f(scenario, original_no_batch, t=1, label='1')


if __name__ == '__main__':
    main()
