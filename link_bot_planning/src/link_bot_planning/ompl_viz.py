from typing import Optional, List, Dict

import matplotlib.pyplot as plt
import numpy as np
from matplotlib import cm
from matplotlib.animation import FuncAnimation
from ompl import base as ob

from link_bot_planning.state_spaces import compound_to_numpy
from link_bot_planning.viz_object import VizObject
from link_bot_pycommon.experiment_scenario import ExperimentScenario
from moonshine.moonshine_utils import states_are_equal, listify


def plot_plan(ax,
              state_space_description: Dict,
              scenario: ExperimentScenario,
              viz_object: VizObject,
              planner_data: ob.PlannerData,
              environment: Dict,
              goal,
              planned_path: Optional[List[Dict]],
              planned_actions: Optional[np.ndarray],
              draw_tree: Optional[bool] = None,
              draw_rejected: Optional[bool] = None,
              ):
    scenario.plot_environment(ax, environment)

    if draw_rejected:
        for rejected_state in viz_object.rejected_samples:
            scenario.plot_state(ax, rejected_state, color='orange', zorder=2, s=10, label='rejected')

    if planned_path is not None:
        start = planned_path[0]
        end = planned_path[-1]
        scenario.plot_state_simple(ax, start, color='b', s=50, zorder=5, label='start')
        scenario.plot_state_simple(ax, end, color='m', s=20, zorder=6, marker='*', label='final tail planned')
        scenario.plot_goal(ax, goal, color='c', zorder=4, s=50, label='goal')
        draw_every_n = 1
        T = len(planned_path)
        colormap = cm.winter
        for t in range(0, T, draw_every_n):
            state = planned_path[t]
            for randomly_accepted_sample in viz_object.randomly_accepted_samples:
                if states_are_equal(state, randomly_accepted_sample):
                    scenario.plot_state_simple(ax, state, color='white', s=10, zorder=4, label='random accept')
            scenario.plot_state(ax, state, color=colormap(t / T), s=10, zorder=3, label='final path')
    if draw_tree:
        print("Drawing tree.")
        tree_json = planner_data_to_json(planner_data, state_space_description)
        draw_tree_from_json(ax, scenario, tree_json)

    ax.set_xlabel("x")
    ax.set_ylabel("y")
    ax.axis("equal")
    extent = environment['full_env/extent']
    ax.set_xlim([extent[0], extent[1]])
    ax.set_ylim([extent[2], extent[3]])

    handles, labels = ax.get_legend_handles_labels()
    by_label = dict(zip(labels, handles))
    handles = list(by_label.values())
    labels = list(by_label.keys())
    return handles, labels


def planner_data_to_json(planner_data, state_space_description):
    json = {
        'vertices': [],
        'edges': [],
    }
    for vertex_index in range(planner_data.numVertices()):
        v = planner_data.getVertex(vertex_index)
        s = v.getState()
        edges_map = ob.mapUintToPlannerDataEdge()

        np_s = compound_to_numpy(state_space_description, s)
        json['vertices'].append(listify(np_s))

        planner_data.getEdges(vertex_index, edges_map)
        for vertex_index2 in edges_map.keys():
            v2 = planner_data.getVertex(vertex_index2)
            s2 = v2.getState()
            np_s2 = compound_to_numpy(state_space_description, s2)
            # FIXME: have a "plot edge" function in the experiment scenario?
            json['edges'].append(listify({
                'from': np_s,
                'to': np_s2,
            }))
    return json


def draw_tree_from_json(ax, scenario, tree_json):
    for state in range(tree_json['vertices']):
        scenario.plot_state(ax, state, color='k', s=10, zorder=2)

    for edge in tree_json['edges']:
        s1 = edge['from']
        s2 = edge['to']
        # FIXME: have a "plot edge" function in the experiment scenario?
        ax.plot([s1['link_bot'][0], s2['link_bot'][0]], [s1['link_bot'][1], s2['link_bot'][1]], linewidth=1, c='grey')
        scenario.plot_state_simple(ax, s2, color='k')


def animate(environment: Dict,
            scenario: ExperimentScenario,
            goal: Optional = None,
            planned_path: Optional[List[Dict]] = None,
            actual_path: Optional[List[Dict]] = None,
            accept_probabilities: Optional[List[float]] = None,
            fps: float = 1):
    # TODO: de-duplicate this code
    fig = plt.figure(figsize=(20, 20))
    ax = plt.gca()
    extent = environment['full_env/extent']
    scenario.plot_environment(ax, environment)

    ax.set_xlabel("x")
    ax.set_ylabel("y")
    ax.axis("equal")

    start = planned_path[0]
    scenario.plot_state(ax, start, color='b', zorder=2, s=20, label='start')
    if goal is not None:
        scenario.plot_goal(ax, goal, color='c', zorder=2, s=20, label='goal')
        scenario.plot_state(ax, actual_path[-1], color='m', zorder=2, s=20, label='final actual')

    if planned_path is not None:
        planned_path_artist = scenario.plot_state(ax, planned_path[0], 'g', zorder=3, s=20, label='planned')
    if actual_path is not None:
        actual_path_artist = scenario.plot_state(ax, actual_path[0], '#00ff00', zorder=3, s=20, label='actual')

    classifier_line_artist = plt.plot([extent[0], extent[1], extent[1], extent[0], extent[0]],
                                      [extent[2], extent[2], extent[3], extent[3], extent[2]], c='green', linewidth=8)[0]
    ax.set_xlim([extent[0], extent[1]])
    ax.set_ylim([extent[2], extent[3]])

    plt.legend()

    def update(t):
        if accept_probabilities is not None:
            if t < len(accept_probabilities):
                accept_probability = accept_probabilities[t]
                ax.set_title("P(accept) = {:.3f}".format(accept_probability))
                color = 'g' if accept_probability > 0.5 else 'r'
                classifier_line_artist.set_color(color)
        if planned_path is not None:
            scenario.update_artist(planned_path_artist, planned_path[t])
        if actual_path is not None:
            scenario.update_artist(actual_path_artist, actual_path[t])

    anim = FuncAnimation(fig, update, frames=len(planned_path), interval=1000 / fps)
    return anim
