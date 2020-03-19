import re
from typing import Optional, Dict, List

import tensorflow as tf

from link_bot_planning.experiment_scenario import ExperimentScenario
from link_bot_pycommon import link_bot_pycommon
from link_bot_pycommon.link_bot_sdf_utils import get_local_env_and_origin_differentiable
from moonshine.action_smear_layer import smear_action_differentiable
from moonshine.numpy_utils import add_batch


# @tf.function
def make_transition_image(full_env,
                          full_env_origin,
                          res,
                          planned_states,
                          action,
                          planned_next_states,
                          scenario: ExperimentScenario,
                          local_env_h: int,
                          local_env_w: int,
                          action_in_image: Optional[bool] = False):
    """
    :param planned_states: each element should be [batch,n_state]
    :param action: [batch,n_action]
    :param planned_next_states: each element should be [batch,n_state]
    :param res: [batch]
    :param action_in_image: include new channels for actions
    :return: [batch,n_points*2+n_action+1], aka  [batch,n_state+n_action+1]
    """
    local_env_center_point = scenario.local_environment_center_differentiable(planned_states)
    # TODO: these functions are scattered all over the place, organize better
    local_env, local_env_origin = get_local_env_and_origin_differentiable(center_point=local_env_center_point,
                                                                          full_env=full_env,
                                                                          full_env_origin=full_env_origin,
                                                                          res=res,
                                                                          local_h_rows=local_env_h,
                                                                          local_w_cols=local_env_w)
    local_env = local_env
    local_env_origin = local_env_origin
    tf.print(local_env[0, 0, 0], local_env_origin[0, 0])

    concat_args = [tf.zeros([1, local_env_h, local_env_w, 1])]
    for planned_state in planned_states.values():
        planned_rope_image = raster_differentiable(state=planned_state,
                                                   res=res,
                                                   origin=local_env_origin,
                                                   h=local_env_h,
                                                   w=local_env_w)
        concat_args.append(planned_rope_image)
    for planned_next_state in planned_next_states.values():
        planned_next_rope_image = raster_differentiable(state=planned_next_state,
                                                        origin=local_env_origin,
                                                        res=res,
                                                        h=local_env_h,
                                                        w=local_env_w)
        concat_args.append(planned_next_rope_image)

    if action_in_image:
        # FIXME: use tf to make sure its differentiable
        action_image = smear_action_differentiable(action, local_env_h, local_env_w)
        concat_args.append(action_image)
    image = tf.concat(concat_args, axis=3)
    return image


# @tf.function
def raster_rope_images(planned_states: List[Dict],
                       res,
                       origin,
                       h: float,
                       w: float):
    """
    Raster all the state into one fixed-channel image representation using color gradient in the green channel
    :param planned_states: each element is [batch, n_state]
    :param res: [batch]
    :param origin: [batch, 2]
    :param h: scalar
    :param w: scalar
    :return: [batch, h, w, 2 * n_points]
    """
    n_time_steps = len(planned_states)
    binary_rope_images = []
    time_colored_rope_images = []
    for t in range(n_time_steps):
        planned_state_t = planned_states[t]
        # iterate over the dict, each element of which is a component of our state
        for s_t_k in planned_state_t.values():
            rope_img_t = raster_differentiable(state=s_t_k, origin=origin, res=res, h=h, w=w)
            time_color = float(t) / n_time_steps
            time_color_image_t = rope_img_t * time_color
            binary_rope_images.append(rope_img_t)
            time_colored_rope_images.append(time_color_image_t)
    binary_rope_images = tf.reduce_sum(binary_rope_images, axis=0)
    time_colored_rope_images = tf.reduce_sum(time_colored_rope_images, axis=0)
    rope_images = tf.concat((binary_rope_images, time_colored_rope_images), axis=3)
    return rope_images


