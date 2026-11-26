# ADR 0005: Lost sales from censored demand, reported as a lower bound

- Status: Accepted
- Date: 2026-11-26

## Context
On a stockout day, POS records what was sold, not what customers wanted. Revenue lost to stockouts is the key
business number, but it is never observed directly.

## Decision
Estimate it with an **EM censored-demand** estimator per item-store (notebook `30_gold_sales_inventory`):
1. demand model from the trailing 28-day mean and dispersion (Poisson, or negative binomial when variance > mean);
2. E-step: on stockout days, demand = sold + E[D - sold | D >= sold]; M-step: recompute the mean over all days;
   3 iterations;
3. lost units = E[D - sold | D >= sold] under the corrected mean.

Graded against the generator's true lost sales: the estimate captures about 70% of true lost units
(WAPE 0.30 at store-month). It is published and labelled as a **conservative lower bound**.

## Alternatives considered
- **Ignore censoring (lost sales = 0)**: hides the problem the project exists to measure.
- **Naive mean of non-stockout days**: biased low, because the mean itself is computed from censored days.
- **A learned model**: needs labels that only exist in simulation; would not transfer to real data.

## Consequences
- The headline number is defensible: it understates, never overstates, the loss.
- Long stockouts are underestimated most (the trailing mean decays while the item is out of stock).
- Forecasting (ADR 0002) trains on sales + estimated lost sales, not raw sales.
