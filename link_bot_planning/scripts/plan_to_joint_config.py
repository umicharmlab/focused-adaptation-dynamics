import argparse
import rospy
import json
from peter_msgs.srv import SetBool, SetBoolRequest
from moveit_msgs.msg import MoveGroupAction, MoveGroupGoal, MotionPlanRequest, Constraints, JointConstraint, MoveItErrorCodes
from peter_msgs.srv import WorldControl, WorldControlRequest
import actionlib


def main():
    rospy.init_node("plan_to_joint_config")

    parser = argparse.ArgumentParser()
    parser.add_argument("configs")
    parser.add_argument("name", type=str)
    args = parser.parse_args()

    client = actionlib.SimpleActionClient('move_group', MoveGroupAction)
    client.wait_for_server()

    with open(args.configs, "r") as configs_file:
        configs = json.load(configs_file)

    config = configs[args.name]
    positions = config['position']
    names = config['name']

    goal_config_constraint = Constraints()
    for name, position in zip(names, positions):
        joint_constraint = JointConstraint()
        joint_constraint.joint_name = name
        joint_constraint.position = position
        goal_config_constraint.joint_constraints.append(joint_constraint)

    req = MotionPlanRequest()
    req.group_name = 'both_arms'
    req.goal_constraints.append(goal_config_constraint)

    goal = MoveGroupGoal()
    goal.request = req
    client.send_goal(goal)
    client.wait_for_result()
    result = client.get_result()
    if result.error_code.val != MoveItErrorCodes.SUCCESS:
        print("Error! code " + str(result.error_code.val))
    else:
        print("Success!")

if __name__ == "__main__":
    main()