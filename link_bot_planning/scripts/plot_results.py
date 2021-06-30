#!/usr/bin/env python
import argparse
import pathlib

from arc_utilities import ros_init
from link_bot_planning.analysis import results_utils
from link_bot_planning.analysis.results_utils import classifier_params_from_planner_params, plot_steps
from link_bot_planning.plan_and_execute import TrialStatus
from link_bot_pycommon.args import int_set_arg


@ros_init.with_ros("plot_results")
def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("results_dir", type=pathlib.Path, help='directory containing metrics.json')
    parser.add_argument("--trials", type=int_set_arg, help='which plan(s) to show')
    parser.add_argument("--threshold", type=float)
    parser.add_argument("--full-plan", action='store_true')
    parser.add_argument("--only-timeouts", action='store_true')
    parser.add_argument("--only-reached", action='store_true')
    parser.add_argument("--verbose", '-v', action="count", default=0)

    args = parser.parse_args()

    try:
        scenario, metadata = results_utils.get_scenario_and_metadata(args.results_dir)
    except RuntimeError:
        args.results_dir = next(args.results_dir.iterdir())
        scenario, metadata = results_utils.get_scenario_and_metadata(args.results_dir)

    classifier_params = classifier_params_from_planner_params(metadata['planner_params'])
    if args.threshold is None:
        threshold = classifier_params['classifier_dataset_hparams']['labeling_params']['threshold']
    else:
        threshold = args.threshold

    for trial_idx, datum in results_utils.trials_generator(args.results_dir, args.trials):
        trial_status = datum['trial_status']
        should_skip = (args.only_timeouts and trial_status == TrialStatus.Reached or
                       args.only_reached and trial_status != TrialStatus.Reached)
        if should_skip:
            continue

        print(f"trial {trial_idx}, status {trial_status}")
        plot_steps(scenario, datum, metadata, {'threshold': threshold}, args.verbose, args.full_plan)


if __name__ == '__main__':
    main()
