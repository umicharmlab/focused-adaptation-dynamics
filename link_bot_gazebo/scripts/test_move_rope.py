#!/usr/bin/env python
import numpy as np
import ros_numpy
import rospy
from arc_utilities import ros_init
from geometry_msgs.msg import Point, Pose, Quaternion
from link_bot_gazebo.gazebo_services import gz_scope
from link_bot_gazebo.position_3d import Position3D
from peter_msgs.srv import RegisterPosition3DControllerRequest, Position3DWaitRequest, Pose3DActionRequest
from tf.transformations import quaternion_from_euler


@ros_init.with_ros("test_move_rope")
def main():
    pos3d = Position3D()

    ROPE_NAMESPACE = 'stiff_rope'

    register_left_req = RegisterPosition3DControllerRequest()
    register_left_req.scoped_link_name = gz_scope(ROPE_NAMESPACE, "left_gripper")
    register_left_req.controller_type = "kinematic"
    register_left_req.position_only = False
    pos3d.register(register_left_req)
    # register_right_req = RegisterPosition3DControllerRequest()
    # register_right_req.scoped_link_name = gz_scope(ROPE_NAMESPACE, "right_gripper")
    # register_right_req.controller_type = "kinematic"
    # register_right_req.position_only = False
    # pos3d.register(register_right_req)

    def move(x=0, y=0, z=0, roll=0, pitch=0, yaw=0):
        right_orientation = ros_numpy.msgify(Quaternion, quaternion_from_euler(roll, pitch, yaw))
        left_orientation = ros_numpy.msgify(Quaternion, quaternion_from_euler(roll, pitch + np.pi, yaw))
        left_req = Pose3DActionRequest(speed_mps=0.1,
                                       speed_rps=5,
                                       tolerance_rad=0.1,
                                       scoped_link_name=gz_scope(ROPE_NAMESPACE, 'left_gripper'),
                                       pose=Pose(position=Point(x, y, z), orientation=left_orientation))
        right_req = Pose3DActionRequest(speed_mps=0.1,
                                        speed_rps=5,
                                        tolerance_rad=0.1,
                                        scoped_link_name=gz_scope(ROPE_NAMESPACE, 'right_gripper'),
                                        pose=Pose(position=Point(x, y + 0.3, z), orientation=right_orientation))
        pos3d.pose_set(left_req)
        # pos3d.pose_set(right_req)

        wait_req = Position3DWaitRequest()
        wait_req.timeout_s = 60.0
        wait_req.scoped_link_names.append(gz_scope(ROPE_NAMESPACE, 'left_gripper'))
        # wait_req.scoped_link_names.append(gz_scope(ROPE_NAMESPACE, 'right_gripper'))
        pos3d.wait(wait_req)

    move(pitch=-np.pi)
    move(pitch=-0.0)


if __name__ == "__main__":
    main()