# @tf.function
def make_traj_images(full_env,
                     full_env_origin,
                     res,
                     states: List[Dict]):
    """
    :param full_env: [batch, h, w]
    :param full_env_origin:  [batch, 2]
    :param res: [batch]
    :param states: each element is [batch, time, n]
    :return: [batch, h, w, 3]
    """
    h = int(full_env.shape[1])
    w = int(full_env.shape[2])

    # add channel index
    full_env = tf.expand_dims(full_env, axis=3)

    rope_imgs = raster_rope_images(states, res, full_env_origin, h, w)

    image = tf.concat((full_env, rope_imgs), axis=3)
    return image


# @tf.function
def add_traj_image(dataset):
    def _make_traj_images(full_env, full_env_origin, res, stop_index, *args):

        n_args = len(args)
        n_states = n_args // 2
        planned_states = args[:n_states]
        planned_states_keys = args[n_states:]

        # convert from a dictionary where each element is [T, n_state] to
        # a list where each element is a dictionary, and element element of that dictionary is [1 (batch), n_state]
        planned_states_seq = []
        for t in range(stop_index):
            state_t = {}
            for k, v in zip(planned_states_keys, planned_states):
                state_t[k] = tf.expand_dims(v[t], axis=0)  # add batch here
            planned_states_seq.append(state_t)

        full_env, full_env_origin, res = add_batch(full_env, full_env_origin, res)
        image = make_traj_images(full_env=full_env,
                                 full_env_origin=full_env_origin,
                                 res=res,
                                 states=planned_states_seq)[0]
        return image

    def _add_traj_image_wrapper(input_dict):
        full_env = input_dict['full_env/env']
        full_env_origin = input_dict['full_env/origin']
        res = input_dict['full_env/res']
        stop_index = input_dict['stop_idx']
        planned_states = []
        planned_state_keys = []
        # NOTE: Here we lose the semantic meaning, because we can't pass a dict to a numpy_function :(
        #  I hate TF
        for k, v in input_dict.items():
            m = re.fullmatch('planned_state/(.*)_all', k)
            if m:
                planned_state_key = 'planned_state/{}'.format(m.group(1))
                v_t = v[:stop_index]
                planned_states.append(v_t)
                planned_state_keys.append(planned_state_key)
        tensor_inputs = [full_env, full_env_origin, res, stop_index] + planned_states + planned_state_keys
        image = tf.numpy_function(_make_traj_images, tensor_inputs, tf.float32)
        input_dict['trajectory_image'] = image
        return input_dict

    return dataset.map(_add_traj_image_wrapper)


# @tf.function
def add_transition_image(dataset,
                         states_keys: List[str],
                         scenario: ExperimentScenario,
                         local_env_w: int,
                         local_env_h: int,
                         action_in_image: Optional[bool] = False):
    def _add_transition_image(input_dict):
        action = input_dict['action']

        planned_states = {}
        planned_next_states = {}
        n_total_points = 0
        for state_key in states_keys:
            planned_state_feature_name = 'planned_state/{}'.format(state_key)
            planned_state_next_feature_name = 'planned_state/{}_next'.format(state_key)
            planned_state = input_dict[planned_state_feature_name]
            planned_next_state = input_dict[planned_state_next_feature_name]
            n_total_points += link_bot_pycommon.n_state_to_n_points(planned_state.shape[0])
            planned_states[state_key] = planned_state
            planned_next_states[state_key] = planned_next_state

        full_env = input_dict['full_env/env']
        full_env_res = tf.squeeze(input_dict['full_env/res'])
        full_env_origin = input_dict['full_env/origin']
        n_action = action.shape[0]

        batched_inputs = add_batch(full_env, full_env_origin, full_env_res, planned_states, action, planned_next_states)
        image = make_transition_image(*batched_inputs,
                                      scenario=scenario,
                                      local_env_h=local_env_h,
                                      local_env_w=local_env_w,
                                      action_in_image=action_in_image)
        # remove batch dim
        image = image[0]
        n_channels = 1 + 2 * n_total_points
        if action_in_image:
            n_channels += n_action

        image.set_shape([local_env_h, local_env_w, n_channels])

        input_dict['transition_image'] = image
        return input_dict

    return dataset.map(_add_transition_image)


