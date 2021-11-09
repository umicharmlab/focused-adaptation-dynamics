import re
from copy import deepcopy
from typing import Dict

import numpy as np
import tensorflow as tf
import tensorflow_probability as tfp
import torch
from pyjacobian_follower import IkParams

from dm_envs import primitive_hand
from dm_envs.cylinders_task import PlanarPushingCylindersTask
from dm_envs.planar_pushing_scenario import PlanarPushingScenario, ACTION_Z
from dm_envs.planar_pushing_task import ARM_HAND_NAME
from link_bot_data.color_from_kwargs import color_from_kwargs
from link_bot_data.rviz_arrow import rviz_arrow
from link_bot_pycommon.debugging_utils import debug_viz_batch_indices
from link_bot_pycommon.marker_index_generator import marker_index_generator
from moonshine.geometry import transform_points_3d, xyzrpy_to_matrices, transformation_jacobian
from moonshine.moonshine_utils import repeat_tensor
from std_msgs.msg import ColorRGBA
from visualization_msgs.msg import MarkerArray, Marker

DEBUG_VIZ_STATE_AUG = True


def pos_to_vel(pos):
    vel = pos[1:] - pos[:-1]
    vel = np.pad(vel, [[0, 1], [0, 0], [0, 0]], mode='edge')
    return vel


def squeeze_and_get_xy(p):
    return torch.squeeze(p, 2)[:, :, :2]


def cylinders_to_points(positions, res, radius, height):
    """

    Args:
        positions:  [b, m, T, 3]
        res:  [b, T]
        radius:  [b, T]
        height:  [b, T]

    Returns: [b, m, T, n_POINTS, 3]

    """
    m = positions.shape[1]  # m is the number of objects
    sized_points = size_to_points(radius, height, res)  # [b, T, n_points, 3]
    num_points = sized_points.shape[-2]
    sized_points = repeat_tensor(sized_points, m, axis=1, new_axis=True)  # [b, m, T, n_points, 3]
    ones = tf.ones(positions.shape[:-1] + [1])
    positions_homo = tf.expand_dims(tf.concat([positions, ones], axis=-1), -1)  # [b, m, T, 4, 1]
    rot_homo = tf.concat([tf.eye(3), tf.zeros([1, 3])], axis=0)
    rot_homo = repeat_tensor(rot_homo, positions.shape[0], 0, True)
    rot_homo = repeat_tensor(rot_homo, positions.shape[1], 1, True)
    rot_homo = repeat_tensor(rot_homo, positions.shape[2], 2, True)
    transform_matrix = tf.concat([rot_homo, positions_homo], axis=-1)  # [b, m, T, 4, 4]
    transform_matrix = repeat_tensor(transform_matrix, num_points, 3, True)
    obj_points = transform_points_3d(transform_matrix, sized_points)  # [b, m, T, num_points, 3]
    return obj_points


def make_odd(x):
    return tf.where(tf.cast(x % 2, tf.bool), x, x + 1)


NUM_POINTS = 512
cylinder_points_rng = np.random.RandomState(0)


def size_to_points(radius, height, res):
    """

    Args:
        radius: [b, T]
        height: [b, T]
        res: [b, T]

    Returns: [b, T, n_points, 3]

    """
    batch_size, time = radius.shape
    res = res[0, 0]
    radius = radius[0, 0]
    height = height[0, 0]

    n_side = make_odd(tf.cast(2 * radius / res, tf.int64))
    n_height = make_odd(tf.cast(height / res, tf.int64))
    p = tf.linspace(-radius, radius, n_side)
    grid_points = tf.stack(tf.meshgrid(p, p), -1)  # [n_side, n_side, 2]
    in_circle = tf.linalg.norm(grid_points, axis=-1) <= radius
    in_circle_indices = tf.where(in_circle)
    points_in_circle = tf.gather_nd(grid_points, in_circle_indices)
    points_in_circle_w_height = repeat_tensor(points_in_circle, n_height, 0, True)
    z = tf.linspace(0., height, n_height) - height / 2
    z = repeat_tensor(z, points_in_circle_w_height.shape[1], 1, True)[..., None]
    points = tf.concat([points_in_circle_w_height, z], axis=-1)
    points = tf.reshape(points, [-1, 3])

    sampled_points_indices = cylinder_points_rng.randint(0, points.shape[0], NUM_POINTS)
    sampled_points = tf.gather(points, sampled_points_indices, axis=0)

    points_batch = repeat_tensor(sampled_points, batch_size, 0, True)
    points_batch_time = repeat_tensor(points_batch, time, 1, True)
    return points_batch_time


