from typing import Dict

import ompl.control as oc

from link_bot_classifiers.base_constraint_checker import BaseConstraintChecker
from link_bot_pycommon.experiment_scenario import ExperimentScenario
from link_bot_planning.my_planner import MyPlanner
from link_bot_planning.viz_object import VizObject
from link_bot_pycommon.base_services import BaseServices
from state_space_dynamics.base_dynamics_function import BaseDynamicsFunction


class NearestRRT(MyPlanner):

    def __init__(self,
                 fwd_model: BaseDynamicsFunction,
                 classifier_model: BaseConstraintChecker,
                 planner_params: Dict,
                 service_provider: BaseServices,
                 scenario: ExperimentScenario,
                 viz_object: VizObject,
                 seed: int,
                 verbose: int):
        super().__init__(fwd_model,
                         classifier_model,
                         planner_params,
                         service_provider,
                         scenario,
                         viz_object,
                         seed,
                         verbose)

        self.planner = oc.RRT(self.si)
        self.planner.setIntermediateStates(True)  # this is necessary, because we use this to generate datasets
        self.ss.setPlanner(self.planner)
        self.si.setPropagationStepSize(self.fwd_model.dt)
        self.si.setMinMaxControlDuration(1, 50)
