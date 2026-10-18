# ML and analytics methods

How the analysis pipeline (Step 4) works and which rules every number follows. Later steps add
their modules to the same framework.

## Module interface

```
AnalysisModule            name, title, requires (set of semantic roles), run(context) -> [Insight]
AnalysisContext           version id, tenant id, RoleMap, AnalysisConnection, settings, quality, config
Insight                   module, type, title, summary, result (ModuleResult), impact, payload, chart, evidence
ModuleResult              value, confidence, sample_size, method, warnings   (CLAUDE.md convention)
registry.all_modules()    explicit list in app/ml/registry.py
capabilities.detect()     role map -> per module {available, reason, notes}
```

* A module never reads the file or the database. It gets one hardened DuckDB connection
  (`app/ml/engine.py`): the role columns are loaded once into a typed table called `data`
  (dates as DATE, amounts as exact DECIMAL), then external access and configuration changes are
  turned off, like `SafeQueryService`. Memory is capped by `ANALYSIS_MEMORY_MB`.
* Modules aggregate in SQL and give pandas/statsmodels only small series.
* The text of insights is a deterministic template (`app/ml/format.py` formats numbers per
  locale: `en`, `de`, `fr`, `ar`). An LLM may polish it later but never computes a number.
* Every SQL string stored in `evidence.queries[].sql` is the exact text that ran, with a literal
  date where needed, and passes `validate_sql`. A test runs each one again through
  `SafeQueryService` and compares the rows, so the agent in Step 8 can reproduce every number.
* A module that raises is recorded as a warning on the run; the others continue.

## Role resolution rules

* Everything works from the semantic roles saved in `column_profiles` (so user overrides and
  Arabic column names work). When a role has several columns the highest `role_confidence` wins.
* **Primary date column.** Among the columns with role `date`, prefer the first whose name or
  original name contains `order`, `sale`, `invoice`, `transaction` or `created` (or the Arabic
  equivalents); otherwise the first date column in the file. Override it with `date_column` in
  `POST /datasets/{id}/analyze` (or `?date_column=` on `/capabilities`).