def get_k_with_stats(batch, k):
    v = batch[f"{k}"]
    v_mean = batch[f"{k}/mean"]
    v_std = batch[f"{k}/std"]
    return v, v_mean, v_std


def make_cylinder_marker(color_msg, height, idx, ns, position, radius):
    marker = Marker(ns=ns, action=Marker.ADD, type=Marker.CYLINDER, id=idx, color=color_msg)
    marker.header.frame_id = 'world'
    marker.pose.position.x = position[0, 0]
    marker.pose.position.y = position[0, 1]
    marker.pose.position.z = position[0, 2]
    marker.pose.orientation.w = 1
    marker.scale.x = radius * 2
    marker.scale.y = radius * 2
    marker.scale.z = height

    return marker


def make_vel_arrow(position, velocity, height, color_msg, idx, ns, vel_scale=1.0):
    start = position[0] + np.array([0, 0, height / 2 + 0.0005])
    end = start + velocity[0] * np.array([vel_scale, vel_scale, 1])
    vel_color_factor = 0.4
    vel_color = ColorRGBA(color_msg.r * vel_color_factor,
                          color_msg.g * vel_color_factor,
                          color_msg.b * vel_color_factor,
                          color_msg.a)
    vel_marker = rviz_arrow(start, end,
                            label=ns + 'vel',
                            color=vel_color,
                            idx=idx)
    return vel_marker


