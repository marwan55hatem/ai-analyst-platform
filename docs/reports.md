# Reports and alerts (Step 10)

A manager can download a PDF report of a dataset version in English or Arabic, and gets in-app
alerts when a new analysis run needs attention compared with the previous run. Neither feature
calls a language model: every number comes from stored analysis results.

## 1. Reports

### Flow

1. `POST /api/v1/datasets/{id}/reports {version?, locale: "en"|"ar"}` queues a report. Its source
   is the newest finished run (`completed` or `completed_with_warnings`) of that version.
2. The Celery task `generate_report` builds the facts and renders the PDF. Status moves through
   `queued`, `running` (progress 10, 40, 80) and then `completed` or `failed` with an `error_code`.
3. `GET /reports` lists reports (filters `dataset_id` and `status`, paginated). `GET /reports/{id}`
   is for polling.
4. `GET /reports/{id}/download` streams the PDF as an attachment. `DELETE /reports/{id}` removes
   it.

### Limits

| Rule | Behaviour |
|---|---|
| One report at a time per company | `429 report_busy`, `Retry-After: 10`. The company row is locked while checking. |
| Stored reports per company | `REPORT_MAX_PER_TENANT` (default 50) → `409 report_limit` |
| Request rate | `REPORT_RATE_LIMIT_PER_HOUR` per user for generate and download |
| Retention | `REPORT_RETENTION_DAYS` (default 90). The daily beat task `purge_expired_reports` deletes the rows and their files. |
| Stuck jobs | After `REPORT_STUCK_MINUTES`, `fail_stuck_reports` sets `error_code=timed_out` |
| Size | `REPORT_MAX_MB` → `error_code=too_large` |
| Delete | The report's creator, an owner or an admin. A report still generating returns `409`. |

### Contents

| Section | Source |
|---|---|
| Cover | Dataset name, version, data period, rows, quality score, currency (only if the profile states one), run date |
| Executive summary | Top 5 insights by impact score, in the feed's order. Each shows its confidence and sample size; low confidence is labelled. |
| Key figures | `analysis_runs.summary.kpis`: the dashboard's default cards (last 90 days vs the 90 before), computed by `ml.timeseries.compute`. They match `GET /timeseries` for that range. |
| Trend and forecast | The `forecast_total` insight's chart (history, point forecast, 80% and 95% bands), plus a table of the last 12 periods and every forecast period |
| Top contributors | `pareto` insights for product, category and region (top 10 with ABC class), and dead stock |
| Unusual days | The anomaly events (largest 10 by change, listed by date) with the largest contributing items |
| Customer segments | `rfm_segments`: segment sizes, share of sales, suggested action |
| Churn risk | `churn_risk` band totals only (customers, yearly spend, expected loss) |
| Assumptions and caveats | Fixed method notes, the top 5 quality issues, every module warning, and a status table of the modules |
| Appendix | For each finding (and the KPI table), the evidence purpose, the exact SQL and its row count. Result rows are not shown. |

What-if scenarios are not included.

The facts behind each PDF are saved as JSON next to it, at `<tenant>/reports/<hex>.json`. Tests
compare that JSON with the PDF, the dashboard and the planted answer keys.

### Honesty rules

- **No estimates in the report layer.** It only formats stored figures.
- **A skipped or failed module shows "Not available"** with the engine's reason. A failure shows a
  fixed sentence, never an exception text. A module that ran but found nothing (for example no
  unusual days) shows "Nothing to report". No section is ever silently empty.
- **Every finding and section shows its confidence and sample size.** Low confidence carries
  "treat as an early signal".
- **Runs from before Step 10 work too.** If a run has no summary yet, the worker builds it from the
  stored insights before rendering.

### Security

- **Escaping.** Jinja2 autoescapes every dataset-derived string (names, column values, insight
  text). Tests cover hostile dataset names (`<script>`, `<img src=http://…>`, `{{7*7}}`) and
  hostile product names.
- **No network while rendering.** `FontsOnlyFetcher` serves only the four bundled TTFs in
  `app/reports/fonts`. Every other URL raises `FatalURLFetchingError`: http, https, data, ftp and
  any other file, so there is no SSRF and no local file read. `base_url` is `None`. SVG charts are
  inline.
