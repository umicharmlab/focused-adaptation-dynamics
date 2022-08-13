import os
import pathlib
import subprocess
from time import sleep
from typing import Dict, List

import more_itertools


def online_parallel_planning(planner_params: Dict,
                             dynamics: str,
                             mde: str,
                             outdir: pathlib.Path,
                             test_scenes_dir: pathlib.Path,
                             method_name: str,
                             trials: List[int],
                             seed,
                             how_to_handle: str,
                             n_parallel: int,
                             world: str):
    planning_processes = []
    port_num = 42000

    for process_idx, trials_iterable in enumerate(more_itertools.divide(n_parallel, trials)):
        trials_strs = [str(trials_i) for trials_i in trials_iterable]
        trials_set = ','.join(trials_strs)
        print(process_idx, trials_set)

        stdout_filename = outdir / f'{process_idx}.stdout'
        stdout_file = stdout_filename.open("w")
        print(f"Writing stdout/stderr to {stdout_filename}")

        env = os.environ.copy()
        env["GAZEBO_MASTER_URI"] = f"http://localhost:{port_num}"
        env["ROS_MASTER_URI"] = f"http://localhost:{port_num + 1}"

        sim_cmd = ["roslaunch", "link_bot_gazebo", "val.launch", "gui:=false", f"world:={world}"]
        print("starting sim", process_idx)
        subprocess.Popen(sim_cmd, env=env, stdout=stdout_file, stderr=stdout_file)

        sleep(30)

        planning_cmd = [
            "python",
            "scripts/planning_evaluation2.py",
            planner_params,
            test_scenes_dir.as_posix(),
            outdir.as_posix(),
            dynamics,
            mde,
            f"--trials={trials_set}",
            f"--on-exception={how_to_handle}",
            f"--seed={seed}",
            f'--method-name={method_name}',
        ]
        port_num += 2
        print(f"starting planning {process_idx} for trials {trials_set}")
        planning_process = subprocess.Popen(planning_cmd, env=env, stdout=stdout_file, stderr=stdout_file)

        planning_processes.append(planning_process)

    for planning_process in planning_processes:
        planning_process.wait()