class CylindersScenario(PlanarPushingScenario):

    def plot_state_rviz(self, state: Dict, **kwargs):
        super().plot_state_rviz(state, **kwargs)

        ns = kwargs.get("label", "")
        idx = kwargs.get("idx", 0)
        color_msg = color_from_kwargs(kwargs, 1.0, 0, 0.0)

        num_objs = state['num_objs'][0]
        height = state['height'][0]
        radius = state['radius'][0]
        msg = MarkerArray()

        ig = marker_index_generator(idx)

        robot_position_k = f'{ARM_HAND_NAME}/tcp_pos'
        if robot_position_k in state:
            robot_position = state[robot_position_k]
            robot_position[0, 2] = primitive_hand.HALF_HEIGHT + ACTION_Z
            robot_color_msg = deepcopy(color_msg)
            robot_color_msg.b = 1 - robot_color_msg.b
            marker = make_cylinder_marker(robot_color_msg, primitive_hand.HEIGHT, next(ig), ns + '_robot',
                                          robot_position,
                                          radius)
            msg.markers.append(marker)

        robot_vel_k = f'{ARM_HAND_NAME}/tcp_vel'
        if robot_vel_k in state:
            robot_velocity = state[robot_vel_k]
            vel_marker = make_vel_arrow(robot_position, robot_velocity, primitive_hand.HEIGHT + 0.005, color_msg,
                                        next(ig), ns + '_robot')
            msg.markers.append(vel_marker)

        for i in range(num_objs):
            obj_position = state[f'obj{i}/position']
            obj_marker = make_cylinder_marker(color_msg, height, next(ig), ns, obj_position, radius)
            msg.markers.append(obj_marker)

            obj_vel_k = f'obj{i}/linear_velocity'
            if obj_vel_k in state:
                obj_velocity = state[obj_vel_k]
                obj_vel_marker = make_vel_arrow(obj_position, obj_velocity, height, color_msg, next(ig), ns)
                msg.markers.append(obj_vel_marker)

        self.state_viz_pub.publish(msg)

    def compute_obj_points(self, inputs: Dict, num_object_interp: int, batch_size: int):
        """

        Args:
            inputs: contains the poses and size of the blocks, over a whole trajectory, which we convert into points
            num_object_interp:
            batch_size:

        Returns: [b, m_objects, T, n_points, 3]

        """
        height = inputs['height'][:, :, 0]  # [b, T]
        radius = inputs['radius'][:, :, 0]  # [b, T]
        num_objs = inputs['num_objs'][0, 0, 0]  # assumed fixed across batch/time
        positions = []  # [b, m, T, 3]
        for i in range(num_objs):
            pos = inputs[f"obj{i}/position"][:, :, 0]  # [b, T, 3]
            positions.append(pos)
        positions = tf.stack(positions, axis=1)
        time = positions.shape[2]

        res = repeat_tensor(inputs['res'], time, 1, True)  # [b]

        obj_points = cylinders_to_points(positions, res=res, radius=radius, height=height)
        robot_radius = repeat_tensor(primitive_hand.RADIUS, batch_size, 0, True)
        robot_radius = repeat_tensor(robot_radius, time, 1, True)
        robot_height = repeat_tensor(primitive_hand.HEIGHT, batch_size, 0, True)
        robot_height = repeat_tensor(robot_height, time, 1, True)
        robot_positions = tf.reshape(inputs[f'{ARM_HAND_NAME}/tcp_pos'], [batch_size, 1, time, 3])
        robot_points = cylinders_to_points(robot_positions, res=res, radius=robot_radius, height=robot_height)

        obj_points = tf.concat([robot_points, obj_points], axis=1)

        return obj_points

    def apply_object_augmentation_no_ik(self,
                                        m,
                                        to_local_frame,
                                        inputs: Dict,
                                        batch_size,
                                        time,
                                        h: int,
                                        w: int,
                                        c: int,
                                        ):
        """

        Args:
            m: [b, k, 4, 4]
            to_local_frame: [b, 3]
            inputs:
            batch_size:
            time:
            h: local env h
            w: local env w
            c: local env c

        Returns:

        """
        to_local_frame_expanded = to_local_frame[:, None, None]
        m_expanded = m[:, None]

        def _transform(m, points, _to_local_frame):
            points_local_frame = points - _to_local_frame
            points_local_frame_aug = transform_points_3d(m, points_local_frame)
            return points_local_frame_aug + _to_local_frame

        # apply transformations to the state
        num_objs = inputs['num_objs'][0, 0, 0]
        object_aug_update = {
            'num_objs': inputs['num_objs'],
            'height':   inputs['height'],
            'radius':   inputs['radius'],
        }
        for obj_idx in range(num_objs):
            pos_k = f'obj{obj_idx}/position'
            vel_k = f"obj{obj_idx}/linear_velocity"
            obj_pos = inputs[pos_k]
            obj_vel = inputs[vel_k]

            obj_pos_aug = _transform(m_expanded, obj_pos, to_local_frame_expanded)
            obj_vel_aug = _transform(m_expanded, obj_vel, to_local_frame_expanded)

            object_aug_update[pos_k] = obj_pos_aug
            object_aug_update[vel_k] = obj_vel_aug

        # apply transformations to the action
        gripper_position = inputs['gripper_position']
        gripper_position_aug = _transform(m_expanded, gripper_position, to_local_frame_expanded)
        object_aug_update['gripper_position'] = gripper_position_aug

        if DEBUG_VIZ_STATE_AUG:
            for b in debug_viz_batch_indices(batch_size):
                env_b = {
                    'env':          inputs['env'][b],
                    'res':          inputs['res'][b],
                    'extent':       inputs['extent'][b],
                    'origin_point': inputs['origin_point'][b],
                }
                object_aug_update_b = {k: v[b] for k, v in object_aug_update.items()}

                self.plot_environment_rviz(env_b)
                for t in range(time):
                    object_aug_update_b_t = {k: v[0] for k, v in object_aug_update_b.items()}
                    self.plot_state_rviz(object_aug_update_b_t, label='aug_no_ik', color='white', id=t)
        return object_aug_update, None, None

    def compute_collision_free_point_ik(self,
                                        default_robot_state,
                                        points,
                                        group_name,
                                        tip_names,
                                        scene_msg,
                                        ik_params):
        return self.robot.j.compute_collision_free_point_ik(default_robot_state,
                                                            points,
                                                            group_name,
                                                            tip_names,
                                                            scene_msg,
                                                            ik_params)

    def compute_collision_free_point_ik(self,
                                        default_robot_state,
                                        points,
                                        group_name,
                                        tip_names,
                                        scene_msg,
                                        ik_params):
        raise NotImplementedError()

    def aug_ik(self,
               inputs_aug: Dict,
               default_robot_positions,
               ik_params: IkParams,
               batch_size: int):
        """

        Args:
            inputs_aug: a dict containing the desired gripper positions as well as the scene_msg and other state info
            default_robot_positions: default robot joint state to seed IK
            batch_size:

        Returns:

        """
        raise NotImplementedError()

    @staticmethod
    def is_points_key(k):
        return any([
            re.match('obj.*position', k),
            k == f'{ARM_HAND_NAME}/tcp_pos',
        ])

    def make_dm_task(self, params):
        return PlanarPushingCylindersTask(params)

    def __repr__(self):
        return "cylinders"

    def propnet_obj_v(self, batch, batch_size, obj_idx, time, device):
        obj_attr = torch.zeros([batch_size, 1], device=device)

        obj_pos_k = f"obj{obj_idx}/position"
        obj_pos = batch[obj_pos_k]  # [b, T, 2]
        obj_pos = squeeze_and_get_xy(obj_pos)

        obj_vel_k = f"obj{obj_idx}/linear_velocity"
        obj_vel = batch[obj_vel_k]  # [b, T, 2]
        obj_vel = squeeze_and_get_xy(obj_vel)

        obj_state = torch.cat([obj_pos, obj_vel], dim=-1)  # [b, T, 4]

        obj_action = torch.zeros([batch_size, time - 1, 3], device=device)

        return obj_attr, obj_state, obj_action

    def propnet_robot_v(self, batch, batch_size, device):
        robot_attr = torch.ones([batch_size, 1], device=device)

        robot_pos_k = f"{ARM_HAND_NAME}/tcp_pos"
        robot_pos = batch[robot_pos_k]
        robot_pos = squeeze_and_get_xy(robot_pos)

        robot_vel_k = f"{ARM_HAND_NAME}/tcp_vel"
        robot_vel = batch[robot_vel_k]
        robot_vel = squeeze_and_get_xy(robot_vel)

        robot_state = torch.cat([robot_pos, robot_vel], dim=-1)  # [b, T, 4]

        robot_action_k = 'gripper_position'
        robot_action = batch[robot_action_k]

        return robot_attr, robot_state, robot_action

    def propnet_add_vel(self, example: Dict):
        num_objs = example['num_objs'][0, 0]  # assumed fixed across time
        robot_pos = example[f'{ARM_HAND_NAME}/tcp_pos']
        robot_vel = pos_to_vel(robot_pos)
        robot_vel_k = f"{ARM_HAND_NAME}/tcp_vel"
        vel_state_keys = [robot_vel_k]
        example[robot_vel_k] = robot_vel
        for obj_idx in range(num_objs):
            obj_pos = example[f'obj{obj_idx}/position']
            obj_vel = pos_to_vel(obj_pos)
            obj_vel_k = f"obj{obj_idx}/linear_velocity"
            example[obj_vel_k] = obj_vel
            vel_state_keys.append(obj_vel_k)
        return example, vel_state_keys

    def propnet_outputs_to_state(self, inputs, pred_vel, pred_pos, b, t):
        pred_state_t = {}
        height_b_t = inputs['height'][b, t]
        pred_state_t['height'] = height_b_t
        pred_state_t['radius'] = inputs['radius'][b, t]
        num_objs = inputs['num_objs'][b, t, 0]
        pred_state_t['num_objs'] = [num_objs]

        pred_robot_pos_b_t_2d = pred_pos[b, t, 0]
        default_robot_z = torch.zeros(1) * 0.01  # we've lost this info so just put something that will visualize ok
        pred_robot_pos_b_t_3d = torch.cat([pred_robot_pos_b_t_2d, default_robot_z])
        pred_robot_vel_b_t_2d = pred_vel[b, t, 0]
        pred_robot_vel_b_t_3d = torch.cat([pred_robot_vel_b_t_2d, torch.zeros(1)])
        pred_state_t[f'{ARM_HAND_NAME}/tcp_pos'] = torch.unsqueeze(pred_robot_pos_b_t_3d, 0).detach()
        pred_state_t[f'{ARM_HAND_NAME}/tcp_vel'] = torch.unsqueeze(pred_robot_vel_b_t_3d, 0).detach()

        for j in range(num_objs):
            pred_pos_b_t_2d = pred_pos[b, t, j + 1]
            pred_pos_b_t_3d = torch.cat([pred_pos_b_t_2d, height_b_t / 2])
            pred_vel_b_t_2d = pred_vel[b, t, j + 1]
            pred_vel_b_t_3d = torch.cat([pred_vel_b_t_2d, torch.zeros(1)])
            pred_state_t[f'obj{j}/position'] = torch.unsqueeze(pred_pos_b_t_3d, 0).detach()
            pred_state_t[f'obj{j}/linear_velocity'] = torch.unsqueeze(pred_vel_b_t_3d, 0).detach()

        return pred_state_t

    def propnet_rel(self, obj_pos, num_objects, relation_dim, threshold=0.05, device=None):
        """

        Args:
            num_objects: number of objects/particles, $|O|$
            relation_dim: dimension of the relation vector
            threshold: in meters
            device:

        Returns:
            Rr: [num_objects, num_relations], binary, 1 at [obj_i,rel_j] means object i is the receiver in relation j
            Rs: [num_objects, num_relations], binary, 1 at [obj_i,rel_j] means object i is the sender in relation j
            Ra: [num_relations, attr_dim] containing the relation attributes

        """
        # we assume the robot is _first_ in the list of objects
        # the robot is included as an object here
        batch_size = obj_pos.shape[0]
        n_rel = num_objects * (num_objects - 1)

        Rs = torch.zeros(batch_size, num_objects, n_rel, device=device)
        Rr = torch.zeros(batch_size, num_objects, n_rel, device=device)
        Ra = torch.ones(batch_size, n_rel, relation_dim, device=device)  # relation attributes information

        rel_idx = 0
        for sender_idx, receiver_idx in np.ndindex(num_objects, num_objects):
            if sender_idx == receiver_idx:
                continue

            distance = (obj_pos[:, sender_idx] - obj_pos[:, receiver_idx]).square().sum()
            is_close = (distance < threshold ** 2).float()

            Rs[:, sender_idx, rel_idx] = 1
            Rr[:, receiver_idx, rel_idx] = 1

            rel_idx += 1

        return Rs, Rr, Ra

    def initial_identity_aug_params(self, batch_size, k_transforms):
        return tf.zeros([batch_size, k_transforms, 2], tf.float32)  # delta x, delta y

    def sample_target_aug_params(self, seed, aug_params, n_samples):
        trans_lim = tf.ones([2]) * aug_params['target_trans_lim']
        trans_distribution = tfp.distributions.Uniform(low=-trans_lim, high=trans_lim)

        trans_target = trans_distribution.sample(sample_shape=n_samples, seed=seed())
        return trans_target

    def plot_transform(self, obj_i, transform_params, frame_id):
        """

        Args:
            frame_id:
            transform_params: [x,y]

        Returns:

        """
        target_pos_b = [transform_params[0], transform_params[1], 0]
        self.tf.send_transform(target_pos_b, [0, 0, 0, 1], f'aug_opt_initial_{obj_i}', frame_id, False)

    def aug_target_pos(self, target):
        return tf.concat([target[0], target[1], 0], axis=0)

    def transformation_params_to_matrices(self, obj_transforms):
        zrpy = tf.zeros(obj_transforms.shape[:-1] + [4])
        xyzrpy = tf.concat([obj_transforms, zrpy], axis=-1)
        return xyzrpy_to_matrices(xyzrpy)

    def aug_transformation_jacobian(self, obj_transforms):
        zrpy = tf.zeros(obj_transforms.shape[:-1] + [4])
        xyzrpy = tf.concat([obj_transforms, zrpy], axis=-1)
        jacobian = transformation_jacobian(xyzrpy)
        jacobian_xy = jacobian[..., 0:2, :, :]
        return jacobian_xy

    def aug_distance(self, transforms1, transforms2):
        trans1 = transforms1[..., :2]
        trans2 = transforms2[..., :2]
        trans_dist = tf.linalg.norm(trans1 - trans2, axis=-1)
        max_distance = tf.reduce_max(trans_dist)
        return max_distance
