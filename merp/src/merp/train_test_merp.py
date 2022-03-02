#!/usr/bin/env python

import pathlib
from datetime import datetime
from typing import Optional

import git
import pytorch_lightning as pl
import torch
import wandb
from pytorch_lightning.loggers import WandbLogger
from torch.utils.data import DataLoader
from torchvision import transforms
from wandb.util import generate_id

from link_bot_data.visualization import init_viz_env, viz_pred_actual_t
from link_bot_pycommon.get_scenario import get_scenario
from link_bot_pycommon.load_wandb_model import load_model_artifact, model_artifact_path
from merp.merp_torch import MERP
from merrrt_visualization.rviz_animation_controller import RvizAnimationController
from moonshine.filepath_tools import load_hjson, load_params
from moonshine.moonshine_utils import get_num_workers
from moonshine.torch_and_tf_utils import add_batch, remove_batch
from moonshine.torch_datasets_utils import take_subset, dataset_skip, my_collate, add_model_error
from moonshine.torchify import torchify
from state_space_dynamics.torch_dynamics_dataset import TorchDynamicsDataset, remove_keys

PROJECT = 'merp'


def prepare_train(batch_size, dataset_dir, take, skip, transform):
    train_dataset = TorchDynamicsDataset(dataset_dir, mode='train', transform=transform)
    train_dataset_take = take_subset(train_dataset, take)
    train_dataset_skip = dataset_skip(train_dataset_take, skip)
    train_dataset_len = len(train_dataset_skip)
    train_loader = DataLoader(train_dataset_skip,
                              batch_size=batch_size,
                              shuffle=True,
                              collate_fn=my_collate,
                              num_workers=get_num_workers(batch_size))
    return train_loader, train_dataset, train_dataset_len


def prepare_validation(batch_size, dataset_dir, no_validate, transform):
    val_loader = None
    val_dataset = TorchDynamicsDataset(dataset_dir, mode='val', transform=transform)
    val_dataset_len = len(val_dataset)
    if val_dataset_len and not no_validate:
        val_loader = DataLoader(val_dataset,
                                batch_size=batch_size,
                                collate_fn=my_collate,
                                num_workers=get_num_workers(batch_size))
    return val_dataset_len, val_loader


def train_main(dataset_dir: pathlib.Path,
               model_params: pathlib.Path,
               batch_size: int,
               epochs: int,
               seed: int,
               user: str,
               steps: int = -1,
               nickname: Optional[str] = None,
               checkpoint: Optional = None,
               take: Optional[int] = None,
               skip: Optional[int] = None,
               no_validate: bool = False,
               project=PROJECT,
               **kwargs):
    pl.seed_everything(seed, workers=True)

    transform = transforms.Compose([remove_keys("scene_msg")])

    train_loader, train_dataset, train_dataset_len = prepare_train(batch_size, dataset_dir, take, skip, transform)
    val_dataset_len, val_loader = prepare_validation(batch_size, dataset_dir, no_validate, transform)

    model_params = load_hjson(model_params)
    model_params['scenario'] = train_dataset.params['scenario']
    model_params['dataset_dir'] = train_dataset.dataset_dir
    model_params['dataset_hparams'] = train_dataset.params
    # add some extra useful info here
    stamp = "{:%B_%d_%H-%M-%S}".format(datetime.now())
    repo = git.Repo(search_parent_directories=True)
    sha = repo.head.object.hexsha[:10]
    model_params['sha'] = sha
    model_params['start-train-time'] = stamp
    model_params['train_dataset_size'] = train_dataset_len
    model_params['val_dataset_size'] = val_dataset_len
    model_params['batch_size'] = batch_size
    model_params['seed'] = seed
    model_params['max_epochs'] = epochs
    model_params['max_steps'] = steps
    model_params['take'] = take
    model_params['mode'] = 'train'
    model_params['checkpoint'] = checkpoint
    model_params['no_validate'] = no_validate

    if checkpoint is None:
        ckpt_path = None
        run_id = generate_id(length=5)
        if nickname is not None:
            run_id = nickname + '-' + run_id
        wandb_kargs = {'entity': user}
    else:
        ckpt_path = model_artifact_path(checkpoint, project, version='latest', user=user)
        run_id = checkpoint
        wandb_kargs = {
            'entity': user,
            'resume': True,
        }

    model = MERP(hparams=model_params, scenario=train_dataset.get_scenario())
    wb_logger = WandbLogger(project=project, name=run_id, id=run_id, log_model='all', **wandb_kargs)
    ckpt_cb = pl.callbacks.ModelCheckpoint(monitor="val_loss", save_top_k=1, save_last=True, filename='{epoch:02d}')
    trainer = pl.Trainer(gpus=1,
                         logger=wb_logger,
                         enable_model_summary=False,
                         max_epochs=epochs,
                         max_steps=steps,
                         log_every_n_steps=1,
                         check_val_every_n_epoch=10,
                         callbacks=[ckpt_cb],
                         default_root_dir='wandb',
                         gradient_clip_val=0.05)
    # this breaks torchscript for some reason
    # wb_logger.watch(model)
    trainer.fit(model,
                train_loader,
                val_dataloaders=val_loader,
                ckpt_path=ckpt_path)
    wandb.finish()

    script = model.to_torchscript()
    torch.jit.save(script, "model.pt")

    eval_main(dataset_dir,
              run_id,
              mode='test',
              user=user,
              batch_size=batch_size)

    return run_id