* **Derived revenue.** If there is no revenue column but quantity and price exist, revenue is
  `quantity x price` and every affected insight says so ("Revenue was derived as quantity x price
  because the file has no revenue column.").
* Runs use the dataset's current version by default; `?version=N` selects another.
* Currency is not detected. Set `currency` (for example `USD`) and `locale` in the run config;
  a re-run inherits the last run's settings unless it repeats them.

## Net, gross and refunds

* The headline revenue is **net**: the sum of all signed revenue values.
* Negative revenue or quantity is a return/refund. The KPI insight reports gross sales,
  `returns_total`, `returns_count` and net revenue in its payload, and a separate `returns`
  insight appears when there are any.
* Orders and average order value use positive-revenue rows (distinct order ids when an order id
  column exists, otherwise one row = one order). AOV = gross sales / orders.
* **Pareto** ranks on positive sales only and lists returns per item beside them. **Cohort
  retention** counts a customer as active in a month only with a positive purchase. **Trend and
  seasonality** use net monthly revenue and say so. Each affected insight carries a warning that
  names the basis.

## Data-quality caveats

Read from the version's stored profile and quality score; nothing is recomputed.

| Trigger | Warning on every affected insight | Confidence |
| --- | --- | --- |
| Profile reports exact duplicate rows | "N exact duplicate rows are included in these totals." (duplicates are not removed; cleaning is opt-in in Step 3) | capped at medium |
| A key column of the module has more than 5% nulls | "N rows with no value in 'col' (x% empty) were excluded from this analysis." | capped at medium |
| Quality score below 60 | "The dataset quality score is low (s/100) ..." | capped at medium; low if score < 40 |

Rows with a null in a module's key column are always excluded in SQL; the count is stored in
`payload.excluded_rows`. A cap never raises a confidence that was already lower.

## Confidence

Sample-size confidence comes from `confidence_for`: high at 1,000 rows or more, medium at 100,
otherwise low. Time-based insights also use the number of periods: fewer than 6 low, fewer
than 12 medium, otherwise high. Seasonality without STL (under 24 months) is always low.

## Impact score

`impact = 100 x min(1, |money effect| / base revenue / 0.25) x confidence weight`

* base revenue = total positive sales of the version; weights: high 1.0, medium 0.6, low 0.3.
* An effect worth 25% of revenue or more scores the maximum, so scores 0-100 compare across
  datasets and across modules, and later actions can use the same function.
* Money effect per insight type: growth = change vs the comparison period; year-to-date = the
  difference vs last year; peak season = average peak month minus the average month; weekday =
  (best weekday - average day) x 52; growth extremes = the larger absolute monthly change; returns
  = returns total; dead stock = revenue the products earned in the N days before they went quiet;
  cohort = customers who did not return next month x revenue per active customer-month.
* Descriptive insights (headline KPI, Pareto concentration) score a **10% swing** of the figure
  (net revenue, or the top item's sales), so they do not outrank findings that need a decision.

## Modules

| Module | Needs | Output |
| --- | --- | --- |
| `kpis` | revenue | net/gross revenue, returns, orders, AOV, units, margin (when cost exists); growth vs the previous period and year. The reporting period follows the detected granularity: monthly (default, also for daily/weekly/irregular), quarterly or yearly. The latest incomplete period is left out. Year-over-year is the headline when known (seasonality-proof). |
| `trend_seasonality` | revenue, date | STL (statsmodels, period 12, robust) of monthly net revenue for 24+ months, with a plain monthly index for 12-23 months (low confidence) and nothing below 12; peak and weakest month; seasonal strength 1 - Var(resid)/Var(seasonal+resid); weekday pattern (4+ weeks of daily data); strongest growth and steepest decline between consecutive complete months; year-to-date vs the same span last year. Missing months are interpolated and flagged. |
| `pareto` | revenue + product/category/region/channel | per dimension: ABC classes (A up to 80% cumulative share, B up to 95%, C the rest), top contributors, long tail (classes B and C); dead stock: products whose last positive sale is more than N days (default 90, `dead_stock_days`) before the dataset's last date. |
| `cohort_retention` | customer_id, date | monthly cohorts by first positive purchase month, retention matrix (offsets 0-12), customer-weighted month-1 and month-3 retention over cohorts old enough to observe. The incomplete last month is left out. |
| `forecast` | date + (revenue or quantity) | rolling-origin backtested forecast of net revenue (or quantity) for the whole business and its top 5 categories (or products), default 6 periods, with 80% and 95% ranges. See [Forecast](#forecast). |

## Statistical notes

* STL needs two full cycles, hence 24 months; the seasonal component is averaged per calendar
  month to choose the peak.
* Percentage changes against a zero base are not reported.
* Cohort retention uses customer-weighted averages, not an average of cohort percentages.

## Forecast

`app/ml/forecasting/` (engine) and `app/ml/modules/forecast.py` (the module). The same code serves the
automatic run and `POST /datasets/{id}/forecast` (metric, horizon, group_by, frequency).

### What is forecast

* **Metric**: net revenue (the Step 4 net basis: refunds are negative rows and stay in), or quantity,
  or cost. `price` is refused because it cannot be summed.
* **Series**: the whole business, plus the top 5 categories (or products when there is no category
  column) by net revenue; a custom request chooses the dimension with `group_by` (a dimension role or
  its column name, at most `FORECAST_MAX_GROUPS` groups).
* **Level**: aggregated in DuckDB (the history SQL is the stored evidence and passes `validate_sql`).
  The detected granularity decides: monthly data stays monthly; daily or weekly rows are rolled up to
  months once there is a year of history (to weeks after half a year, otherwise days); quarterly stays
  quarterly; yearly data is not forecast. `frequency` in a custom request overrides this.
  Seasonal cycles: day 7, week 52, month 12, quarter 4.
* **Primary date**: the same rule as Step 4 (an explicit `date_column` wins, then order/sale/invoice
  names, then the first date column).

### Series preparation (every change is a warning)

1. **Partial last period dropped.** If the data ends part-way through a period (for example on the 14th),
   that period is removed from training: half a month of sales would pull every forecast down. The
   forecast then starts at the dropped period.
2. **Gaps filled with zero.** A period without records is treated as "no sales" (zero) for revenue,
   quantity and cost, and counted in a warning.
3. **Outliers capped for training only.** Values more than 5 robust deviations (MAD) from a trend plus
   seasonal-position level (fitted on log values for non-negative series) are capped; the chart and the
   backtest scores use the real values. A normal seasonal peak is not an outlier.

### Candidate models and selection

* Baseline **seasonal naive** (repeat the same period of the last cycle); **AutoETS**, **AutoARIMA** and
  **AutoTheta** from statsforecast; **LightGBM** on a linear trend plus lag, rolling-mean and calendar
  features (recursive, fixed seed, one thread). No Prophet. ARIMA and seasonal ETS are skipped for
  weekly data (cycle 52) because they are slow and memory hungry.
* **Fewer than two full seasonal cycles**: only simple models (naive or seasonal naive, non-seasonal ETS
  and Theta); confidence is low. Below one cycle, or too short to backtest, the baseline is used.
* **Rolling-origin (expanding window) backtest**: horizon = the requested horizon, shrunk so that at
  least two thirds of the history is always training data; windows move forward half a horizon at a
  time, at most 6, with two seasonal cycles as the earliest training set when the history allows.
* **Metrics** pooled over all test points: sMAPE (percent, 0-200), MAE (units of the metric) and MASE
  (MAE divided by the in-sample seasonal-naive MAE; below 1 beats the baseline). The winner has the
  lowest MASE. It must beat the baseline's MASE by at least 2%; otherwise the result is "no better than
  a seasonal naive baseline": the baseline is used and confidence is low.
* Fixed random seed (`FORECAST_SEED`, default 42): the same data gives the same forecast.

### Intervals

80% and 95% prediction intervals come from the models (statsforecast's model-based intervals). For
LightGBM they come from the root-mean-square of its own earlier backtest errors (1.5x its in-sample
residuals when there are none yet). Each backtest also measures how often the 80% range really
contained the outcome (`coverage80`); on the synthetic test series it averages 70-90% across seeds.
Lower bounds are cut at zero for series that never go negative. An interval says how far real outcomes
were from past forecasts of this kind; it is not a guarantee.

### Confidence (`forecasting/confidence.py`)

| Level | Needs |
|---|---|
| high | beats the baseline, MASE <= 0.7 and at least 3 full seasonal cycles of history |
| medium | beats the baseline, MASE <= 1.0 and at least 2 full cycles |
| low | anything else: under 2 cycles, MASE above 1, no better than the baseline, no backtest possible, a constant series |

Then one level is removed if the backtest 80% coverage is outside 60-95%, and the Step 4 data-quality
cap (duplicates, null key columns, low quality score) is applied on top. The wording follows the final
level: every title and summary says "estimated" and shows the 80% range beside the point value; low
confidence or "no better than baseline" opens the summary with "This forecast is unreliable: ...".

### Limits on the work (never a timeout)

`FORECAST_MAX_SECONDS`, `FORECAST_MAX_SERIES` and `FORECAST_MEMORY_MB` (process RSS) are checked before
each model and each extra series. When one is hit: no further models are tried (LightGBM, the heaviest,
needs 40% of the time budget in reserve and goes first), then the remaining groups are skipped; each
step is a warning. The baseline always runs.

### MLflow

One run per forecast under experiment `tenant-<tenant_id>` (tags `tenant_id`, `dataset_version_id`),
with parameters, per-model backtest metrics (`s0.AutoETS.mase`; series are numbered, never named) and
`forecast.json` (periods, point, 80/95 bounds, scores; no history, no rows). Logging runs on a thread
with a short deadline: an unreachable server only adds a warning.

### Statistical caveats

* The winner is the best of up to five models on a few overlapping windows, so its scores are slightly
  optimistic (selection bias) and overlapping windows are correlated; treat 3-4 points of coverage as noise.
* The 2% margin and the confidence thresholds are conventions, not significance tests.
* **Known limits**: no holiday, Ramadan or Eid effects, no promotions or price changes, no external
  data (weather, macro), no reconciliation (group forecasts need not add up to the total). Seasonality
  is learned from the history only, so a moving calendar (Ramadan shifts about 11 days a year) is smeared
  across neighbouring months.
