# Data profile

Answer each question with a number and the query that produced it.
Do this yourself: these are the "why" questions interviewers ask.

## M5 (Walmart demand, real)

1. How many items, stores, states and days? What date range does the full data cover?
2. Which 2-year window will you use, and why that one?
3. What share of item-store-days have zero sales? What does that mean for the forecast metric?
4. How many items change price at least once in your window? How often?
5. Which calendar events and SNAP days fall in your window?
6. How large is your window in rows once converted from wide to long format?

## DataCo (supplier delay calibration, origin unclear)

7. Distribution of `Days for shipping (real)` minus `Days for shipment (scheduled)`: mean, median, p90.
8. Does the delay differ by shipping mode? By how much?
9. Which distribution will your generator use for each supplier type (reliable vs unreliable)?

## Decisions this profile led to
<!-- e.g., chosen window, chosen metric (WAPE), lead-time parameters -->
<!-- Q7: grain = per order (all 62,897 orders have a single delay); delivered orders only (canceled removed, 4.3% of rows); mean 0.57, median 1, p90 2, range −2..+4, 57.3% late.


Q8: the four mode patterns above, plus the conclusion that delays are uniform per mode, so the data is synthetic.
Data-quality notes: #1 canceled rows excluded; #2 uniform synthetic delays, so not used for calibration. -->
