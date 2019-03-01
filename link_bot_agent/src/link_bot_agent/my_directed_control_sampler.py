import numpy as np
import ompl.util as ou
from ompl import control as oc

import matplotlib.pyplot as plt


class MyDirectedControlSampler(oc.DirectedControlSampler):
    states_sampled_at = []

    def __init__(self, si, name):
        super(MyDirectedControlSampler, self).__init__(si)
        self.si = si
        self.name_ = name
        self.rng_ = ou.RNG()

    @classmethod
    def reset(cls):
        cls.states_sampled_at = []

    @classmethod
    def alloc(cls, si):
        return cls(si)

    @classmethod
    def allocator(cls):
        def partial(si):
            return cls.alloc(si)

        return oc.DirectedControlSamplerAllocator(partial)

    @classmethod
    def plot(cls, start, goal):
        sampled_points = np.ndarray((len(cls.states_sampled_at), 2))
        for i, (s, p) in enumerate(zip(sampled_points, cls.states_sampled_at)):
            s[0] = p[0]
            s[1] = p[1]
        plt.scatter(sampled_points[:, 0], sampled_points[:, 1], s=10)
        plt.scatter(start[0, 0], start[1, 0], label='start', s=100, c='blue')
        plt.scatter(goal[0, 0], goal[1, 0], label='goal', s=100, c='green')
        plt.xlabel("o0")
        plt.ylabel("o1")
        plt.legend()
        plt.show()
