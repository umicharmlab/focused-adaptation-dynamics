import pathlib
from typing import Dict

import numpy as np
import tensorflow as tf
import tensorflow.keras.layers as layers
from colorama import Fore

from moonshine.numpy_utils import add_batch, remove_batch
from moonshine.tensorflow_train_test_loop import MyKerasModel
from state_space_dynamics.base_dynamics_function import BaseDynamicsFunction


class SimpleNN(MyKerasModel):

    def __init__(self, hparams: Dict, batch_size: int):
        super().__init__(hparams=hparams, batch_size=batch_size)
        self.initial_epoch = 0

        self.concat = layers.Concatenate()
        self.dense_layers = []
        for fc_layer_size in self.hparams['fc_layer_sizes']:
            self.dense_layers.append(layers.Dense(fc_layer_size, activation='relu', use_bias=True))
        # TODO: make state_key always mean without "state/" and state_feature_name always mean with
        self.state_key = self.hparams['state_key']
        # TODO: support multiple state keys like in obstacle_nn
        self.state_feature_name = "state/{}".format(self.state_key)
        self.n_state = self.hparams['dynamics_dataset_hparams']['states_description'][self.state_key]
        self.dense_layers.append(layers.Dense(self.n_state, activation=None))

    def call(self, dataset_element, training=None, mask=None):
        input_dict, _ = dataset_element
        states = input_dict[self.state_feature_name]
        actions = input_dict['action']
        input_sequence_length = actions.shape[1]
        s_0 = states[:, 0]

        pred_states = [s_0]
        for t in range(input_sequence_length):
            s_t = pred_states[-1]
            action_t = actions[:, t]

            _state_action_t = self.concat([s_t, action_t])
            z_t = _state_action_t
            for dense_layer in self.dense_layers:
                z_t = dense_layer(z_t)

            if self.hparams['residual']:
                ds_t = z_t
                s_t_plus_1_flat = s_t + ds_t
            else:
                s_t_plus_1_flat = z_t

            pred_states.append(s_t_plus_1_flat)

        pred_states = tf.stack(pred_states, axis=1)
        return {self.state_feature_name: pred_states}


class SimpleNNWrapper(BaseDynamicsFunction):

    def __init__(self, model_dir: pathlib.Path, batch_size: int):
        super().__init__(model_dir, batch_size)
        self.net = SimpleNN(hparams=self.hparams, batch_size=batch_size)
        self.ckpt = tf.train.Checkpoint(net=self.net)
        self.manager = tf.train.CheckpointManager(self.ckpt, model_dir, max_to_keep=1)
        self.ckpt.restore(self.manager.latest_checkpoint)
        if self.manager.latest_checkpoint:
            print(Fore.CYAN + "Restored from {}".format(self.manager.latest_checkpoint) + Fore.RESET)
        self.state_keys = [self.net.state_key]

    def propagate_differentiable(self,
                                 full_env: np.ndarray,
                                 full_env_origin: np.ndarray,
                                 res: float,
                                 start_states: Dict[str, np.ndarray],
                                 actions: tf.Variable) -> Dict[str, tf.Tensor]:
        """
        :param full_env:        (H, W)
        :param full_env_origin: (2)
        :param res:             scalar
        :param start_states:          each value in the dictionary should be of shape (batch, n_state)
        :param actions:        (T, 2)
        :return: states:       each value in the dictionary should be a of shape [batch, T+1, n_state)
        """
        del full_env  # unused
        del full_env_origin  # unused
        del res  # unsed
        state = start_states[self.net.state_feature_name]
        state = np.expand_dims(state, axis=0)
        state = tf.convert_to_tensor(state, dtype=tf.float32)
        actions = tf.convert_to_tensor(actions, dtype=tf.float32)
        test_x = {
            # must be batch, T, n_state
            self.net.state_feature_name: state,
            # must be batch, T, 2
            'action': actions,
        }
        test_x = add_batch(test_x)
        predictions = self.net((test_x, None))
        predictions = remove_batch(predictions)
        return predictions
