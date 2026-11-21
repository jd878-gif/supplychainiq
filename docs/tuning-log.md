| Run | Change | Reliable in-stock | Unreliable in-stock | Notes |
|---|---|---|---|---|
| 1 | Baseline (static policy, old formula) | 88.7% | 86.6% | Formula bug + drift |
| 2 | Fixed (R,s,S) + monthly recalc | 98.2% | 97.4% | Reliable OK; tiers too close |
| 3 | Unreliable delays: long tail (7d 10%, 10d 5%) | 98.2% | 96.4% | On-time 45%, avg delay 1.9d; small in-stock impact |
| 4 | Unreliable fill: 70% full, short 50–85% | 98.2% | 95.8% | Lost-sales rate 3.1% vs 6.9% (2.2×) |
| 5 | signal: sales (planner sees only sales) | 97.6% | 94.3% | Lost-sales 4.5% vs 10.0% (2.2×); both tiers worse |
