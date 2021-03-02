#!/usr/bin/env python
import argparse
import logging
import pathlib
from typing import Dict

import colorama
import numpy as np
import tensorflow as tf
from progressbar import progressbar

import rospy
from arc_utilities import ros_init
from link_bot_classifiers import classifier_utils
from link_bot_classifiers.points_collision_checker import PointsCollisionChecker
from link_bot_data import base_dataset
from link_bot_data.classifier_dataset import ClassifierDatasetLoader
from link_bot_data.dataset_utils import batch_tf_dataset, remove_predicted
from link_bot_data.visualization import init_viz_env
from link_bot_planning.results_utils import print_percentage
from link_bot_pycommon.experiment_scenario import ExperimentScenario
from link_bot_pycommon.scenario_with_visualization import ScenarioWithVisualization
from link_bot_pycommon.serialization import dump_gzipped_pickle
from merrrt_visualization.rviz_animation_controller import RvizAnimation
from moonshine import filepath_tools
from moonshine.indexing import index_dict_of_batched_tensors_tf
from std_msgs.msg import Float32


@ros_init.with_ros("test_for_fp")
def main():
    colorama.init(autoreset=True)
    tf.get_logger().setLevel(logging.ERROR)

    np.set_printoptions(linewidth=250, precision=4, suppress=True)
    parser = argparse.ArgumentParser()

    parser.add_argument('dataset_dirs', type=pathlib.Path, nargs='+')
    parser.add_argument('checkpoint', type=pathlib.Path)
    parser.add_argument('--mode', type=str, choices=['train', 'test', 'val', 'all'], default='val')
    parser.add_argument('--batch-size', type=int, default=8)
    parser.add_argument('--verbose', '-v', action='count', default=0)
    parser.add_argument('--use-gt-rope', action='store_true')
    parser.add_argument('--only-fp', action='store_true')
    parser.add_argument('--only-tn', action='store_true')
    parser.add_argument('--only-predicted-not-in-collision', action='store_true')
    parser.add_argument('--only-predicted-in-collision', action='store_true')
    parser.add_argument('--threshold', type=float, default=None)
    parser.add_argument('--no-viz', action='store_true')
    parser.add_argument('--start-at', type=int, default=0)

    args = parser.parse_args()

    traj_idx_pub_ = rospy.Publisher("traj_idx_viz", Float32, queue_size=10)

    ###############
    # Model
    ###############
    trials_directory = pathlib.Path('trials').absolute()
    trial_path = args.checkpoint.parent.absolute()
    _, params = filepath_tools.create_or_load_trial(trial_path=trial_path,
                                                    trials_directory=trials_directory)

    ###############
    # Dataset
    ###############
    dataset = ClassifierDatasetLoader(args.dataset_dirs,
                                      load_true_states=True,
                                      use_gt_rope=args.use_gt_rope,
                                      threshold=args.threshold)
    tf_dataset = dataset.get_datasets(mode=args.mode)
    scenario = dataset.scenario

    ###############
    # Evaluate
    ###############
    tf_dataset = batch_tf_dataset(tf_dataset, args.batch_size, drop_remainder=True)

    model = classifier_utils.load_generic_model(args.checkpoint)

    cc = PointsCollisionChecker(pathlib.Path('trials/cc_baseline/none'), scenario)

    fp = 0
    fn = 0
    predicted_in_collision = 0
    predicted_not_in_collision = 0
    predicted_in_collision_labeled_0 = 0
    predicted_in_collision_labeled_1 = 0
    predicted_in_collision_fp = 0
    predicted_in_collision_fn = 0
    count = 0
    # for batch_idx, example in enumerate(tf_dataset):
    for batch_idx, example in enumerate(progressbar(tf_dataset, widgets=base_dataset.widgets)):

        if batch_idx < args.start_at:
            continue

        example.update(dataset.batch_metadata)
        predictions, _ = model.check_constraint_from_example(example, training=False)

        is_close = tf.expand_dims(example['is_close'][:, 1:], axis=2)

        probabilities = predictions['probabilities']

        # Visualization
        example.pop("time")
        example.pop("batch_size")
        decisions = tf.squeeze(probabilities > 0.5, axis=-1)
        is_close = tf.squeeze(tf.cast(is_close, tf.bool), axis=-1)
        classifier_is_correct = tf.equal(decisions, is_close)
        is_tn = tf.logical_and(tf.logical_not(is_close), tf.logical_not(decisions))
        is_fp = tf.logical_and(tf.logical_not(is_close), decisions)
        is_fn = tf.logical_and(is_close, tf.logical_not(decisions))
        for b in range(args.batch_size):
            example_b = index_dict_of_batched_tensors_tf(example, b)

            example_b_pred = {}
            example_b_pred.update(example_b)
            example_b_pred.update({remove_predicted(k): example_b[k] for k in dataset.predicted_state_keys})

            rope_points_not_in_collision, _ = cc.check_constraint_from_example(example_b_pred)
            rope_points_not_in_collision = rope_points_not_in_collision[0]
            rope_points_in_collision = not rope_points_not_in_collision

            if args.only_predicted_not_in_collision:
                if not rope_points_not_in_collision:
                    continue
            if args.only_predicted_in_collision:
                if rope_points_not_in_collision:
                    continue

            if args.only_fp:
                if not tf.reduce_all(is_fp[b]):
                    continue

            if args.only_tn:
                if not tf.reduce_all(is_tn[b]):
                    continue

            count += 1
            if rope_points_not_in_collision:
                predicted_not_in_collision += 1
            if rope_points_in_collision:
                predicted_in_collision += 1
                if is_close[b, 0]:
                    predicted_in_collision_labeled_1 += 1
                else:
                    predicted_in_collision_labeled_0 += 1
                if tf.reduce_all(is_fp[b]):
                    predicted_in_collision_fp += 1
                if tf.reduce_all(is_fn[b]):
                    predicted_in_collision_fn += 1
            if tf.reduce_all(is_fp[b]):
                fp += 1
            if tf.reduce_all(is_fn[b]):
                fn += 1

            if not args.no_viz:
                def _custom_viz_t(scenario: ScenarioWithVisualization, e: Dict, t: int):
                    if t > 0:
                        accept_probability_t = predictions['probabilities'][b, t - 1, 0].numpy()
                    else:
                        accept_probability_t = -999
                    scenario.plot_accept_probability(accept_probability_t)

                    traj_idx_msg = Float32()
                    traj_idx_msg.data = batch_idx * args.batch_size + b
                    traj_idx_pub_.publish(traj_idx_msg)

                anim = RvizAnimation(scenario=scenario,
                                     n_time_steps=dataset.horizon,
                                     init_funcs=[init_viz_env,
                                                 dataset.init_viz_action(),
                                                 ],
                                     t_funcs=[_custom_viz_t,
                                              dataset.classifier_transition_viz_t(),
                                              ExperimentScenario.plot_stdev_t,
                                              init_viz_env,
                                              ])

                dump_gzipped_pickle(example_b, pathlib.Path('debugging.pkl.gzip'))
                anim.play(example_b)

    print_percentage('% predicted state is in collision',
                     predicted_in_collision, count)
    print_percentage('% predicted state is in collision and the label is 0 ',
                     predicted_in_collision_labeled_0, predicted_in_collision)
    print_percentage('% false positives and predicted state is in collision and labeled 0',
                     predicted_in_collision_fp, predicted_in_collision_labeled_0)
    print_percentage('% false negatives and predicted state is in collision but labeled 1 ',
                     predicted_in_collision_fn, predicted_in_collision_labeled_1)


if __name__ == '__main__':
    main()
