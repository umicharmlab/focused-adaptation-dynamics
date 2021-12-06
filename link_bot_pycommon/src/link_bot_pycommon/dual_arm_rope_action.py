import warnings
from typing import Dict

import ros_numpy
import rospy
from actionlib_msgs.msg import GoalStatus
from arc_utilities.tf2wrapper import TF2Wrapper
from link_bot_pycommon.base_dual_arm_rope_scenario import joint_state_msg_from_state_dict
from link_bot_pycommon.point_to_robot import point_to_root
from peter_msgs.srv import GetOverstretching, GetOverstretchingResponse, GetOverstretchingRequest
from rosgraph.names import ns_join
from sensor_msgs.msg import JointState

with warnings.catch_warnings():
    warnings.simplefilter("ignore", category=RuntimeWarning)
    from arm_robots.robot import MoveitEnabledRobot


def dual_arm_rope_execute_action(robot: MoveitEnabledRobot, tf_wrapper: TF2Wrapper, environment: Dict, state: Dict,
                                 action: Dict,
                                 check_overstretching=True):
    tool_names = [robot.left_tool_name, robot.right_tool_name]

    start_left_gripper_pos, start_right_gripper_pos = robot.get_gripper_positions()

    left_gripper_point = point_to_root(robot, tf_wrapper, action['left_gripper_position'], 'hdt_michigan_root')
    right_gripper_point = point_to_root(robot, tf_wrapper, action['right_gripper_position'], 'hdt_michigan_root')
    grippers = [[left_gripper_point], [right_gripper_point]]

    if check_overstretching:
        overstretching_srv = rospy.ServiceProxy(ns_join("rope_3d", "rope_overstretched"), GetOverstretching)
        res: GetOverstretchingResponse = overstretching_srv(GetOverstretchingRequest())

        if res.magnitude > 1.06:
            # just do nothing...
            rospy.logwarn("The rope is extremely overstretched -- refusing to execute action")
            return (end_trial := True)

    if check_overstretching:
        def _stop_condition(_):
            return overstretching_stop_condition()
    else:
        _stop_condition = None

    joint_state = joint_state_msg_from_state_dict(state)
    result = robot.follow_jacobian_to_position_from_scene_and_state(group_name="both_arms",
                                                                    scene_msg=environment['scene_msg'],
                                                                    joint_state=joint_state,
                                                                    tool_names=tool_names,
                                                                    points=grippers,
                                                                    stop_condition=_stop_condition)

    if check_overstretching:
        rospy.sleep(1.0)
        res: GetOverstretchingResponse = overstretching_srv(GetOverstretchingRequest())
        overstretched = res.overstretched
    else:
        overstretched = False
    if result.execution_result.action_client_state == GoalStatus.PREEMPTED or overstretched:
        post_action_joint_positions = robot.get_joint_positions(state['joint_names'])
        post_action_joint_state = JointState(name=state['joint_names'], position=post_action_joint_positions)

        rev_grippers = [[ros_numpy.numpify(start_left_gripper_pos)],
                        [ros_numpy.numpify(start_right_gripper_pos)]]
        robot.follow_jacobian_to_position_from_scene_and_state(group_name="both_arms",
                                                               scene_msg=environment['scene_msg'],
                                                               joint_state=post_action_joint_state,
                                                               tool_names=tool_names,
                                                               points=rev_grippers)

    return (end_trial := False)


def overstretching_stop_condition():
    overstretching_srv = rospy.ServiceProxy(ns_join("rope_3d", "rope_overstretched"), GetOverstretching)
    res: GetOverstretchingResponse = overstretching_srv(GetOverstretchingRequest())
    return res.overstretched
