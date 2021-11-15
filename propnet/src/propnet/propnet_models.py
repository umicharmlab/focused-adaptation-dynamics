import pytorch_lightning as pl
import torch
from torch.autograd import Variable
from torch.nn import functional as F

from link_bot_pycommon.get_scenario import get_scenario
from propnet.component_models import ParticleEncoder, RelationEncoder, Propagator, ParticlePredictor


def pos_to_vel(pos):
    """

    Args:
        pos: [b, T, n]

    Returns:

    """
    vel = pos[:, 1:] - pos[:, :-1]
    vel = F.pad(vel, [0, 0, 1, 0])
    return vel


def get_batch_size(batch):
    batch_size = len(batch['time_idx'])
    return batch_size


# noinspection PyPep8Naming
class PropModule(pl.LightningModule):
    def __init__(self, hparams, residual=False):
        super().__init__()

        self.save_hyperparameters(hparams)

        self.residual = residual
        self.input_dim = self.hparams.attr_dim + self.hparams.state_dim + self.hparams.action_dim
        self.output_dim = self.hparams.position_dim

        # particle encoder
        self.particle_encoder = ParticleEncoder(self.input_dim, self.hparams.nf_particle, self.hparams.nf_effect)

        # relation encoder
        self.relation_encoder = RelationEncoder(2 * self.input_dim + self.hparams.relation_dim,
                                                self.hparams.nf_relation,
                                                self.hparams.nf_relation)

        # input: (1) particle encode (2) particle effect
        self.particle_propagator = Propagator(2 * self.hparams.nf_effect,
                                              self.hparams.nf_effect,
                                              self.residual)

        # input: (1) relation encode (2) sender effect (3) receiver effect
        self.relation_propagator = Propagator(self.hparams.nf_relation + 2 * self.hparams.nf_effect,
                                              self.hparams.nf_effect)

        # input: (1) particle effect
        self.particle_predictor = ParticlePredictor(self.hparams.nf_effect,
                                                    self.hparams.nf_effect,
                                                    self.output_dim)

    def forward(self, state, Rr, Rs, Ra, pstep):
        # calculate particle encoding
        particle_effect = Variable(torch.zeros((state.size(0), state.size(1), self.hparams.nf_effect))).to(self.device)

        # receiver_state, sender_state
        Rrp = torch.transpose(Rr, 1, 2)
        Rsp = torch.transpose(Rs, 1, 2)
        state_r = Rrp.bmm(state)  # basically copies the receiver states
        state_s = Rsp.bmm(state)

        # particle encode
        particle_encode = self.particle_encoder(state)  # [b, n_objects, nf_particle]

        # calculate relation encoding
        # TODO: use difference in pos and vel between related objects?
        state_r_rel = state_r  # [b, num_relations, nf_relation]
        state_s_rel = state_s
        relation_features = torch.cat([state_r_rel, state_s_rel, Ra], dim=-1)
        relation_encode = self.relation_encoder(relation_features)  # [b, n_objects, nf_relation]

        for i in range(pstep):
            effect_r = Rrp.bmm(particle_effect)
            effect_s = Rsp.bmm(particle_effect)

            # calculate relation effect
            relation_effect = self.relation_propagator(torch.cat([relation_encode, effect_r, effect_s], 2))

            # calculate particle effect by aggregating relation effect
            effect_agg = Rr.bmm(relation_effect)

            # calculate particle effect
            particle_effect = self.particle_propagator(torch.cat([particle_encode, effect_agg], 2), res=particle_effect)

        pred = self.particle_predictor(particle_effect)

        return pred


def normalize(x, mean, std):
    return (x - mean) / std


