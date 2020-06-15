from typing import Dict, Optional

import matplotlib.patheffects as PathEffects
import matplotlib.pyplot as plt
import numpy as np
import tensorflow as tf

import rospy
from geometry_msgs.msg import Pose
from ignition.markers import MarkerProvider
from link_bot_data.link_bot_dataset_utils import add_planned
from link_bot_data.visualization import plot_arrow, update_arrow
from link_bot_pycommon.experiment_scenario import ExperimentScenario
from link_bot_pycommon.params import CollectDynamicsParams
from link_bot_pycommon.pycommon import quaternion_from_euler
from moonshine.base_learned_dynamics_model import dynamics_loss_function, dynamics_points_metrics_function
from moonshine.moonshine_utils import remove_batch, add_batch
from peter_msgs.srv import Position3DAction, Position3DActionRequest, SetRopeConfiguration, Position3DEnableRequest, \
    SetRopeConfigurationRequest
from std_srvs.srv import Empty


class LinkBotScenario(ExperimentScenario):
    def __init__(self):
        super().__init__()
        object_name = 'link_bot'
        self.set_srv = rospy.ServiceProxy(f"{object_name}/set", Position3DAction)
        self.stop_object_srv = rospy.ServiceProxy(f"{object_name}/stop", Empty)
        self.object_enable_srv = rospy.ServiceProxy(f"{object_name}/enable", Empty)
        self.get_object_srv = rospy.ServiceProxy(f"{object_name}/get", Empty)
        self.set_rope_config_srv = rospy.ServiceProxy("set_rope_config", SetRopeConfiguration)

        # self.movable_object_names = movable_object_names
        # self.movable_object_services = {}
        # for object_name in self.movable_object_names:
        #     self.movable_object_services[object_name] = {
        #         'enable': rospy.ServiceProxy(f'{object_name}/enable', Position3DEnable),
        #         'get_position': rospy.ServiceProxy(f'{object_name}/get', GetPosition3D),
        #         'action': rospy.ServiceProxy(f'{object_name}/set', Position3DAction),
        #         'stop': rospy.ServiceProxy(f'{object_name}/stop', Empty),
        #     }

    def enable_object(self):
        enable_object = Position3DEnableRequest()
        enable_object.enable = True
        self.object_enable_srv(enable_object)

    def execute_action(self, action: Dict):
        req = Position3DActionRequest()
        req.position.x = action['position'][0]
        req.position.y = action['position'][1]
        req.position.z = action['position'][2]
        req.timeout = action['timeout'][0]
        _ = self.set_srv(req)

    def reset_rope(self, x, y, yaw, joint_angles):
        gripper_pose = Pose()
        gripper_pose.position.x = x
        gripper_pose.position.y = y
        q = quaternion_from_euler(0, 0, yaw)
        gripper_pose.orientation.x = q[0]
        gripper_pose.orientation.y = q[1]
        gripper_pose.orientation.z = q[2]
        gripper_pose.orientation.w = q[3]
        req = SetRopeConfigurationRequest()
        req.gripper_poses.append(gripper_pose)
        req.joint_angles.extend(joint_angles)
        self.set_rope_config_srv(req)

    def nudge(self):
        self.execute_action({
            'position': [np.random.randn(), np.random.randn(), 0],
            'timeout': [0.5],
        })

    @staticmethod
    def random_pos(action_rng: np.random.RandomState, environment):
        x_min, x_max, y_min, y_max, z_min, z_max = environment['full_env/extent']
        pos = action_rng.uniform([x_min, y_min, z_min], [x_max, y_max, z_max])
        return pos

    @staticmethod
    def sample_action(environment: Dict,
                      service_provider,
                      state,
                      last_action: Optional[Dict],
                      params: CollectDynamicsParams,
                      action_rng):
        # sample the previous action with 80% probability, this improves exploration
        if last_action is not None and action_rng.uniform(0, 1) < 0.80:
            return last_action
        else:
            pos = LinkBotScenario.random_pos(action_rng, environment)
            return {
                'position': pos,
                'timeout': [params.dt],
            }

    @staticmethod
    def plot_state_simple(ax, state: Dict, **kwargs):
        link_bot_points = np.reshape(state['link_bot'], [-1, 2])
        x = link_bot_points[0, 0]
        y = link_bot_points[0, 1]
        color = kwargs.get('color')
        label = kwargs.get('label')
        scatt = ax.scatter(x, y, c=color, label=label, **kwargs)
        return scatt

    @staticmethod
    def plot_state(ax: plt.Axes,
                   state: Dict,
                   color='b',
                   s: int = 20,
                   zorder: int = 1,
                   label: Optional[str] = None,
                   linewidth=4,
                   **kwargs):
        link_bot_points = np.reshape(state['link_bot'], [-1, 2])
        xs = link_bot_points[:, 0]
        ys = link_bot_points[:, 1]
        scatt_c = color if isinstance(color, str) else np.reshape(color, [1, -1])
        scatt = ax.scatter(xs[0], ys[0], c=scatt_c, s=s, zorder=zorder)
        line = ax.plot(xs, ys, linewidth=linewidth, c=color, zorder=zorder, label=label, **kwargs)[0]
        txt = None
        if 'num_diverged' in state:
            txt = ax.text(x=xs[-1], y=ys[-1], s=f"{int(np.squeeze(state['num_diverged']))}", zorder=zorder + 1, alpha=0.8,
                          fontsize=12)
            txt.set_path_effects([PathEffects.withStroke(linewidth=1, foreground='w')])

        return line, scatt, txt

    @staticmethod
    def plot_action(ax, state: Dict, action, color, s: int, zorder: int, linewidth=1, **kwargs):
        link_bot_points = np.reshape(state['link_bot'], [-1, 2])
        artist = plot_arrow(ax, link_bot_points[-1, 0], link_bot_points[-1, 1], action[0], action[1], zorder=zorder,
                            linewidth=linewidth, color=color, **kwargs)
        return artist

    @staticmethod
    def state_to_points(state: Dict):
        """
        :param state:
        :return:
        >>> LinkBotScenario.state_to_points({'link_bot': np.array([0, 0, 1, 1, 2, 2]), 'gripper': np.array([0, 0])})
        array([[0, 0],
               [1, 1],
               [2, 2],
               [0, 0]])
        """
        link_bot_points = np.reshape(state['link_bot'], [-1, 2])
        if 'gripper' in state:
            gripper_position = np.reshape(state['gripper'], [-1, 2])
            points = np.concatenate([link_bot_points, gripper_position], axis=0)
            return points
        else:
            return link_bot_points

    @staticmethod
    def state_to_gripper_position(state: Dict):
        gripper_position = np.reshape(state['gripper'], [-1, 2])
        return gripper_position

    @staticmethod
    def distance_to_goal(
            state: Dict[str, np.ndarray],
            goal: np.ndarray):
        """
        Uses the first point in the link_bot subspace as the thing which we want to move to goal
        :param state: A dictionary of numpy arrays
        :param goal: Assumed to be a point in 2D
        :return:
        """
        link_bot_points = np.reshape(state['link_bot'], [-1, 2])
        tail_point = link_bot_points[0]
        distance = np.linalg.norm(tail_point - goal)
        return distance

    @staticmethod
    def distance_to_goal_differentiable(state, goal):
        link_bot_points = tf.reshape(state['link_bot'], [-1, 2])
        tail_point = link_bot_points[0]
        distance = tf.linalg.norm(tail_point - goal)
        return distance

    @staticmethod
    def distance(s1, s2):
        # NOTE: using R^22 distance angles the rope shape more, so we don't use it.
        link_bot_points1 = np.reshape(s1['link_bot'], [-1, 2])
        tail_point1 = link_bot_points1[0]
        link_bot_points2 = np.reshape(s2['link_bot'], [-1, 2])
        tail_point2 = link_bot_points2[0]
        return np.linalg.norm(tail_point1 - tail_point2)

    @staticmethod
    def distance_differentiable(s1, s2):
        # NOTE: using R^22 distance angles the rope shape more, so we don't use it.
        link_bot_points1 = tf.reshape(s1['link_bot'], [-1, 2])
        tail_point1 = link_bot_points1[0]
        link_bot_points2 = tf.reshape(s2['link_bot'], [-1, 2])
        tail_point2 = link_bot_points2[0]
        return tf.linalg.norm(tail_point1 - tail_point2)

    @staticmethod
    def get_subspace_weight(subspace_name: str):
        if subspace_name == 'link_bot':
            return 1.0
        elif subspace_name == 'tether':
            return 0.0
        else:
            raise NotImplementedError("invalid subspace_name {}".format(subspace_name))

    @staticmethod
    def sample_goal(state, goal):
        link_bot_state = state['link_bot']
        goal_points = np.reshape(link_bot_state, [-1, 2])
        goal_points -= goal_points[0]
        goal_points += goal
        goal_state = goal_points.flatten()
        return {
            'link_bot': goal_state
        }

    @staticmethod
    def to_rope_local_frame(state, reference_state=None):
        rope_state = state['link_bot']
        if reference_state is None:
            reference_rope_state = np.copy(rope_state)
        else:
            reference_rope_state = reference_state['link_bot']
        return LinkBotScenario.to_rope_local_frame_np(rope_state, reference_rope_state)

    @staticmethod
    def to_rope_local_frame_np(rope_state, reference_rope_state=None):
        if reference_rope_state is None:
            reference_rope_state = np.copy(rope_state)
        rope_local = LinkBotScenario.to_rope_local_frame_tf(add_batch(rope_state), add_batch(reference_rope_state))
        return remove_batch(rope_local).numpy()

    @staticmethod
    def to_rope_local_frame_tf(rope_state, reference_rope_state=None):
        from tensorflow_graphics.geometry import transformation
        if reference_rope_state is None:
            reference_rope_state = tf.identity(rope_state)
        batch_size = rope_state.shape[0]
        rope_points = tf.reshape(rope_state, [batch_size, -1, 2])
        reference_rope_points = tf.reshape(reference_rope_state, [batch_size, -1, 2])
        n_points = rope_points.shape[1]
        # rotate so the link from head to previous node is along positive X axis
        deltas = reference_rope_points[:, 1:] - reference_rope_points[:, :-1]
        last_dxs = deltas[:, -1, 0]
        last_dys = deltas[:, -1, 1]
        angles_of_last_link = -tf.expand_dims(tf.atan2(last_dys, last_dxs), axis=1)
        rotation_matrix = transformation.rotation_matrix_2d.from_euler(angles_of_last_link)
        rotation_matrix_tiled = tf.tile(tf.expand_dims(rotation_matrix, axis=1), [1, n_points, 1, 1])
        rotated_points = transformation.rotation_matrix_2d.rotate(rope_points, rotation_matrix_tiled)
        reference_rotated_points = transformation.rotation_matrix_2d.rotate(reference_rope_points, rotation_matrix_tiled)
        # translate so head is at 0,0
        rotated_points -= reference_rotated_points[:, tf.newaxis, -1]
        rotated_vectors = tf.reshape(rotated_points, [batch_size, -1])
        return rotated_vectors

    @staticmethod
    def plot_goal(ax, goal, color='g', label=None, **kwargs):
        ax.scatter(goal[0], goal[1], c=color, label=label, **kwargs)

    @classmethod
    def plot_environment(cls, ax, environment: Dict):
        occupancy = environment['full_env/env']
        extent = environment['full_env/extent']
        ax.imshow(np.flipud(occupancy), extent=extent, cmap='Greys')

    @staticmethod
    def update_artist(artist, state, **kwargs):
        """ artist: Whatever was returned by plot_state """
        line, scatt, txt = artist
        link_bot_points = np.reshape(state['link_bot'], [-1, 2])
        xs = link_bot_points[:, 0]
        ys = link_bot_points[:, 1]
        line.set_data(xs, ys)
        if 'alpha' in kwargs:
            line.set_alpha(kwargs['alpha'])
        scatt.set_offsets(link_bot_points[0])
        if txt is not None:
            txt.set_text(f"{int(np.squeeze(state['num_diverged']))}")
            txt.set_x(xs[-1])
            txt.set_y(ys[-1])

    @staticmethod
    def update_action_artist(artist, state, action):
        """ artist: Whatever was returned by plot_state """
        link_bot_points = np.reshape(state['link_bot'], [-1, 2])
        update_arrow(artist, link_bot_points[-1, 0], link_bot_points[-1, 1], action[0], action[1])

    @staticmethod
    def publish_state_marker(marker_provider: MarkerProvider, state):
        link_bot_points = np.reshape(state['link_bot'], [-1, 2])
        tail_point = link_bot_points[0]
        marker_provider.publish_marker(id=0, rgb=[1, 0, 0], scale=0.05, x=tail_point[0], y=tail_point[1])

    @staticmethod
    def publish_goal_marker(marker_provider: MarkerProvider, goal, size: float):
        marker_provider.publish_marker(id=0, rgb=[1, 0, 0], scale=size, x=goal[0], y=goal[1])

    @staticmethod
    def local_environment_center(state):
        link_bot_points = tf.reshape(state['link_bot'], [-1, 2])
        head_point_where_gripper_is = link_bot_points[-1]
        return head_point_where_gripper_is

    @staticmethod
    def local_environment_center_differentiable(state):
        """
        :param state: Dict of batched states
        :return:
        """
        link_bot_state = None
        if 'link_bot' in state:
            link_bot_state = state['link_bot']
        elif add_planned('link_bot') in state:
            link_bot_state = state[add_planned('link_bot')]
        b = int(link_bot_state.shape[0])
        link_bot_points = tf.reshape(link_bot_state, [b, -1, 2])
        head_point_where_gripper_is = link_bot_points[:, -1]
        return head_point_where_gripper_is

    def __repr__(self):
        return "Rope Manipulation"

    def simple_name(self):
        return "link_bot"

    @staticmethod
    def robot_name():
        return "link_bot"

    @staticmethod
    def dynamics_loss_function(dataset_element, predictions):
        return dynamics_loss_function(dataset_element, predictions)

    @staticmethod
    def dynamics_metrics_function(dataset_element, predictions):
        return dynamics_points_metrics_function(dataset_element, predictions)

    @staticmethod
    def integrate_dynamics(s_t, ds_t):
        return s_t + ds_t

    @staticmethod
    def get_environment_from_state_dict(start_states: Dict):
        return {}

    @staticmethod
    def get_environment_from_example(example: Dict):
        if isinstance(example, tuple):
            example = example[0]

        return {
            'full_env/env': example['full_env/env'],
            'full_env/origin': example['full_env/origin'],
            'full_env/res': example['full_env/res'],
            'full_env/extent': example['full_env/extent'],
        }

    @staticmethod
    def put_state_local_frame(state_key, state):
        batch_size, time, _ = state.shape
        if state_key in ['link_bot', 'gripper']:
            points = tf.reshape(state, [batch_size, time, -1, 2])
            points = points - points[:, :, tf.newaxis, 0]
            state_in_local_frame = tf.reshape(points, [batch_size, time, -1])
            return state_in_local_frame
        else:
            raise NotImplementedError()
