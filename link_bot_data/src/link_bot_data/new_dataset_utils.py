import pathlib
import pickle

from link_bot_pycommon.serialization import load_gzipped_pickle
from moonshine.filepath_tools import load_hjson

UNUSED_COMPAT = None


def load_mode_filenames(d: pathlib.Path, filenames_filename: pathlib.Path):
    with filenames_filename.open("r") as filenames_file:
        filenames = [l.strip("\n") for l in filenames_file.readlines()]
    return [d / p for p in filenames]


def get_filenames(dataset_dirs, mode: str):
    all_filenames = []
    for d in dataset_dirs:
        if mode == 'all':
            all_filenames.extend(load_mode_filenames(d, d / f'train.txt'))
            all_filenames.extend(load_mode_filenames(d, d / f'test.txt'))
            all_filenames.extend(load_mode_filenames(d, d / f'val.txt'))
        else:
            filenames_filename = d / f'{mode}.txt'
            all_filenames.extend(load_mode_filenames(d, filenames_filename))
    all_filenames = sorted(all_filenames)
    return all_filenames


def load_single(metadata_filename: pathlib.Path):
    metadata = load_metadata(metadata_filename)

    data_filename = metadata.pop("data")
    full_data_filename = metadata_filename.parent / data_filename
    example = load_gzipped_pickle(full_data_filename)
    example.update(metadata)
    example['metadata'] = metadata
    return example


def load_metadata(metadata_filename):
    if 'hjson' in metadata_filename.name:
        metadata = load_hjson(metadata_filename)
    elif 'pkl' in metadata_filename.name:
        with metadata_filename.open("rb") as f:
            metadata = pickle.load(f)
    else:
        raise NotImplementedError()
    metadata['filename'] = metadata_filename.stem
    return metadata
