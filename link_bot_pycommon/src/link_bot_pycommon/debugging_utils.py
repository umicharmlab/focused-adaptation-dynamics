SHOW_ALL = False
DEBUG_VIZ_B = 14


def debug_viz_batch_indices(batch_size):
    if SHOW_ALL:
        return range(batch_size)
    else:
        return [DEBUG_VIZ_B]