# @tf.function
def raster_differentiable(state, res, origin, h, w):
    """
    Even though this data is batched, we use singular and reserve plural for sequences in time
    state: [batch, n]
    res: [batch] scalar float
    origins: [batch, 2] index (so int, or technically float is fine too)
    h: scalar int
    w: scalar int
    return: [batch, h, w, n_points]
    """
    b = int(state.shape[0])
    points = tf.reshape(state, [b, -1, 2])
    n_points = int(points.shape[1])
    res = res[0]

    k = 10000.0

    ## Below is a un-vectorized implementation, which is much easier to read and understand
    # rope_images = np.zeros([b, h, w, n_points], dtype=np.float32)
    # for batch_index in range(b):
    #     for point_idx in range(n_points):
    #         for row, col in np.ndindex(h, w):
    #             point_in_meters = points[batch_index, point_idx]
    #             pixel_center_in_meters = idx_to_point(row, col, res, origins[batch_index])
    #             squared_distance = np.sum(np.square(point_in_meters - pixel_center_in_meters))
    #             pixel_value = np.exp(-k*squared_distance)
    #             rope_images[batch_index, row, col, point_idx] += pixel_value
    # rope_images = rope_images

    ## vectorized implementation

    # add h & w dimensions
    tiled_points = tf.expand_dims(tf.expand_dims(points, axis=1), axis=1)
    tiled_points = tf.tile(tiled_points, [1, h, w, 1, 1])
    pixel_row_indices = tf.range(0, h, dtype=tf.float32)
    pixel_col_indices = tf.range(0, w, dtype=tf.float32)
    # pixel_indices is b, n_points, 2
    pixel_indices = tf.stack(tf.meshgrid(pixel_row_indices, pixel_col_indices), axis=2)
    # add batch dim
    pixel_indices = tf.expand_dims(pixel_indices, axis=0)
    pixel_indices = tf.tile(pixel_indices, [b, 1, 1, 1])

    # shape [b, h, w, 2]
    origin_expanded = tf.expand_dims(tf.expand_dims(origin, axis=1), axis=1)
    pixel_centers = (pixel_indices - origin_expanded) * res

    # add n_points dim
    pixel_centers = tf.expand_dims(pixel_centers, axis=3)
    pixel_centers = tf.tile(pixel_centers, [1, 1, 1, n_points, 1])

    squared_distances = tf.reduce_sum(tf.square(pixel_centers - tiled_points), axis=4)
    pixel_values = tf.exp(-k * squared_distances)
    rope_images = tf.reshape(pixel_values, [b, h, w, n_points])
    ##############################################################################
    # FIXME: figure out whether to do clipping or normalization
    ##############################################################################
    return rope_images


