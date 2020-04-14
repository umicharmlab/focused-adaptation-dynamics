import pathlib
from typing import List, Dict
import numpy as np

import tensorflow as tf

from link_bot_data.base_dataset import BaseDataset
from link_bot_data.dynamics_dataset import DynamicsDataset
from link_bot_data.link_bot_dataset_utils import add_next, add_planned, add_next_and_planned, add_all_and_planned, \
    null_future_states, add_all, null_previous_states, balance, null_diverged
from link_bot_planning.model_utils import EnsembleDynamicsFunction
from link_bot_planning.params import FullEnvParams


def add_model_predictions(fwd_model: EnsembleDynamicsFunction, tf_dataset, dataset: DynamicsDataset, labeling_params: Dict):
    batch_size = 2
    for in_example in tf_dataset.batch(batch_size):
        inputs, outputs = in_example
        full_env = inputs['full_env/env']
        full_env_origin = inputs['full_env/origin']
        full_env_extent = inputs['full_env/extent']
        full_env_res = inputs['full_env/res']
        traj_idx = inputs['traj_idx']
        actions = inputs['action']

        for start_t in range(0, dataset.max_sequence_length - 1, 5):
            start_states_t = {}
            for name in dataset.states_description.keys():
                start_state_t = tf.expand_dims(inputs[name][:, start_t], axis=1)
                start_states_t[name] = start_state_t

            predictions_from_start_t = fwd_model.propagate_differentiable_batched(start_states_t, actions[:, start_t:])
            # null out all the predictions past divergence
            predictions_from_start_t, last_valid_ts = null_diverged(outputs, predictions_from_start_t, start_t, labeling_params)
            # when start_t > 0, this output will need to be padded so that all outputs are the same size
            all_predictions = null_previous_states(predictions_from_start_t, dataset.max_sequence_length)

            for batch_idx in range(full_env.shape[0]):
                out_example = {
                    'full_env/env': full_env[batch_idx],
                    'full_env/origin': full_env_origin[batch_idx],
                    'full_env/extent': full_env_extent[batch_idx],
                    'full_env/res': full_env_res[batch_idx],
                    'traj_idx': traj_idx[batch_idx, 0],
                }
                end_t_stepped = np.linspace(start_t + 1, last_valid_ts[batch_idx], num=5).astype(np.int32)
                for end_t in end_t_stepped:
                    print(start_t + 1, end_t)
                    # take the true states and the predicted states and add them to the output dictionary
                    out_example['start_t'] = start_t
                    out_example['end_t'] = end_t

                    for name in fwd_model.states_keys:
                        # true state, $s^t$
                        out_example[name] = outputs[name][batch_idx, end_t - 1]
                        # true next state $s^{t+1}$
                        out_example[add_next(name)] = outputs[name][batch_idx, end_t]
                        out_example[add_all(name)] = outputs[name][batch_idx]

                    for name in all_predictions.keys():
                        predictions_for_name = all_predictions[name][batch_idx]
                        # predicted state $\hat{s^t}$
                        out_example[add_planned(name)] = predictions_for_name[end_t - 1]
                        # predicted next state $\hat{s^{t+1}}$
                        out_example[add_next_and_planned(name)] = predictions_for_name[end_t]
                        # ALL predicted states $s^0, \hat{s}^1, ..., \hat{s^{t+1}}$, null padded
                        # you have to use some null padding instead of slicing because all examples must have the same size
                        predictions_nulled_future = null_future_states(predictions_for_name, end_t)
                        out_example[add_all_and_planned(name)] = predictions_nulled_future

                    # action
                    out_example['action'] = inputs['action'][batch_idx, start_t]

                    # compute label
                    state_key = labeling_params['state_key']
                    state_key_next = add_next(state_key)
                    planned_state_key_next = add_next_and_planned(state_key)
                    post_transition_distance = tf.norm(out_example[state_key_next] - out_example[planned_state_key_next])
                    threshold = labeling_params['post_close_threshold']
                    post_close = post_transition_distance < threshold
                    out_example['label'] = tf.expand_dims(tf.cast(post_close, dtype=tf.float32), axis=0)

                    yield out_example


class ClassifierDataset(BaseDataset):

    def __init__(self, dataset_dirs: List[pathlib.Path]):
        super(ClassifierDataset, self).__init__(dataset_dirs)
        self.full_env_params = FullEnvParams.from_json(self.hparams['full_env_params'])
        self.labeling_params = self.hparams['labeling_params']
        self.label_state_key = self.hparams['labeling_params']['state_key']

        self.state_keys = self.hparams['state_keys']

        self.feature_names = [
            'full_env/env',
            'full_env/origin',
            'full_env/extent',
            'full_env/res',
            'traj_idx',
            'start_t',
            'end_t',
            'action',
            'label',
        ]

        for k in self.state_keys:
            self.feature_names.append(k)
            self.feature_names.append(add_next(k))
            self.feature_names.append(add_all(k))
            self.feature_names.append(add_planned(k))
            self.feature_names.append(add_all_and_planned(k))
            self.feature_names.append(add_next_and_planned(k))

    def make_features_description(self):
        features_description = {}
        for feature_name in self.feature_names:
            features_description[feature_name] = tf.io.FixedLenFeature([], tf.string)

        return features_description

    def post_process(self, dataset: tf.data.TFRecordDataset, n_parallel_calls: int):
        # dataset = balance(dataset, labeling_params=self.labeling_params)
        return dataset
