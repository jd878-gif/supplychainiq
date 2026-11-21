"""(R, s, S) periodic-review inventory policy.

Orders can only be placed on the supplier's review day, once every R days.
If we do NOT order at a review, the next chance is R days later, and that order
then takes L days to arrive. So the reorder point must protect demand over R + L:

    s = mean * (R + L) + z * std * sqrt(R + L)    (reorder point)
    S = s + mean * R                              (order-up-to: adds one review period)

At a review, if position (on hand + on order) <= s, order S - position.

L is the PROMISED lead time. The planner does not know a supplier will be late,
which is exactly why unreliable suppliers cause stockouts.

Levels are recalculated periodically from trailing demand (see simulate.py), the
way real planners refresh parameters instead of setting them once.
"""

import numpy as np


def policy_levels(
    mean: np.ndarray,
    std: np.ndarray,
    lead_time: np.ndarray,
    review_period: int,
    z: float,
    min_order_up_to: int,
) -> tuple[np.ndarray, np.ndarray]:
    """Return (reorder_point s, order_up_to S) as int32 arrays, with s < S."""
    exposure = review_period + np.asarray(lead_time, dtype=np.float64)

    reorder_point = mean * exposure + z * std * np.sqrt(exposure)
    order_up_to = reorder_point + mean * review_period

    s = np.ceil(reorder_point).astype(np.int32)
    S = np.maximum(np.ceil(order_up_to), min_order_up_to).astype(np.int32)
    S = np.maximum(S, s + 1)
    return s, S
