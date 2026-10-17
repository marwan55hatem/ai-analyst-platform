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

## Statistical notes

* STL needs two full cycles, hence 24 months; the seasonal component is averaged per calendar
  month to choose the peak.
* Percentage changes against a zero base are not reported.
* Cohort retention uses customer-weighted averages, not an average of cohort percentages.