def eval_main(dataset_dir: pathlib.Path,
              checkpoint: str,
              mode: str,
              batch_size: int,
              user: str,
              take: Optional[int] = None,
              skip: Optional[int] = None,
              project=PROJECT,
              **kwargs):
    model = load_model_artifact(checkpoint, MERP, project, version='best', user=user)

    run_id = f'eval-{generate_id(length=5)}'
    eval_config = {
        'training_dataset': model.hparams.dataset_dir,
        'eval_dataset':     dataset_dir.as_posix(),
        'eval_checkpoint':  checkpoint,
        'eval_mode':        mode,
    }

    wb_logger = WandbLogger(project=project, name=run_id, id=run_id, tags=['eval'], config=eval_config, entity='armlab')
    trainer = pl.Trainer(gpus=1, enable_model_summary=False, logger=wb_logger)

    transform = transforms.Compose([remove_keys("scene_msg")])
    dataset = TorchDynamicsDataset(dataset_dir, mode, transform=transform)
    dataset = take_subset(dataset, take)
    dataset = dataset_skip(dataset, skip)
    loader = DataLoader(dataset, collate_fn=my_collate, num_workers=get_num_workers(batch_size))
    metrics = trainer.validate(model, loader, verbose=False)
    wandb.finish()

    print(f'run_id: {run_id}')
    for metrics_i in metrics:
        for k, v in metrics_i.items():
            print(f"{k:20s}: {v:0.5f}")

    return metrics


def viz_main(dataset_dir: pathlib.Path,
             checkpoint,
             mode: str,
             user: str,
             skip: Optional[int] = None,
             project=PROJECT,
             **kwargs):
    dataset = TorchDynamicsDataset(dataset_dir, mode)

    dataset = dataset_skip(dataset, skip)

    model = load_model_artifact(checkpoint, MERP, project, version='best', user=user)
    model.training = False

    s = model.scenario

    dataset_anim = RvizAnimationController(n_time_steps=len(dataset), ns='trajs')

    n_examples_visualized = 0
    while not dataset_anim.done:
        inputs = dataset[dataset_anim.t()]

        predicted_error = remove_batch(model(torchify(add_batch(inputs))))

        n_time_steps = inputs['time_idx'].shape[0]
        time_anim = RvizAnimationController(n_time_steps=n_time_steps)

        while not time_anim.done:
            t = time_anim.t()
            init_viz_env(s, inputs, t)
            viz_pred_actual_t(dataset, model, inputs, outputs, s, t, threshold=0.05)
            time_anim.step()

            n_examples_visualized += 1

        dataset_anim.step()

    print(f"{n_examples_visualized:=}")