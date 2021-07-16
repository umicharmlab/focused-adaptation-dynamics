#!/usr/bin/env python
import argparse

import tabulate
from dynamo_pandas import get_df

from analysis.analyze_results import generate_tables, make_table_specs
from link_bot_data import dynamodb_utils
from link_bot_pycommon.pandas_utils import df_where


def make_tables_specs(column_name: str, metric_name: str, table_format: str):
    groupby = [
        "do_augmentation",
        "fine_tuning_take",
        "classifier_source_env",
        "dataset_dirs",
        "mode",
        "balance",
        "fine_tuning_dataset_dirs",
    ]
    tables_config = [
        {
            'type':       'MyTable',
            'name':       f'{metric_name} mean',
            'header':     [
                'Classifier Source Env',
                'N',
                'Dataset',
                'Aug?',
                'Fine-Tuning Take',
                column_name,
            ],
            'reductions': [
                [[groupby, "classifier_source_env", "first"]],
                [[groupby, "classifier_source_env", "count"]],
                [[groupby, "dataset_dirs", "first"]],
                [[groupby, "do_augmentation", "first"]],
                [[groupby, "fine_tuning_take", "first"]],
                [[groupby, metric_name, "mean"]],
            ],
        },
        {
            'type':       'PValuesTable',
            'name':       f'{metric_name} pvalues',
            'reductions': [
                [[groupby, metric_name, None]],
            ],

        }
    ]
    return make_table_specs(table_format, tables_config)


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--debug')
    parser.add_argument('--latex')
    args = parser.parse_args()

    if args.latex:
        table_format = 'latex_raw'
    else:
        table_format = tabulate.simple_separated_format("\t")

    df = get_df(table=dynamodb_utils.classifier_table(args.debug))

    df = filter_df_for_experiment(df)

    cld = '/media/shared/classifier_data/'

    random_actions_table_specs = make_tables_specs('Random Actions Spec', 'accuracy on negatives', table_format)
    df_random_actions = df_where(df, 'dataset_dirs', cld + 'val_car_feasible_1614981888+op2')
    generate_tables(df=df_random_actions, outdir=None, table_specs=random_actions_table_specs)

    no_classifier_table_specs = make_tables_specs('No Classifier Spec', 'accuracy on negatives', table_format)
    df_no_classifier = df_where(df, 'dataset_dirs', cld + 'car_no_classifier_eval')
    generate_tables(df=df_no_classifier, outdir=None, table_specs=no_classifier_table_specs)

    heuristic_rejected_table_specs = make_tables_specs('Heuristic Rejected Spec', 'accuracy on negatives', table_format)
    df_heuristic_rejected = df_where(df, 'dataset_dirs', cld + 'car_heuristic_classifier_eval2')
    generate_tables(df=df_heuristic_rejected, outdir=None, table_specs=heuristic_rejected_table_specs)


def filter_df_for_experiment(df):
    # just some nicknames
    experiment_type = 'online'
    df = df.loc[df['mode'] == 'all']
    cond1 = (df['fine_tuning_dataset_dirs'] == '/media/shared/classifier_data/val_car_feasible_1614981888+op2')
    cond2 = df['fine_tuning_dataset_dirs'].isna()
    df = df.loc[cond1 | cond2]
    print(experiment_type)
    if experiment_type == 'online':
        cond1 = (df['fine_tuning_take'] == 500)
        cond2 = (df['fine_tuning_take'].isna() & (~df['do_augmentation']))
        df = df.loc[cond1 | cond2]
    elif experiment_type == 'take10':
        drop_indices = df.index[(df['fine_tuning_take'] != 10) & df['do_augmentation']]
        df.drop(drop_indices, inplace=True)
    elif experiment_type == 'full':
        df = df.loc[df['fine_tuning_take'].isna()]
    return df


if __name__ == '__main__':
    main()
