#!/usr/bin/env python
import argparse
from time import sleep

import rospy
from arc_utilities import ros_init
from link_bot_gazebo_python.gazebo_services import GazeboServices
from roslaunch.pmon import ProcessListener


@ros_init.with_ros("relaunch_gazebo")
def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('launch')
    parser.add_argument('world')
    parser.add_argument('--gui', action='store_true')

    args = parser.parse_args()

    launch_params = {
        'launch': args.launch,
        'world':  args.world,
    }

    service_provider = GazeboServices()
    listener = ProcessListener()

    gazebo_is_dead = False

    def _on_process_died(process_name: str, exit_code: int):
        nonlocal gazebo_is_dead
        gazebo_is_dead = True
        rospy.logerr(f"Process {process_name} exited with code {exit_code}")

    listener.process_died = _on_process_died

    while True:
        service_provider.launch(launch_params, gui=args.gui, world=launch_params['world'])
        sleep(5)
        service_provider.play()

        gazebo_is_dead = False

        service_provider.gazebo_process.pm.add_process_listener(listener)

        while not gazebo_is_dead:
            sleep(1)

        service_provider.kill()
        sleep(5)


if __name__ == "__main__":
    main()
