#!/usr/bin/env python
import argparse
import itertools
import pathlib
import warnings
from time import perf_counter

import rospkg
from more_itertools import chunked

from link_bot_data.base_collect_dynamics_data import collect_dynamics_data
from link_bot_gazebo import gazebo_utils
from link_bot_planning.test_scenes import get_all_scene_indices
from link_bot_pycommon.pycommon import pathify
from moonshine.gpu_config import limit_gpu_mem
from moonshine.magic import wandb_lightning_magic

with warnings.catch_warnings():
    warnings.simplefilter("ignore", category=RuntimeWarning)
    from ompl import util as ou

from colorama import Fore

from arc_utilities import ros_init
from link_bot_data.new_dataset_utils import fetch_udnn_dataset
from link_bot_data.wandb_datasets import wandb_save_dataset
from link_bot_planning.planning_evaluation import evaluate_planning, load_planner_params
from link_bot_planning.results_to_dynamics_dataset import ResultsToDynamicsDataset
from link_bot_pycommon.job_chunking import JobChunker
from mde import train_test_mde
from mde.make_mde_dataset import make_mde_dataset
from state_space_dynamics import train_test_dynamics

limit_gpu_mem(None)  # just in case TF is used somewhere


@ros_init.with_ros("save_as_test_scene")
def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("nickname")
    parser.add_argument("--on-exception", default='retry')

    args = parser.parse_args()

    ou.setLogLevel(ou.LOG_ERROR)
    wandb_lightning_magic()

    root = pathlib.Path("results/online_adaptation")
    outdir = root / args.nickname
    outdir.mkdir(exist_ok=True, parents=True)
    print(Fore.YELLOW + "Output directory: {}".format(outdir) + Fore.RESET)

    r = rospkg.RosPack()
    dynamics_pkg_dir = pathlib.Path(r.get_path('state_space_dynamics'))
    data_pkg_dir = pathlib.Path(r.get_path('link_bot_data'))
    mde_pkg_dir = pathlib.Path(r.get_path('mde'))

    logfile_name = root / args.nickname / 'logfile.hjson'
    job_chunker = JobChunker(logfile_name)

    method_name = job_chunker.load_prompt('method_name', 'adaptation')
    unadapted_run_id = job_chunker.load_prompt('unadapted_run_id', 'sim_rope_unadapted-dme7l')
    seed = int(job_chunker.load_prompt('seed', 0))
    collect_data_params_filename = job_chunker.load_prompt_filename('collect_data_params_filename',
                                                                    'collect_dynamics_params/floating_rope_100.hjson')
    collect_data_params_filename = data_pkg_dir / collect_data_params_filename
    planner_params_filename = job_chunker.load_prompt_filename('planner_params_filename',
                                                               'planner_configs/val_car/mde_online_learning.hjson')
    planner_params = load_planner_params(planner_params_filename)
    test_scenes_dir = job_chunker.load_prompt_filename('test_scenes_dir', 'test_scenes/car4_alt')
    iterations = int(job_chunker.load_prompt('iterations', 100))
    n_trials_per_iteration = int(job_chunker.load_prompt('n_trials_per_iteration', 10))

    if method_name == 'adaptation':
        dynamics_params_filename = dynamics_pkg_dir / "hparams" / "iterative_lowest_error_soft_all.hjson"
    elif method_name == 'all_data':
        dynamics_params_filename = dynamics_pkg_dir / "hparams" / "all_data.hjson"
    elif method_name == 'no_adaptation':
        dynamics_params_filename = None
    else:
        raise NotImplementedError(f'Unknown method name {method_name}')

    mde_params_filename = mde_pkg_dir / "hparams" / "rope.hjson"

    all_trial_indices = list(get_all_scene_indices(test_scenes_dir))
    trial_indices_generator = chunked(itertools.cycle(all_trial_indices), n_trials_per_iteration)

    gazebo_utils.suspend()  # most code runs faster if gazebo is suspended

    # initialize with unadapted model
    dynamics_dataset_dirs = []
    mde_dataset_dirs = []
    for i in range(iterations):
        print(Fore.CYAN + f"Iteration {i}" + Fore.RESET)

        sub_chunker_i = job_chunker.sub_chunker(f'iter{i}')
        planning_job_chunker = sub_chunker_i.sub_chunker("planning")

        classifiers = [pathlib.Path("cl_trials/new_feasibility_baseline/none")]
        if i != 0:
            prev_sub_chunker = job_chunker.sub_chunker(f'iter{i - 1}')
            prev_mde_run_id = prev_sub_chunker.get("mde_run_id")
            prev_dynamics_run_id = prev_sub_chunker.get("dynamics_run_id")
            classifiers.append(f'p:{prev_mde_run_id}')
        else:
            prev_dynamics_run_id = unadapted_run_id

        planning_outdir = pathify(planning_job_chunker.get('planning_outdir'))
        if i == 0:
            planning_trials = None
        else:
            planning_trials = next(trial_indices_generator)  # must call every time or it won't be reproducible
        if planning_outdir is None:
            t0 = perf_counter()
            planning_outdir = outdir / 'planning_results' / f'iteration_{i}'
            planning_outdir.mkdir(exist_ok=True, parents=True)
            planner_params["classifier_model_dir"] = classifiers
            planner_params['fwd_model_dir'] = f'p:{prev_dynamics_run_id}'
            gazebo_utils.resume()
            if i == 0:
                dynamics_dataset_dir_i = None
                for dynamics_dataset_dir_i, _ in collect_dynamics_data(collect_data_params_filename,
                                                                       n_trajs=10,
                                                                       root=outdir,
                                                                       nickname=f'{args.nickname}_dynamics_dataset_{i}',
                                                                       seed=seed):
                    pass
                wandb_save_dataset(dynamics_dataset_dir_i, 'udnn', entity='armlab')
                dynamics_dataset_name = dynamics_dataset_dir_i.name
                sub_chunker_i.store_result('dynamics_dataset_name', dynamics_dataset_name)
            else:
                evaluate_planning(planner_params=planner_params,
                                  job_chunker=planning_job_chunker,
                                  outdir=planning_outdir,
                                  test_scenes_dir=test_scenes_dir,
                                  trials=planning_trials,
                                  seed=seed,
                                  how_to_handle=args.on_exception)
            gazebo_utils.suspend()
            planning_job_chunker.store_result('planning_outdir', planning_outdir.as_posix())
            dt = perf_counter() - t0
            planning_job_chunker.store_result('planning_outdir_dt', dt)

        # convert the planning results to a dynamics dataset
        dynamics_dataset_name = sub_chunker_i.get("dynamics_dataset_name")
        if dynamics_dataset_name is None:
            t0 = perf_counter()
            r = ResultsToDynamicsDataset(results_dir=planning_outdir,
                                         outname=f'{args.nickname}_dynamics_dataset_{i}',
                                         root=outdir / 'dynamics_datasets',
                                         traj_length=100,
                                         visualize=False)
            dynamics_dataset_dir_i = r.run()
            wandb_save_dataset(dynamics_dataset_dir_i, project='udnn')
            dynamics_dataset_name = dynamics_dataset_dir_i.name
            sub_chunker_i.store_result('dynamics_dataset_name', dynamics_dataset_name)
            dt = perf_counter() - t0
            planning_job_chunker.store_result('dynamics_dataset_name_dt', dt)

        dynamics_dataset_dirs.append(dynamics_dataset_name)

        dynamics_run_id = sub_chunker_i.get(f"dynamics_run_id")
        if dynamics_run_id is None:
            if dynamics_params_filename is not None:
                t0 = perf_counter()
                dynamics_run_id = train_test_dynamics.fine_tune_main(dataset_dir=dynamics_dataset_dirs,
                                                                     checkpoint=prev_dynamics_run_id,
                                                                     params_filename=dynamics_params_filename,
                                                                     batch_size=32,
                                                                     steps=10_000,
                                                                     epochs=-1,
                                                                     repeat=100,
                                                                     seed=seed,
                                                                     nickname=f'{args.nickname}_udnn_{i}',
                                                                     user='armlab')
                print(f'{dynamics_run_id=}')
                sub_chunker_i.store_result(f"dynamics_run_id", dynamics_run_id)
                dt = perf_counter() - t0
                planning_job_chunker.store_result('fine_tune_dynamics_dt', dt)

        mde_dataset_name = pathify(sub_chunker_i.get('mde_dataset_name'))
        if mde_dataset_name is None:
            t0 = perf_counter()
            mde_dataset_name = f'{args.nickname}_mde_dataset_{i}'
            mde_dataset_outdir = outdir / 'mde_datasets' / mde_dataset_name
            mde_dataset_outdir.mkdir(parents=True, exist_ok=True)
            make_mde_dataset(dataset_dir=fetch_udnn_dataset(dynamics_dataset_name),
                             checkpoint=dynamics_run_id,
                             outdir=mde_dataset_outdir,
                             step=999)
            sub_chunker_i.store_result('mde_dataset_name', mde_dataset_name)
            dt = perf_counter() - t0
            planning_job_chunker.store_result('make_mde_dataset_dt', dt)
        mde_dataset_dirs.append(mde_dataset_name)

        mde_run_id = sub_chunker_i.get('mde_run_id')
        if mde_run_id is None:
            t0 = perf_counter()
            mde_run_id = train_test_mde.train_main(dataset_dir=mde_dataset_dirs,
                                                   params_filename=mde_params_filename,
                                                   batch_size=4,
                                                   epochs=-1,
                                                   steps=i * 1_000 + 10_000,
                                                   train_mode='all',
                                                   val_mode='all',
                                                   seed=seed,
                                                   user='armlab',
                                                   nickname=f'{args.nickname}_mde_{i}')
            sub_chunker_i.store_result('mde_run_id', mde_run_id)
            dt = perf_counter() - t0
            planning_job_chunker.store_result('fine_tune_mde_dt', dt)


if __name__ == '__main__':
    main()