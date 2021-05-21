import pathlib
from typing import Dict, Optional, List

import tensorflow as tf
from colorama import Fore

from link_bot_classifiers.base_constraint_checker import BaseConstraintChecker
from link_bot_classifiers.nn_classifier import NNClassifier
from link_bot_data.dataset_utils import add_predicted
from link_bot_pycommon.experiment_scenario import ExperimentScenario
from link_bot_pycommon.pycommon import make_dict_tf_float32
from link_bot_pycommon.scenario_with_visualization import ScenarioWithVisualization
from moonshine.moonshine_utils import add_batch, sequence_of_dicts_to_dict_of_tensors, remove_batch


class NNClassifierWrapper(BaseConstraintChecker):
    def __init__(self, path: pathlib.Path, batch_size: int, scenario: ExperimentScenario):
        """
        Unlike the BaseConstraintChecker, this takes in list of paths, like cl_trials/dir1/dir2/best_checkpoint
        Args:
            path:
            batch_size:
            scenario:
        """
        super().__init__(path, scenario)
        self.name = self.__class__.__name__
        # FIXME: Bad API design
        assert isinstance(scenario, ScenarioWithVisualization)

        self.dataset_labeling_params = self.hparams['classifier_dataset_hparams']['labeling_params']
        self.data_collection_params = self.hparams['classifier_dataset_hparams']['data_collection_params']
        self.horizon = self.dataset_labeling_params['classifier_horizon']

        net_class_name = self.get_net_class()

        self.net = net_class_name(hparams=self.hparams, batch_size=batch_size, scenario=scenario)

        ckpt = tf.train.Checkpoint(model=self.net)
        manager = tf.train.CheckpointManager(ckpt, path, max_to_keep=1)

        status = ckpt.restore(manager.latest_checkpoint).expect_partial()
        if manager.latest_checkpoint:
            print(Fore.CYAN + "Restored from {}".format(manager.latest_checkpoint) + Fore.RESET)
            if manager.latest_checkpoint:
                status.assert_nontrivial_match()
        else:
            raise RuntimeError(f"Failed to restore {manager.latest_checkpoint}!!!")

        self.state_keys = self.net.state_keys
        self.action_keys = self.net.action_keys
        self.true_state_keys = self.net.true_state_keys
        self.pred_state_keys = self.net.pred_state_keys

    # @tf.function
    def check_constraint_from_example(self, example: Dict, training: Optional[bool] = False):
        example_preprocessed = self.net.preprocess_no_gradient(example, training)
        predictions = self.net(example_preprocessed, training=training)
        return predictions

    def check_constraint_tf_batched(self,
                                    environment: Dict,
                                    states: Dict,
                                    actions: Dict,
                                    batch_size: int,
                                    state_sequence_length: int):
        # construct network inputs
        net_inputs = {
            'batch_size': batch_size,
            'time':       state_sequence_length,
        }
        if 'scene_msg' in environment:
            environment.pop('scene_msg')
        net_inputs.update(make_dict_tf_float32(environment))

        for action_key in self.action_keys:
            net_inputs[action_key] = tf.cast(actions[action_key], tf.float32)

        for state_key in self.state_keys:
            planned_state_key = add_predicted(state_key)
            net_inputs[planned_state_key] = tf.cast(states[state_key], tf.float32)

        if self.hparams['stdev']:
            net_inputs[add_predicted('stdev')] = tf.cast(states['stdev'], tf.float32)

        net_inputs = make_dict_tf_float32(net_inputs)
        predictions = self.check_constraint_from_example(net_inputs, training=False)
        probability = predictions['probabilities']
        probability = tf.squeeze(probability, axis=2)
        return probability

    def check_constraint_tf(self,
                            environment: Dict,
                            states_sequence: List[Dict],
                            actions: List[Dict]):
        environment = add_batch(environment)
        states_sequence_dict = sequence_of_dicts_to_dict_of_tensors(states_sequence)
        states_sequence_dict = add_batch(states_sequence_dict)
        state_sequence_length = len(states_sequence)
        actions_dict = sequence_of_dicts_to_dict_of_tensors(actions)
        actions_dict = add_batch(actions_dict)
        probabilities = self.check_constraint_tf_batched(environment=environment,
                                                         states=states_sequence_dict,
                                                         actions=actions_dict,
                                                         batch_size=1,
                                                         state_sequence_length=state_sequence_length)
        probabilities = remove_batch(probabilities)
        return probabilities

    def check_constraint(self,
                         environment: Dict,
                         states_sequence: List[Dict],
                         actions: List[Dict]):
        probabilities = self.check_constraint_tf(environment=environment,
                                                 states_sequence=states_sequence,
                                                 actions=actions)
        probabilities = probabilities.numpy()
        return probabilities

    @staticmethod
    def get_net_class():
        return NNClassifier