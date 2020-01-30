import numpy as np
import ompl.base as ob

from link_bot_planning.viz_object import VizObject


class LinkBotGoal(ob.GoalSampleableRegion):

    def __init__(self, si, threshold, numpy_goal, viz: VizObject, n_sttae):
        super(LinkBotGoal, self).__init__(si)
        self.tail_x = numpy_goal[0]
        self.tail_y = numpy_goal[1]
        self.setThreshold(threshold)
        self.viz = viz

    def distanceGoal(self, state):
        return np.linalg.norm([state[0] - self.tail_x, state[1] - self.tail_y])

    def sampleGoal(self, state_out):
        sampler = self.getSpaceInformation().allocStateSampler()
        sampler.sampleUniform(state_out)
        n_links = state_out
        for i in range(n_links):
            theta = theta + np.random.uniform(-max_angle_rad, max_angle_rad)
            rope_configuration[j - 2] = rope_configuration[j] + np.cos(theta) * link_length
            rope_configuration[j - 3] = rope_configuration[j - 1] + np.sin(theta) * link_length

        state_out[2] = (state_out[2] - state_out[0]) + self.tail_x
        state_out[3] = (state_out[3] - state_out[1]) + self.tail_y
        state_out[4] = (state_out[4] - state_out[0]) + self.tail_x
        state_out[5] = (state_out[5] - state_out[1]) + self.tail_y
        state_out[0] = self.tail_x
        state_out[1] = self.tail_y

    def maxSampleCount(self):
        return 100


class LinkBotCompoundGoal(ob.GoalSampleableRegion):

    def __init__(self, si, threshold, numpy_goal, viz: VizObject):
        super(LinkBotCompoundGoal, self).__init__(si)
        self.tail_x = numpy_goal[0]
        self.tail_y = numpy_goal[1]
        self.setThreshold(threshold)
        self.viz = viz
        # TODO: add these to the viz object

    def distanceGoal(self, state: ob.CompoundStateInternal):
        """
        Uses the distance between the tail point and the goal point
        """
        dtg = np.linalg.norm([state[0][0] - self.tail_x, state[0][1] - self.tail_y])
        return dtg

    def sampleGoal(self, state_out: ob.CompoundStateInternal):
        sampler = self.getSpaceInformation().allocStateSampler()
        # sampe a random valid rope configuration
        sampler.sampleUniform(state_out)
        # translate it so that the tail is at the goal
        state_out[0][2] = (state_out[0][2] - state_out[0][0]) + self.tail_x
        state_out[0][3] = (state_out[0][3] - state_out[0][1]) + self.tail_y
        state_out[0][4] = (state_out[0][4] - state_out[0][0]) + self.tail_x
        state_out[0][5] = (state_out[0][5] - state_out[0][1]) + self.tail_y
        state_out[0][0] = self.tail_x
        state_out[0][1] = self.tail_y
        self.viz.states_sampled_at.append(to_numpy(state_out[0], self.))

    def maxSampleCount(self):
        return 100