- **No personal data.** The report never reads `segment_assignments` or `churn_scores`. Churn is
  band totals only, with no customer named, not even by pseudonym. Contributor tables keep only
  product, category and region, and drop any of them whose column is flagged as personal data.
  A test replaces customer ids with e-mail addresses and checks that none appears in the PDF.
- **Storage.** Files are stored under the storage volume with a generated key
  (`<tenant>/reports/<uuid hex>.pdf`) and read only through the tenant-scoped repository. A report
  of another company is a `404`.
- **Download name.** `report-<ascii slug>-v<N>-<locale>-<yyyymmdd>.pdf`, plus an RFC 5987
  `filename*` that keeps the readable (for example Arabic) name with control, path and quote
  characters removed. The storage path never leaves the server.

### Rendering

- **Library: WeasyPrint 70** (HTML and CSS to PDF), chosen after checking its current docs.
  - Pango and HarfBuzz shape Arabic and handle right-to-left text.
  - `@page` margin boxes carry the header (dataset, version, run date) and the footer (page X of Y).
  - `pdf_variant="pdf/ua-1"` writes a tagged PDF with a title and language.
  - It needs no headless browser.
- **Fonts.** IBM Plex Sans and IBM Plex Sans Arabic (400 and 600, SIL OFL; licence in
  `app/reports/fonts/OFL.txt`) are bundled, so the output is the same on every machine.
- **Charts** (`app/reports/charts.py`) are inline SVG drawn by plain Python, and every chart has a
  table of its numbers next to it.
  - Only digits and ISO periods are written inside the SVG; names and legends are HTML, so the
    page shapes Arabic.
  - In Arabic, bar charts grow from the right. The time axis stays left to right.
- **Arabic layout.** `<html dir="rtl">`. Dates and numbers inside Arabic sentences are wrapped in
  left-to-right embedding marks, so `2024-10-03` does not turn into `03-10-2024`. Text written by
  the analysis engine stays English, left-to-right and marked `lang="en"`, as in the app.
- **System packages.**
  - Linux (Docker and CI): `libpango-1.0-0 libpangoft2-1.0-0 libharfbuzz-subset0`.
  - Windows (local `make test`): MSYS2 Pango (`pacman -S mingw-w64-ucrt-x86_64-pango`).
    `tests/conftest.py` points `WEASYPRINT_DLL_DIRECTORIES` at `C:\msys64\ucrt64\bin` when that
    folder exists. When Pango cannot be loaded, the tests in `test_reports.py` are skipped with the
    reason "WeasyPrint cannot load Pango"; Docker and CI install the Linux packages automatically,
    so the tests always run there.

## 2. Alerts

### When they run

1. When a run finishes (`completed` or `completed_with_warnings`), `services/run_summary.py` stores
   a small summary on `analysis_runs.summary`:
   - the dashboard KPI block
   - monthly net revenue and orders, and the latest complete month
   - the monthly revenue forecast, keyed by month
   - the unusual days
   - churn band totals
   - dead-stock names
   - the quality score
   - the last data date
2. `services/alerts.py` then compares that summary with the previous finished run of the same
   dataset (any version) that has a summary. With no previous run, nothing is raised.

The summary exists because a later run of the same version deletes the earlier run's insights.
Without it, the comparison would have nothing to read.

Alert failures are logged and never fail the run.

Cleaning-derived versions (`source_version_id` set) never raise alerts: a cleaned copy of earlier
data is not new data. Their run summary is still stored. See docs/dataset-versions.md.

### Rules

| Rule | Fires when | Severity | Dedupe key |
|---|---|---|---|
| `revenue_drop` (with forecast) | The new run has a later complete month M, and the previous run forecast M | Below its 80% range: warning. Below its 95% range: critical. | `revenue_drop:<dataset>:<YYYY-MM>` |
| `revenue_drop` (no forecast for M) | Net revenue of M vs the month before, down by `revenue_drop_pct` or more. The text adds "Seasonality is not accounted for." | Warning; critical at twice the threshold | same |
| `orders_drop` | Orders of M vs the month before, down by `orders_drop_pct` or more. No orders forecast is stored, so the text always adds the seasonality sentence. | Warning; critical at twice the threshold | `orders_drop:<dataset>:<YYYY-MM>` |
| `new_unusual_days` | Anomaly events that start after the previous run's last data date and were not in its list. At most 5, largest change first. | Drop: warning. Spike: info. | `new_unusual_days:<dataset>:<date>:<kind>` |
| `churn_up` | High-risk customers, or the yearly spend they represent, up by `churn_increase_pct` or more. The top-10% band is used when either run ranked instead of scoring probabilities. | Warning; critical at twice the threshold | `churn_up:<dataset>:v<N>:<last date>` |
| `new_dead_stock` | Products in the new dead-stock list that were not in the previous one (the first 10 are named) | Info | hash of the sorted new names |
| `quality_down` | The quality score of a new version fell by `quality_drop_points` or more | Warning; critical at twice the threshold | `quality_down:<dataset>:v<N>` |
| `stale_data` (daily beat, off by default) | Today minus the version's last data date is more than `freshness_days` | Info; warning at twice the threshold | `stale_data:<dataset>:<last date>` |

