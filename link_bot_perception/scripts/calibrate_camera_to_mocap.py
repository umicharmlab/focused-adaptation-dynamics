import argparse
import logging

import numpy as np
import transformations
from scipy.spatial.transform import Rotation as R
from tqdm import trange

import rospy
from arc_utilities import ros_init
from arc_utilities.tf2wrapper import TF2Wrapper

logger = logging.getLogger(__file__)


def average_transformation_matrices(offsets):
    rots = R.from_matrix([m[:3, :3] for m in offsets])
    mean_rot = rots.mean().as_matrix()
    mean = np.mean(offsets, axis=0)
    mean[:3, :3] = mean_rot
    return mean


@ros_init.with_ros("calibrate_camera_to_mocap")
def main():
    np.set_printoptions(suppress=True, precision=4, linewidth=200)

    parser = argparse.ArgumentParser()
    parser.add_argument('camera_tf_name', help='name of the camera in mocap according to TF')

    args = parser.parse_args()

    tf = TF2Wrapper()

    mocap_world_frame = 'mocap_world'

    offsets = []
    fiducial_center_to_marker_corner = np.sqrt(0.118 ** 2 / 2)
    i = 0
    for t in trange(10):
        fiducial_center_to_fiducial_mocap = transformations.compose_matrix(
            translate=[-fiducial_center_to_marker_corner, fiducial_center_to_marker_corner, 0])
        for _ in range(3):
            # send TF from fiducial to mocap markers on the fiducial board
            tf.send_transform_matrix(fiducial_center_to_fiducial_mocap, f"fiducial_{i}", f"fiducial_{i}_mocap_markers")
            rospy.sleep(0.1)

        mocap2fiducial_markers = tf.get_transform(mocap_world_frame, f"mocap_calib{i}_calib{i}")
        # TODO: make sure this is right
        mocap2fiducial = mocap2fiducial_markers @ transformations.inverse_matrix(fiducial_center_to_fiducial_mocap)
        camera2fiducial = tf.get_transform(args.camera_tf_name, f"fiducial_{i}")
        fiducial2camera = transformations.inverse_matrix(camera2fiducial)
        mocap2camera_sensor_detected = mocap2fiducial @ fiducial2camera
        mocap2camera_markers = tf.get_transform(mocap_world_frame, args.camera_tf_name)
        mocap2camera_sensor_offset = np.linalg.solve(mocap2camera_markers, mocap2camera_sensor_detected)

        offsets.append(mocap2camera_sensor_offset)

        for _ in range(3):
            # these are for debugging
            tf.send_transform_matrix(mocap2fiducial, mocap_world_frame, f'mocap_fiducial_{i}_{t}')
            tf.send_transform_matrix(camera2fiducial, args.camera_tf_name, f'fiducial_{i}_{t}')
            tf.send_transform_matrix(mocap2camera_markers, mocap_world_frame, f'camera_a_{t}')
            tf.send_transform_matrix(mocap2camera_sensor_detected, mocap_world_frame, f'camera_b_{t}')
            rospy.sleep(0.2)
        q = input("press enter to capture")
        if q == 'q':
            break

    average_offset = average_transformation_matrices(offsets)
    trans = transformations.translation_from_matrix(average_offset)
    rot = transformations.euler_from_matrix(average_offset)
    roll, pitch, yaw = rot
    print('Copy This into the static_transform_publisher')
    print(f'{trans[0]:.5f} {trans[1]:.5f} {trans[2]:.5f} {yaw:.5f} {pitch:.5f} {roll:.5f}')
    print("NOTE: tf2_ros static_transform_publisher uses Yaw, Pitch, Roll so that's what is printed above")


if __name__ == '__main__':
    main()
