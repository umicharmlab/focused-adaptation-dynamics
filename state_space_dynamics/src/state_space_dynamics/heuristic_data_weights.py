from typing import Dict

import numpy as np
import tensorflow as tf

from link_bot_pycommon.collision_checking import batch_in_collision_tf_3d
from link_bot_pycommon.grid_utils_np import extent_to_env_shape
from moonshine.raster_3d_tf import points_to_voxel_grid_res_origin_point_batched
from moonshine.robot_points_tf import batch_robot_state_to_transforms, batch_transform_robot_points


def check_in_collision(environment, points, inflation):
    in_collision, inflated_env = batch_in_collision_tf_3d(environment=environment,
                                                          points=points,
                                                          inflate_radius_m=inflation)
    in_collision = in_collision.numpy()
    return in_collision


def make_robot_voxelgrid(scenario, example, origin_point, robot_info):
    # batch here means time
    batch_size = example['time_idx'].shape[0]
    h, w, c = extent_to_env_shape(example['extent'], robot_info.res)
    joint_names = example['joint_names']
    joint_positions = example['joint_positions']
    link_to_robot_transforms = batch_robot_state_to_transforms(scenario.robot.jacobian_follower,
                                                               joint_names,
                                                               joint_positions,
                                                               robot_info.link_names)
    robot_points = batch_transform_robot_points(link_to_robot_transforms, robot_info, batch_size)
    n_robot_points = robot_points.shape[1]
    flat_batch_indices = tf.repeat(tf.range(batch_size, dtype=tf.int64), n_robot_points, axis=0)
    flat_points = tf.reshape(robot_points, [-1, 3])
    flat_points.set_shape([n_robot_points * batch_size, 3])
    flat_res = tf.repeat(robot_info.res, n_robot_points * batch_size, axis=0)
    flat_origin_point = tf.repeat(tf.expand_dims(origin_point, 0), n_robot_points * batch_size, axis=0)
    robot_voxelgrid = points_to_voxel_grid_res_origin_point_batched(flat_batch_indices,
                                                                    flat_points,
                                                                    flat_res,
                                                                    flat_origin_point,
                                                                    h,
                                                                    w,
                                                                    c,
                                                                    batch_size)
    return robot_voxelgrid


def heuristic_weight_func(scenario, example: Dict, hparams, robot_info):
    points = scenario.state_to_points_for_cc(example)
    env_inflation = float(tf.squeeze(example['res'])) * hparams['env_inflation']
    in_collision = check_in_collision(example, points, env_inflation)

    if hparams['check_length']:
        d = tf.linalg.norm(example['right_gripper'] - example['left_gripper'], axis=-1)
        too_far = d > 0.55  # copied from floating_rope.hjson data collection params, max_distance_between_grippers

        rope_points = example['rope'].reshape([10, 25, 3])
        rope_length = np.sum(np.linalg.norm(rope_points[:, :-1] - rope_points[:, 1:], axis=-1), axis=-1)
        too_long = rope_length > hparams['max_rope_length']

    if hparams['check_robot']:
        robot_voxel_grid = make_robot_voxelgrid(scenario, example, example['origin_point'], robot_info)
        time = example['time_idx'].shape[0]
        robot_in_collision = []
        for t in range(time):
            robot_as_env_t = {
                'env':          robot_voxel_grid[t],
                'res':          robot_info.res,
                'origin_point': example['origin_point'],
            }
            robot_in_collision_t = check_in_collision(robot_as_env_t, points[t],
                                                      robot_info.res * hparams['robot_inflation'])
            robot_in_collision.append(robot_in_collision_t)
            # if robot_in_collision_t:
            #     scenario.plot_environment_rviz(robot_as_env_t)
            #     scenario.plot_points_rviz(tf.reshape(points[t], [-1, 3]).numpy(), label='cc', scale=0.005)

    or_conditions = [in_collision]

    if hparams['check_robot']:
        or_conditions.append(robot_in_collision)

    if hparams['check_length']:
        or_conditions.append(too_far)
        or_conditions.append(too_long)

    weight = 1 - np.logical_or.reduce(or_conditions).astype(np.float32)
    weight_padded = np.concatenate((weight, [1]))
    weight = np.logical_and(weight_padded[:-1], weight_padded[1:]).astype(np.float32)
    example['metadata']['weight'] = weight
    yield example