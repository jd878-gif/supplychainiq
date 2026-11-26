# ADR 0002: One forecast model; stockout risk as a rule on the forecast

- Status: Accepted; forecast implemented (`notebooks/databricks/50_forecast_train`), risk rule not built yet
- Date: 2026-11-26

## Context
The business question is: *which item-stores will run out before the next delivery, and why?* A direct stockout
classifier needs labels, and labels here are weak: a stockout is only observed when it already happened, it depends
on the replenishment policy (which changes), and in the simulation the labels would come from our own generator,
so a classifier would mostly learn the generator's rules.

## Decision
Train **one** model: daily demand per item-store (M5 demand is real, so this is a genuine forecasting problem).
Stockout risk is a transparent rule on top of it:

```
at risk  if  on_hand + on_order_arriving_within_lead_time - forecast_demand_over_lead_time < safety_stock
```

The forecast is trained on censoring-corrected demand (sales + estimated lost sales from Gold), not raw sales,
so stockouts do not teach the model that demand was low. Metric: WAPE (61% of item-store-days are zero, so MAPE is
undefined).

## Alternatives considered
- **Two models (forecast + stockout classifier)**: more to maintain, and the classifier trains on synthetic labels.
- **Classifier only**: answers "will it stock out" but not "how much will we need", and gives planners nothing to act on.
- **Forecast only**: leaves the operational question (what is at risk this week) to the user.

## Consequences
- Every alert is explainable: on hand, inbound, forecast and safety stock are all visible.
- The rule is unit-testable and can be checked against the generator's ground truth.
- It cannot capture effects outside the inputs (supplier lateness beyond the promised lead time) unless lead time
  is replaced by the supplier's observed lead time from the scorecard, which is the planned next step.

## Results (first implementation, 2026-11-26)
Direct multi-horizon LightGBM (Tweedie loss, features from the forecast origin, baseline offset
`init_score = log(28-day mean)`), trained on censoring-corrected demand and graded against the generator's true demand
on the last 28 days:

| Model | Item-day WAPE | Bias |
|---|---|---|
| 28-day moving average of sales | **0.834** | -2.8% |
| LightGBM, corrected target | 0.845 | -3.5% |

The model did not beat the moving average at item-day level, so the promotion gate kept it out of `@champion`.
What the iterations showed:
- an `item_code` categorical feature (3,049 levels) memorised stale item levels: removing it cut WAPE from 0.900 to 0.847;
- the offset closed most of the remaining gap but not all of it;
- at item-day level, intermittent demand (61% zero days) makes a flat moving average a strong baseline, as in M5.

Decision: keep champion/challenger. Training runs weekly, every result is logged (MLflow + `gold.forecast_metrics`),
and a model is promoted only when it wins. A failed challenger is a result, not a pipeline failure.

Next: evaluate at the decision level (item x lead time), add price-change and promotion features, and try a
median/quantile objective, which matches an absolute-error metric on intermittent demand.