# @tf.function
def bilinear_sampler(img, x, y):
    """
    Performs bilinear sampling of the input images according to the
    normalized coordinates provided by the sampling grid. Note that
    the sampling is done identically for each channel of the input.

    To test if the function works properly, output image should be
    identical to input image when theta is initialized to identity
    transform.

    Input
    -----
    - img: batch of images in (B, H, W, C) layout.
    - grid: x, y which is the output of affine_grid_generator.

    Returns
    -------
    - out: interpolated images according to grids. Same size as grid.
    """
    H = tf.shape(img)[1]
    W = tf.shape(img)[2]
    max_y = tf.cast(H - 1, 'int32')
    max_x = tf.cast(W - 1, 'int32')
    zero = tf.zeros([], dtype='int32')

    # rescale x and y to [0, W-1/H-1]
    x = tf.cast(x, 'float32')
    y = tf.cast(y, 'float32')
    x = 0.5 * ((x + 1.0) * tf.cast(max_x - 1, 'float32'))
    y = 0.5 * ((y + 1.0) * tf.cast(max_y - 1, 'float32'))

    # grab 4 nearest corner points for each (x_i, y_i)
    x0 = tf.cast(tf.floor(x), 'int32')
    x1 = x0 + 1
    y0 = tf.cast(tf.floor(y), 'int32')
    y1 = y0 + 1

    # clip to range [0, H-1/W-1] to not violate img boundaries
    x0 = tf.clip_by_value(x0, zero, max_x)
    x1 = tf.clip_by_value(x1, zero, max_x)
    y0 = tf.clip_by_value(y0, zero, max_y)
    y1 = tf.clip_by_value(y1, zero, max_y)

    # get pixel value at corner coords
    Ia = get_pixel_value(img, x0, y0)
    Ib = get_pixel_value(img, x0, y1)
    Ic = get_pixel_value(img, x1, y0)
    Id = get_pixel_value(img, x1, y1)

    # recast as float for delta calculation
    x0 = tf.cast(x0, 'float32')
    x1 = tf.cast(x1, 'float32')
    y0 = tf.cast(y0, 'float32')
    y1 = tf.cast(y1, 'float32')

    # calculate deltas
    wa = (x1 - x) * (y1 - y)
    wb = (x1 - x) * (y - y0)
    wc = (x - x0) * (y1 - y)
    wd = (x - x0) * (y - y0)

    # add dimension for addition
    wa = tf.expand_dims(wa, axis=3)
    wb = tf.expand_dims(wb, axis=3)
    wc = tf.expand_dims(wc, axis=3)
    wd = tf.expand_dims(wd, axis=3)

    # compute output
    out = tf.add_n([wa * Ia, wb * Ib, wc * Ic, wd * Id])

    return out


def get_pixel_value(img, x, y):
    """
    Utility function to get pixel value for coordinate
    vectors x and y from a  4D tensor image.

    Input
    -----
    - img: tensor of shape (B, H, W, C)
    - x: tensor of shape (B, H, W)
    - y: tensor of shape (B, H, W)

    Returns
    -------
    - output: tensor of shape (B, H, W, C)
    """
    shape = tf.shape(x)
    batch_size = shape[0]
    height = shape[1]
    width = shape[2]

    batch_idx = tf.range(0, batch_size)
    batch_idx = tf.reshape(batch_idx, (batch_size, 1, 1))
    b = tf.tile(batch_idx, (1, height, width))

    indices = tf.stack([b, y, x], 3)

    return tf.gather_nd(img, indices)


# Numpy is only used be the one function below
import numpy as np


def old_raster(state, res, origin, h, w):
    """
    state: [batch, n]
    res: [batch] scalar float
    origin: [batch, 2] index (so int, or technically float is fine too)
    h: scalar int
    w: scalar int
    return: [batch, h, w, n_points]
    """
    b = int(state.shape[0])
    points = np.reshape(state, [b, -1, 2])
    n_points = int(points.shape[1])

    res = res[0]  # NOTE: assume constant resolution

    # points[:,1] is y, origin[0] is row index, so yes this is correct
    row_y_indices = (points[:, :, 1] / res + origin[:, 0:1]).astype(np.int64).flatten()
    col_x_indices = (points[:, :, 0] / res + origin[:, 1:2]).astype(np.int64).flatten()
    channel_indices = np.tile(np.arange(n_points), b)
    batch_indices = np.repeat(np.arange(b), n_points)

    # filter out invalid indices, which can happen during training
    state_images = np.zeros([b, h, w, n_points], dtype=np.float32)
    valid_indices = np.where(np.all([row_y_indices >= 0,
                                     row_y_indices < h,
                                     col_x_indices >= 0,
                                     col_x_indices < w], axis=0))

    state_images[batch_indices[valid_indices],
                 row_y_indices[valid_indices],
                 col_x_indices[valid_indices],
                 channel_indices[valid_indices]] = 1.0
    return state_images