Seasonality-aware revenue drop, an example:

- A December peak followed by a January low does not alert when the previous run's forecast
  expected that January.
- The same January without a forecast alerts as a month-over-month fall, and says that
  seasonality is not accounted for.
- Both paths are tested.

Each `(tenant, dedupe_key)` pair is unique (`INSERT … ON CONFLICT DO NOTHING`), so re-running the
same analysis repeats nothing.

Alert text is plain:
- It states the numbers and both periods or versions, and never claims a cause.
- The payload holds the figures and a link target (dashboard, churn, insights or dataset).
- The app writes the sentence in the reader's language from the payload. The stored English
  `title` and `body` are the fallback.

### Thresholds

The defaults come from the environment (`ALERT_*`; see `.env.example`). A company can override
them in `alert_settings` through `GET`/`PUT /api/v1/alert-settings`. Every role may read them;
only owners and admins may change them.

| Setting | Default | Bounds |
|---|---|---|
| `revenue_drop_pct` | 15 | 1–90 |
| `orders_drop_pct` | 15 | 1–90 |
| `churn_increase_pct` | 20 | 1–500 |
| `quality_drop_points` | 5 | 1–50 |
| `freshness_enabled` | false | |
| `freshness_days` | 14 | 1–365 |

### Endpoints

| Endpoint | What it does |
|---|---|
| `GET /alerts?unread&limit&offset` | The feed, as `{items, total}` |
| `GET /alerts/unread-count` | The unread count |
| `POST /alerts/{id}/read` | Mark one alert read (idempotent) |
| `POST /alerts/read-all` | Mark every alert read |

## 3. Frontend

- **/reports, Reports tab**
  - The list shows status, version, language, size and date.
  - Generate dialog: the dataset and version come from the active dataset, the language is EN or
    AR, and the dialog says what the report includes and excludes.
  - Progress while generating, polled every 3 s only while a report is running.
  - Download (authenticated blob) and delete with confirmation.
  - Honest states: no dataset, no finished analysis, analysis running, failed with its reason.
- **/reports?tab=alerts.**
  - Feed with severity badges (icon and word), the compared numbers, links, and mark read / mark
    all read.
  - Unread-only filter and an empty state.
- **Header bell.**
  - Its accessible name carries the count ("Alerts, 3 unread").
  - A polite live region speaks only when the count rises.
  - It polls every 60 s; TanStack Query pauses polling while the tab is hidden.
- **Settings → Alerts.** Thresholds and the data-freshness toggle. Owners and admins edit them;
  members see read-only values.

## 4. Measurements (Step 10, local machine)

| What | Value |
|---|---|
| Backend image (api, worker and beat share it) | 1,654.5 MB before → 1,778.4 MB after (+124 MB: Pango, HarfBuzz, WeasyPrint and its Python deps, fonts) |
| Report on the 309,254-row sample | 2.9 s in the test suite, 1.6 to 2.1 s on the stack. EN 135 KB, AR 160 KB, 21 pages |
| Worker memory while rendering it | 534–693 MiB peak in `docker stats` (worker limit 1024 MiB). Sampled about once a second, so a short spike could be missed. |
| Report on the planted 2-year file | 2.6–3.3 s. EN 153 KB, AR 179 KB |
| Lighthouse on /reports (production build) | Accessibility 100, Best Practices 100, Performance 77 (EN) and 82 (AR) |

The samples `evaluation/results/report-sample-{en,ar}.pdf` were generated from the planted file
(`ground_truth_generator.py --anomalies --churn`, synthetic data only).
