from state_space_dynamics import unconstrained_dynamics_nn
from state_space_dynamics import full_dynamics_nn


def get_model(model_class_name):
    if model_class_name == "ObstacleNN":
        return full_dynamics_nn.FullDynamicsNN
    elif model_class_name == "SimpleNN":
        return unconstrained_dynamics_nn.UnconstrainedDynamicsNN

