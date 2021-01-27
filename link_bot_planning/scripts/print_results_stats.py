#!/usr/bin/env python
import argparse
import csv
import json

import colorama
from progressbar import progressbar

import rospy
from link_bot_planning.results_metrics import *
from link_bot_pycommon.args import my_formatter
from link_bot_pycommon.get_scenario import get_scenario
from link_bot_pycommon.serialization import load_gzipped_pickle


def metrics_main(args):
    with (args.results_dir / 'metadata.json').open('r') as metadata_file:
        metadata_str = metadata_file.read()
        metadata = json.loads(metadata_str)
    scenario = get_scenario(metadata['scenario'])

    rows = []
    metrics_filenames = list(args.results_dir.glob("*_metrics.pkl.gz"))
    for metrics_filename in progressbar(metrics_filenames):
        datum = load_gzipped_pickle(metrics_filename)
        status = datum['trial_status']
        trial_idx = datum['trial_idx']
        end_state = datum['end_state']
        goal = datum['goal']
        task_error = scenario.distance_to_goal(end_state, goal).numpy()
        used_recovery = np.any([step['type'] == 'executed_recovery' for step in datum['steps']])
        row = [trial_idx, status.name, f'{task_error:.3f}', int(used_recovery)]
        rows.append(row)

    rows = sorted(rows)

    for row in rows:
        print("\t".join([str(x) for x in row]))

    with open(args.results_dir / 'results_stats.txt', 'w') as outfile:
        writer = csv.writer(outfile)
        writer.writerows(rows)


def main():
    colorama.init(autoreset=True)

    rospy.init_node("print_results_stats")
    np.set_printoptions(suppress=True, precision=4, linewidth=180)

    parser = argparse.ArgumentParser(formatter_class=my_formatter)
    parser.add_argument('results_dir', help='results directory', type=pathlib.Path)
    parser.add_argument('--table-format', help='table format', type=str, choices=['plain', 'fancy_grid'])

    args = parser.parse_args()

    metrics_main(args)


if __name__ == '__main__':
    main()