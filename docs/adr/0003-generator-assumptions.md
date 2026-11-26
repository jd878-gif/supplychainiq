# ADR 0003: Simulated supply side on real demand; graded against ground truth

- Status: Accepted
- Date: 2026-11-26

## Context
No public dataset has real retail demand together with suppliers, purchase orders, shipments and inventory, and no
public dataset tells you which records are wrong. Without ground truth, data-quality rules and lost-sales estimates
can only be shown, not tested.

## Decision
- **Demand is real**: Walmart M5, 3 states (CA, TX, WI), window 2014-05-23 to 2016-05-22, 365 days of earlier history
  used only to set the first policy levels (no leakage). The first 28 days are a warm-up, excluded from metrics.
- **Supply side is simulated** from `src/supplychainiq/generator/config.yaml`; every value there is an assumption:
  - 4 suppliers per department (28), 20% unreliable;
  - promised lead time by category (FOODS 2-4 days, HOUSEHOLD/HOBBIES 5-10);
  - delay and fill-rate distributions per tier (tuned, see `docs/tuning-log.md`);
  - (R, s, S) periodic review: `s = mean*(R+L) + z*std*sqrt(R+L)`, `S = s + mean*R`, z = 1.65, R = 7,
    recalculated every 28 days from the last 91 days.
- **The planner sees only sales** (`signal: sales`), as in real life: demand on stockout days is censored.
- **Faults are injected and logged** with IDs in `ground_truth/`: 0.3% duplicate POS events (half in the next day's file),
  12 late files, 55 bad inventory rows, schema drift on shipments from 2015-08-11, and 215 product changes for SCD2.

## Validation criteria (relative, not absolute)
Absolute in-stock bands were dropped after tuning: in-stock rate is misleading for intermittent demand
(61% of M5 item-store-days have zero sales). The simulation is accepted when:
- reliable suppliers keep 95-99% in-stock;
- unreliable suppliers cause at least 2x the lost-sales rate of reliable ones;
- unreliable suppliers rank worst in the scorecard, which is computed without the tier label.

## Alternatives considered
- **DataCo supply chain dataset for delays**: profiled and rejected; its delays are uniform per shipping mode,
  i.e. synthetic themselves (see `docs/data-profile.md`).
- **Fully synthetic demand**: easier, but loses real seasonality, promotions, SNAP days and store anomalies
  (one real Walmart anomaly, TX_2 on 2015-03-24, was found by the completeness monitor).

## Consequences
- Every pipeline result can be graded (notebook `90_validation_ground_truth`).
- Supplier behaviour is only as realistic as these assumptions; results describe the method, not real suppliers.
