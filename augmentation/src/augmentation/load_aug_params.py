import pathlib

from arc_utilities.algorithms import nested_dict_update
from moonshine.filepath_tools import load_hjson


def load_aug_params(args):
    hparams_filename = pathlib.Path("aug_hparams/common.hjson")
    common_hparams = load_hjson(hparams_filename)
    hparams = load_hjson(args.hparams)
    hparams = nested_dict_update(common_hparams, hparams)
    return hparams

