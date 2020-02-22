#!/usr/bin/env python
from __future__ import print_function

import json
import pathlib
from typing import Dict

import numpy as np
import tensorflow as tf
import tensorflow.keras.layers as layers
from colorama import Fore
from tensorflow import keras

from link_bot_classifiers.base_classifier import BaseClassifier
from link_bot_planning.params import LocalEnvParams
from link_bot_pycommon.link_bot_sdf_utils import OccupancyData
from moonshine.base_model import BaseModel
from moonshine.raster_points_layer import make_transition_image


class RasterClassifier(BaseModel):

    def __init__(self, hparams: Dict, batch_size: int, *args, **kwargs):
        super().__init__(hparams, batch_size, *args, **kwargs)
        self.dynamics_dataset_hparams = self.hparams['classifier_dataset_hparams']['fwd_model_hparams'][
            'dynamics_dataset_hparams']
        self.n_action = self.dynamics_dataset_hparams['n_action']
        self.batch_size = batch_size

        self.local_env_params = LocalEnvParams.from_json(self.dynamics_dataset_hparams['local_env_params'])

        self.conv_layers = []
        self.pool_layers = []
        for n_filters, kernel_size in self.hparams['conv_filters']:
            conv = layers.Conv2D(n_filters,
                                 kernel_size,
                                 activation='relu',
                                 kernel_regularizer=keras.regularizers.l2(self.hparams['kernel_reg']),
                                 bias_regularizer=keras.regularizers.l2(self.hparams['bias_reg']),
                                 activity_regularizer=keras.regularizers.l1(self.hparams['activity_reg']))
            pool = layers.MaxPool2D(2)
            self.conv_layers.append(conv)
            self.pool_layers.append(pool)

        self.conv_flatten = layers.Flatten()
        if self.hparams['batch_norm']:
            self.batch_norm = layers.BatchNormalization()

        self.dense_layers = []
        self.dropout_layers = []
        for hidden_size in self.hparams['fc_layer_sizes']:
            dropout = layers.Dropout(rate=self.hparams['dropout_rate'])
            dense = layers.Dense(hidden_size,
                                 activation='relu',
                                 kernel_regularizer=keras.regularizers.l2(self.hparams['kernel_reg']),
                                 bias_regularizer=keras.regularizers.l2(self.hparams['bias_reg']),
                                 activity_regularizer=keras.regularizers.l1(self.hparams['activity_reg']))
            self.dropout_layers.append(dropout)
            self.dense_layers.append(dense)

        self.output_layer = layers.Dense(1, activation='sigmoid')

    def _conv(self, image):
        # feed into a CNN
        conv_z = image
        for conv_layer, pool_layer in zip(self.conv_layers, self.pool_layers):
            conv_h = conv_layer(conv_z)
            conv_z = pool_layer(conv_h)
        out_conv_z = conv_z

        return out_conv_z

    def call(self, input_dict: dict, training=None, mask=None):
        # choose what key to use here
        image = input_dict[self.hparams['image_key']]
        state = input_dict['planned_state/link_bot']
        action = input_dict['action']
        next_state = input_dict['planned_state_next/link_bot']
        out_conv_z = self._conv(image)
        conv_output = self.conv_flatten(out_conv_z)

        if self.hparams['mixed']:
            conv_output = tf.concat((conv_output, state, action, next_state), axis=1)

        # plt.imshow(image[0, :, :, :-2])
        # print(input_dict['label'][0])
        # import ipdb; ipdb.set_trace()
        # plt.show()

        if self.hparams['batch_norm']:
            conv_output = self.batch_norm(conv_output)

        z = conv_output
        for dropout_layer, dense_layer in zip(self.dropout_layers, self.dense_layers):
            h = dropout_layer(z)
            z = dense_layer(h)
        out_h = z

        accept_probability = self.output_layer(out_h)
        return accept_probability


class RasterClassifierWrapper(BaseClassifier):

    def __init__(self, path: pathlib.Path, batch_size: int):
        super().__init__()
        model_hparams_file = path / 'hparams.json'
        self.model_hparams = json.load(model_hparams_file.open('r'))
        self.net = RasterClassifier(hparams=self.model_hparams, batch_size=batch_size)
        self.ckpt = tf.train.Checkpoint(net=self.net)
        self.manager = tf.train.CheckpointManager(self.ckpt, path, max_to_keep=1)
        if self.manager.latest_checkpoint:
            print(Fore.CYAN + "Restored from {}".format(self.manager.latest_checkpoint) + Fore.RESET)
        self.ckpt.restore(self.manager.latest_checkpoint)

    def predict(self, local_env_data: OccupancyData, s1: np.ndarray, s2: np.ndarray, action: np.ndarray) -> float:
        """
        :param local_env_data:
        :param s1: [n_state] float64
        :param s2: [n_state] float64
        :param action: [n_action] float64
        :return: [1] float64
        """
        image_key = self.model_hparams['image_key']
        if image_key == 'transition_image':
            origin = local_env_data.origin
            res = local_env_data.resolution[0]
            local_env = local_env_data.data
            image = make_transition_image(local_env, s1, action, s2, res, origin)
            image = tf.convert_to_tensor(image, dtype=tf.float32)
            image = tf.expand_dims(image, axis=0)
        elif image_key == 'trajectory_image':
            image = None
        test_x = {image_key: image}
        image = test_x
        accept_probabilities = self.net(image)
        accept_probabilities = accept_probabilities.numpy()
        accept_probabilities = accept_probabilities.astype(np.float64).squeeze()

        return accept_probabilities
