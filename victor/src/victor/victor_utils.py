from typing import Tuple

import rospy
import std_msgs
from link_bot_gazebo.srv import ExecuteActionRequest

from std_srvs.srv import EmptyRequest

from link_bot_pycommon.ros_pycommon import Services


class VictorServices(Services):
    def __init__(self):
        super().__init__()

        self.get_rope = rospy.ServiceProxy('/cdcpd/tracked_points', None)

        self.services_to_wait_for.extend([
            '/cdcpd/tracked_points'
        ])

    def reset_world(self, verbose, reset_gripper_to: Tuple[float]):
        empty = EmptyRequest()
        self.reset.call(empty)


def setup_env(verbose: int,
              reset_world: bool = True):
    # fire up services
    services = VictorServices()
    services.wait(verbose)

    if reset_world:
        services.reset_world(verbose, [])

    # first the controller
    stop = ExecuteActionRequest()
    stop.action.gripper1_delta_pos.x = 0
    stop.action.gripper1_delta_pos.y = 0
    stop.action.max_time_per_step = 1.0
    services.execute_action(stop)

    return services