# noinspection PyPep8Naming
class PropNet(pl.LightningModule):

    def __init__(self, hparams, residual=False):
        super().__init__()
        self.save_hyperparameters(hparams)  # this allows us to access kwargs['foo'] like self.hparams.foo

        self.scenario = get_scenario(self.hparams.scenario)

        self.model = PropModule(self.hparams, residual)

    def one_step_forward(self, attr, state, action, Rs, Rr, Ra):
        """
        Used only for fully observable case. Make only a 1-step prediction.
        In the original propnet code this was simply called forward.

            attr: [b, num_objects, n_attr] first dim is value, mean, std
            state: [b, num_objects, n_state]
            action: [b, num_objects, n_action]
            Rs: [b, num_objects, num_relations], binary, 1 at [obj_i,rel_j] means object i is the sender in relation j
            Rr: [b, num_objects, num_relations], binary, 1 at [obj_i,rel_j] means object i is the receiver in relation j
            Ra: [b, num_objects^2, attr_dim] containing the relation attributes
        """
        if action is not None:
            object_observations = torch.cat([attr, state, action], dim=-1)
        else:
            object_observations = torch.cat([attr, state], dim=-1)
        # [b, num_objects, attr+state+action]

        pred_vel_t = self.model(object_observations, Rs, Rr, Ra, self.hparams['pstep'])

        return pred_vel_t

    def configure_optimizers(self):
        optimizer = torch.optim.Adam(self.parameters(), lr=1e-3)
        return optimizer

    def forward(self, batch):
        batch_size = get_batch_size(batch)

        attr, states = self.attr_and_states(batch, batch_size)
        # attr: [b, n_objects, 1]
        # states: [b, T, n_objects, n_state]
        gt_pos = states[..., :self.hparams.position_dim].clone()
        gt_vel = states[..., self.hparams.position_dim:].clone()

        pred_state_0 = states[:, 0]  # [b, n_objects, state_dim]
        pred_pos_t = pred_state_0[..., :self.hparams.position_dim]
        pred_vel_t = pred_state_0[..., self.hparams.position_dim:]
        pred_pos = [pred_pos_t.clone()]
        pred_vel = [pred_vel_t.clone()]
        for t in range(states.shape[1] - 1):
            # compute the relations
            Rs, Rr, Ra = self.scenario.propnet_rel(pred_pos_t, self.hparams.num_objects, self.hparams.relation_dim,
                                                   device=self.device)

            # Integrate the velocity to produce the next positions
            # but only do this for the objects, not the robot. The robots position and velocity is known
            # over the entire prediction, because that is the "action". The robot must be at index 0 here.
            pred_pos_t[:, 1:] = pred_pos_t[:, 1:] + pred_vel_t[:, 1:]  # [b, n_objects, pos_dim]
            # copy in the robot position
            pred_pos_t[:, 0] = gt_pos[:, t + 1, 0]

            # now predict the next velocity
            pred_state_t = torch.cat([pred_pos_t, pred_vel_t], dim=-1)
            pred_vel_t = self.one_step_forward(attr, pred_state_t, None, Rs, Rr, Ra)  # [b, n_objects, position_dim]

            pred_vel_t[:, 0] = gt_vel[:, t + 1, 0]  # copy in the known robot position and velocity

            pred_pos.append(pred_pos_t.clone())
            pred_vel.append(pred_vel_t.clone())

        pred_vel = torch.stack(pred_vel, dim=1)
        pred_pos = torch.stack(pred_pos, dim=1)
        return gt_vel, gt_pos, pred_vel, pred_pos

    def attr_and_states(self, batch, batch_size):
        num_objs = batch['num_objs'][0, 0, 0]  # assumed fixed across batch/time
        time = batch['time_idx'].shape[1]
        attrs = []
        states = []

        # end-effector (more generally, the robot) is treated as an object in the system, so it has state.
        # It also has action and attribute=1
        # all of the object objects do not have action (zero value and attribute=0)
        # putting this _before_ the loops over objects means the robot is index=0, which we rely on elsewhere
        robot_attr, robot_state, robot_action = self.scenario.propnet_robot_v(batch, batch_size, time, self.device)
        attrs.append(robot_attr)
        states.append(robot_state)

        # loop over objects
        for obj_idx in range(num_objs):
            obj_attr, obj_state, obj_action = self.scenario.propnet_obj_v(batch, batch_size, obj_idx, time, self.device)
            attrs.append(obj_attr)
            states.append(obj_state)
        attrs = torch.stack(attrs, dim=1)  # [b, n_objects, 1]
        states = torch.stack(states, dim=2)  # [b, T, n_objects,  ...]
        return attrs, states

    def velocity_loss(self, gt_vel, pred_vel):
        loss = torch.norm(gt_vel - pred_vel, dim=-1)
        # mask out the robot, we know it's velocity and so we don't care about predicting it
        robot_mask = torch.ones(loss.shape[-1], device=self.device)
        robot_mask[0] = 0
        loss = loss * robot_mask
        loss = torch.mean(loss, dim=2)  # objects
        loss = torch.mean(loss, dim=1)  # time
        loss = torch.mean(loss, dim=0)  # batch
        return loss

    def mean_error_pos(self, gt_pos, pred_pos):
        loss = torch.norm(gt_pos - pred_pos, dim=-1)
        loss = torch.mean(loss, dim=2)  # objects
        loss = torch.mean(loss, dim=1)  # time
        loss = torch.mean(loss, dim=0)  # batch
        return loss

    def max_error_vel(self, gt_vel, pred_vel):
        error_vel = torch.norm(gt_vel - pred_vel, dim=-1)
        # we use 95% quantile instead of max because there are outliers
        error_vel = torch.quantile(error_vel, q=0.95, dim=2)  # objects
        error_vel = torch.quantile(error_vel, q=0.95, dim=1)  # time
        error_vel = torch.quantile(error_vel, q=0.95, dim=0)  # batch
        error_vel = error_vel
        return error_vel

    def max_error_pos(self, gt_pos, pred_pos):
        error_pos = torch.norm(gt_pos - pred_pos, dim=-1)
        # we use 95% quantile instead of max because there are outliers
        error_pos = torch.quantile(error_pos, q=0.95, dim=2)  # objects
        error_pos = torch.quantile(error_pos, q=0.95, dim=1)  # time
        error_pos = torch.quantile(error_pos, q=0.95, dim=0)  # batch
        error_pos = error_pos
        return error_pos

    def training_step(self, train_batch, batch_idx):
        gt_vel, gt_pos, pred_vel, pred_pos = self.forward(train_batch)
        loss = self.velocity_loss(gt_vel, pred_vel)
        self.log('train_loss', loss)
        return loss

    def validation_step(self, val_batch, batch_idx):
        gt_vel, gt_pos, pred_vel, pred_pos = self.forward(val_batch)
        loss = self.velocity_loss(gt_vel, pred_vel)
        self.log('val_loss', loss)
        max_error_vel = self.max_error_vel(gt_vel, pred_vel)
        self.log('max_error_vel', max_error_vel)
        max_error_pos = self.max_error_pos(gt_pos, pred_pos)
        self.log('max_error_pos', max_error_pos)
        mean_error_pos = self.mean_error_pos(gt_pos, pred_pos)
        self.log('mean_error_pos', mean_error_pos)