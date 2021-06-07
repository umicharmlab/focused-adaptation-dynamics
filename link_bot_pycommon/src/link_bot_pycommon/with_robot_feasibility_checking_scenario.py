from link_bot_pycommon.dual_arm_real_val_rope_scenario import DualArmRealValRopeScenario
from link_bot_pycommon.dual_arm_rope_with_robot_feasibility_checking_scenario import \
    DualArmRopeWithRobotFeasibilityCheckingScenario
from link_bot_pycommon.dual_arm_sim_rope_scenario import SimValDualArmRopeScenario


class DualArmRopeSimValWithRobotFeasibilityCheckingScenario(DualArmRopeWithRobotFeasibilityCheckingScenario,
                                                            SimValDualArmRopeScenario):
    def __init__(self):
        SimValDualArmRopeScenario.__init__(self)
        DualArmRopeWithRobotFeasibilityCheckingScenario.__init__(self, self.robot_namespace)


class DualArmRopeRealValWithRobotFeasibilityCheckingScenario(DualArmRopeWithRobotFeasibilityCheckingScenario,
                                                             DualArmRealValRopeScenario):
    def __init__(self):
        DualArmRealValRopeScenario.__init__(self)
        DualArmRopeWithRobotFeasibilityCheckingScenario.__init__(self, self.robot_namespace)