from dataclasses import dataclass
from typing import Dict, List, Optional

import numpy as np
import ompl.base as ob
import ompl.control as oc
import rospy
from colorama import Fore

from link_bot_classifiers.base_constraint_checker import BaseConstraintChecker
from link_bot_pycommon.experiment_scenario import ExperimentScenario
from link_bot_planning.link_bot_goal import MyGoalRegion
from link_bot_planning.state_spaces import ValidRopeConfigurationCompoundSampler, \
    compound_to_numpy, ompl_control_to_model_action
from link_bot_planning.trajectory_smoother import TrajectorySmoother
from link_bot_planning.viz_object import VizObject
from link_bot_pycommon.base_services import Services
from state_space_dynamics.base_dynamics_function import BaseDynamicsFunction
import matplotlib.pyplot as plt


@dataclass
class PlannerResult:
    path: Optional[List[Dict]]
    actions: Optional[np.ndarray]
    planner_status: ob.PlannerStatus


class MyPlanner:

    def __init__(self,
                 fwd_model: BaseDynamicsFunction,
                 classifier_model: BaseConstraintChecker,
                 params: Dict,
                 service_provider: Services,
                 scenario: ExperimentScenario,
                 viz_object: VizObject,
                 seed: int,
                 verbose: int,
                 ):
        self.verbose = verbose
        self.rope_length = rospy.get_param('/link_bot/rope_length')
        self.planner = None
        self.fwd_model = fwd_model
        self.classifier_model = classifier_model
        self.n_action = self.fwd_model.n_action
        self.params = params
        # TODO: consider making full env params h/w come from elsewhere. res should match the model though.
        self.full_env_params = self.fwd_model.full_env_params
        self.service_provider = service_provider
        self.viz_object = viz_object
        self.si = ob.SpaceInformation(ob.StateSpace())
        self.seed = seed
        self.classifier_rng = np.random.RandomState(seed)
        self.state_sampler_rng = np.random.RandomState(seed)
        self.scenario = scenario
        self.ax = None

        self.setup_state_space(service_provider)

        self.ss = oc.SimpleSetup(self.control_space)

        self.si = self.ss.getSpaceInformation()

        self.ss.setStatePropagator(oc.AdvancedStatePropagatorFn(self.propagate))
        self.ss.setMotionsValidityChecker(oc.MotionsValidityCheckerFn(self.motions_valid))
        self.ss.setStateValidityChecker(ob.StateValidityCheckerFn(self.is_valid))

        if params['directed_control_sampler'] == 'simple':
            pass  # the default
        else:
            raise NotImplementedError()

        # a Dictionary containing the parts of state which are not predicted/planned for, i.e. the environment
        self.environment = None

        if params['sampler_type'] == 'sample_train':
            raise NotImplementedError()

        smoothing_params = self.params['smoothing']
        # FIXME: call it "action" instead of control everywhere
        if smoothing_params is None:
            self.smoother = None
        else:
            self.smoother = TrajectorySmoother(verbose=self.verbose,
                                               fwd_model=fwd_model,
                                               classifier_model=classifier_model,
                                               params=smoothing_params,
                                               experiment_scenario=self.scenario)

    def setup_state_space(self, service_provider):
        # TODO: make Dict -> state space etc... a function
        # should we manually add stdev and num_diverged to this map?
        self.state_space_description = {}
        self.state_space = ob.CompoundStateSpace()
        # are these two lists necessary?!
        self.subspaces = []
        self.subspace_bounds = []
        subspace_idx = None
        for subspace_idx, state_key in enumerate(self.fwd_model.states_keys):
            weight = self.scenario.get_subspace_weight(state_key)
            n_state = self.fwd_model.states_description[state_key]
            subspace_description = {
                "idx": subspace_idx,
                "weight": weight,
                "n_state": n_state
            }
            self.state_space_description[state_key] = subspace_description

            subspace = ob.RealVectorStateSpace(n_state)
            bounds = ob.RealVectorBounds(n_state)
            for i in range(n_state):
                if i % 2 == 0:
                    bounds.setLow(i, -self.params['full_env_w'] / 2)
                    bounds.setHigh(i, self.params['full_env_w'] / 2)
                else:
                    bounds.setLow(i, -self.params['full_env_h'] / 2)
                    bounds.setHigh(i, self.params['full_env_h'] / 2)
            subspace.setBounds(bounds)
            self.subspaces.append(subspace_idx)
            self.subspace_bounds.append(bounds)
            self.state_space.addSubspace(subspace, weight=weight)
        # extra subspace component for the variance, which is necessary to pass information from propagate to constraint checker
        self.stdev_subspace_idx = subspace_idx + 1
        stdev_subspace = ob.RealVectorStateSpace(1)
        stdev_bounds = ob.RealVectorBounds(1)
        stdev_bounds.setLow(-1000)
        stdev_bounds.setHigh(1000)
        stdev_subspace.setBounds(stdev_bounds)
        self.subspace_bounds.append(stdev_bounds)
        self.state_space.addSubspace(stdev_subspace, weight=0)
        self.state_space_description['stdev'] = {"idx": self.stdev_subspace_idx, "weight": 0, "n_state": 1}
        # extra subspace component for the number of diverged steps
        self.num_diverged_subspace_idx = subspace_idx + 2
        num_diverged_subspace = ob.RealVectorStateSpace(1)
        num_diverged_bounds = ob.RealVectorBounds(1)
        num_diverged_bounds.setLow(-1000)
        num_diverged_bounds.setHigh(1000)
        num_diverged_subspace.setBounds(num_diverged_bounds)
        self.subspace_bounds.append(num_diverged_bounds)
        self.state_space.addSubspace(num_diverged_subspace, weight=0)
        self.state_space_description['num_diverged'] = {"idx": self.num_diverged_subspace_idx, "weight": 0, "n_state": 1}
        self.state_space.setStateSamplerAllocator(ob.StateSamplerAllocator(self.state_sampler_allocator))
        control_bounds = ob.RealVectorBounds(2)
        control_bounds.setLow(0, -np.pi)
        control_bounds.setHigh(0, np.pi)
        control_bounds.setLow(1, 0)
        max_delta_pos = service_provider.get_max_speed() * self.fwd_model.dt
        control_bounds.setHigh(1, max_delta_pos)
        self.control_space = oc.RealVectorControlSpace(self.state_space, self.n_action)
        self.control_space.setBounds(control_bounds)

    def is_valid(self, state):
        return self.state_space.satisfiesBounds(state)

    def motions_valid(self, motions):
        final_state = compound_to_numpy(self.state_space_description, motions[-1].getState())
        motions_valid = bool(np.squeeze(final_state['num_diverged'] < self.classifier_model.horizon - 1))  # yes, minus 1
        if self.verbose >= 3:
            print(final_state)
            print(motions_valid)
        if not motions_valid:
            self.viz_object.rejected_samples.append(final_state)
        return motions_valid

    def motions_to_numpy(self, motions):
        states_sequence = []
        actions = []
        for t, motion in enumerate(motions):
            # motions is a vector of oc.Motion, which has a state, parent, and control
            state = motion.getState()
            state_t = compound_to_numpy(self.state_space_description, state)
            states_sequence.append(state_t)
            if t > 0:  # skip the first (null) action, because that would represent the action that brings us to the first state
                actions.append(self.control_to_numpy(motion.getControl()))
        actions = np.array(actions)
        return states_sequence, actions

    def control_to_numpy(self, control):
        np_u = ompl_control_to_model_action(control, self.n_action)
        return np_u

    def predict(self, previous_states, previous_actions, new_action):
        # TODO: check_constraint and propagate should take in "environment" instead of these three special variances
        new_actions = np.expand_dims(new_action, axis=0)
        last_previous_state = previous_states[-1]
        predicted_states = self.fwd_model.propagate(full_env=self.environment['full_env/env'],
                                                    full_env_origin=self.environment['full_env/origin'],
                                                    res=self.fwd_model.full_env_params.res,
                                                    start_states=last_previous_state,
                                                    actions=new_actions)
        # get only the final state predicted
        final_predicted_state = predicted_states[-1]
        if self.verbose >= 3:
            self.scenario.plot_state(self.ax, state=final_predicted_state, color='r', zorder=3, s=50,
                                     label='predicted next state', linewidth=4)
            self.scenario.plot_action(self.ax, state=last_previous_state, action=new_action, color='#999922', zorder=3, s=10,
                                      linewidth=4)

        # compute new num_diverged by checking the constraint
        # walk back up the branch until num_diverged == 0
        all_states = [final_predicted_state]
        all_actions = [new_action]
        for previous_idx in range(len(previous_states) - 1, -1, -1):
            previous_state = previous_states[previous_idx]
            all_states.insert(0, previous_state)
            if self.verbose >= 3:
                self.scenario.plot_state(self.ax, state=previous_state, color='#229946', zorder=4, s=10, label='',
                                         linewidth=2)
            if previous_state['num_diverged'] == 0:
                break
            # this goes after the break because action_i brings you TO state_i and we don't want that last action
            previous_action = previous_actions[previous_idx - 1]
            all_actions.insert(0, previous_action)
            if self.verbose >= 3:
                previous_previous_state = previous_states[previous_idx - 1]
                self.scenario.plot_action(self.ax, state=previous_previous_state, action=new_action, color='orange', zorder=5,
                                          s=10,
                                          linewidth=2)
        if self.verbose >= 3:
            plt.pause(1)
            print(len(all_states))
            input("press enter to continue")
        classifier_probability = self.classifier_model.check_constraint(environment=self.environment,
                                                                        states_sequence=all_states,
                                                                        actions=all_actions)
        classifier_accept = classifier_probability > self.params['accept_threshold']
        final_predicted_state['num_diverged'] = np.array([0.0]) if classifier_accept else last_previous_state['num_diverged'] + 1
        return final_predicted_state

    def propagate(self, motions, control, duration, state_out):
        del duration  # unused, multi-step propagation is handled inside propagateMotionsWhileValid

        # Convert from OMPL -> Numpy
        new_action = self.control_to_numpy(control)
        previous_states, previous_actions = self.motions_to_numpy(motions)
        np_final_states = self.predict(previous_states, previous_actions, new_action)

        # Convert back Numpy -> OMPL
        self.compound_from_numpy(np_final_states, state_out)

    def plan(self,
             start_states: Dict,
             environment: Dict,
             goal,
             ) -> PlannerResult:
        """
        :param start_states: each element is a vector
        :type environment: each element is a vector of state which we don't predict
        :param goal:
        :return: controls, states
        """
        self.environment = environment

        # create start and goal states
        ompl_start = ob.CompoundState(self.state_space)
        start_states['stdev'] = np.array([0.0])
        start_states['num_diverged'] = np.array([0.0])
        self.compound_from_numpy(start_states, ompl_start())

        start = ob.State(ompl_start)
        ompl_goal = MyGoalRegion(self.si,
                                 self.params['goal_threshold'],
                                 goal,
                                 self.viz_object,
                                 self.scenario,
                                 self.state_space_description)

        self.ss.clear()
        self.viz_object.clear()
        self.ss.setStartState(start)
        self.ss.setGoal(ompl_goal)

        if self.verbose >= 3:
            plt.figure()
            plt.ion()
            plt.xlim([-3, 3])
            plt.ylim([-3, 3])
            plt.axis("equal")
            plt.show(block=False)
            self.ax = plt.gca()
            plt.imshow(np.flipud(environment['full_env/env']), cmap='Greys', extent=environment['full_env/extent'])
            self.scenario.plot_state(self.ax, state=start_states, color='b', zorder=4, s=10, linewidth=1)
            plt.pause(1)
            input("press enter to continue")

        planner_status = self.ss.solve(self.params['timeout'])

        if planner_status:
            ompl_path = self.ss.getSolutionPath()
            controls_np, planned_path = self.convert_path(ompl_path)
            if self.smoother is not None:
                controls_np, planned_path = self.smooth_path(goal, controls_np, planned_path)
            return PlannerResult(planner_status=planner_status,
                                 path=planned_path,
                                 actions=controls_np)
        return PlannerResult(planner_status=planner_status,
                             path=None,
                             actions=None)

    def state_sampler_allocator(self, state_space):
        # Note: I had issues using RealVectorStateSampler() here...
        if self.params['sampler_type'] == 'random':
            extent = [-self.params['full_env_w'] / 2,
                      self.params['full_env_w'] / 2,
                      -self.params['full_env_h'] / 2,
                      self.params['full_env_h'] / 2]
            # FIXME: need to handle arbitrary state space dictionary/description
            #  this is such a hack
            sampler = ValidRopeConfigurationCompoundSampler(state_space,
                                                            my_planner=self,
                                                            viz_object=self.viz_object,
                                                            extent=extent,
                                                            link_bot_state_idx=self.state_space_description['link_bot']['idx'],
                                                            n_rope_state=self.state_space_description['link_bot']['n_state'],
                                                            rope_length=self.rope_length,
                                                            max_angle_rad=self.params['max_angle_rad'],
                                                            rng=self.state_sampler_rng)
        elif self.params['sampler_type'] == 'sample_train':
            raise NotImplementedError()
            # sampler = TrainingSetCompoundSampler(state_space,
            #                                      self.viz_object,
            #                                      train_dataset=self.training_dataset,
            #                                      sequence_length=self.train_dataset_max_sequence_length,
            #                                      rng=self.state_sampler_rng)
        else:
            raise ValueError("Invalid sampler type {}".format(self.params['sampler_type']))

        return sampler

    def convert_path(self, ompl_path: oc.PathControl):
        planned_path = []
        for time_idx, state in enumerate(ompl_path.getStates()):
            np_state = compound_to_numpy(self.state_space_description, state)
            planned_path.append(np_state)

        np_controls = np.ndarray((ompl_path.getControlCount(), self.n_action))
        for time_idx, (control, duration) in enumerate(zip(ompl_path.getControls(), ompl_path.getControlDurations())):
            # duration is always be 1 for control::RRT, not so for control::SST
            np_controls[time_idx] = ompl_control_to_model_action(control, self.n_action).squeeze()

        return np_controls, planned_path

    def smooth_path(self,
                    goal,
                    controls: np.ndarray,
                    planned_path: List[Dict]):
        smoothed_actions_tf, smoothed_path_tf = self.smoother.smooth(full_env=self.environment['full_env/env'],
                                                                     full_env_origin=self.environment['full_env/origin'],
                                                                     res=self.environment['full_env/res'],
                                                                     goal=goal,
                                                                     actions=controls,
                                                                     planned_path=planned_path)
        smoothed_path = []
        for state_tf in smoothed_path_tf:
            state_np = {}
            for k, v in state_tf.items():
                state_np[k] = v.numpy()
            smoothed_path.append(state_np)

        return smoothed_actions_tf.numpy(), smoothed_path


def interpret_planner_status(planner_status: ob.PlannerStatus, verbose: int = 0):
    if verbose >= 1:
        # If the planner failed, print the error
        if not planner_status:
            print(Fore.RED + planner_status.asString() + Fore.RESET)
