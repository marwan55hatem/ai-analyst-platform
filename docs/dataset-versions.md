# Dataset versions (Step 12c)

A manager can refresh a dataset from the UI: **Datasets > (a dataset) > Upload new data**.

## What a new version does

- **It replaces the data. It does not append.** The uploaded file must be a complete export of the
  same data. The new file is stored on its own, converted to its own Parquet file, profiled and
  analysed from scratch. Nothing reads the rows of the previous version. Older versions stay on disk
  and in the version list.
- The new version becomes the current one only after it has been read successfully
  (`promote_if_newer`). A file that cannot be read never hides good data.
- Only column roles chosen by a user are carried over, and only into cleaned versions.
- A version is either an **upload** or a **cleaning** (`source_version_id` is set). There is no
  separate source column; the API derives `source` from that field.

## Compatibility check

`GET /datasets/{id}/versions/{n}/compatibility` compares a version with the highest lower version
that has a ready profile. It answers `pending` until the new file has been profiled, so the UI polls.

| Result | Meaning |
|---|---|
| Added columns | Reported. Never a warning, never a block. |
| Missing columns, changed type or role | Warning, plain words in the UI. |
| Lost date or revenue role | Warning ("some results may be skipped"). |
| Shorter period | Warning when the first date is later than before or the file has less than 50% of the previous row count: "This file covers a shorter period than the previous version. If it only contains new rows, upload a complete export instead, because this upload replaces the previous data." |
| Blocked | Only when the file cannot be analysed: it could not be read, or it has no date column and no revenue or quantity column. A blocked version is not analysed automatically and the previous version stays in use. |

## Automatic analysis

The upload form field `analyze` (default `true`) is stored on the version
(`dataset_versions.auto_analyze`). After profiling, the worker starts the analysis when the global
`AUTO_ANALYZE` setting, the version's `auto_analyze` and a not-blocked file all allow it. Analysis is
still locked per version (409 `analysis_running`); reports stay one at a time per tenant.

## Alerts

Alerts compare a finished run with the previous finished run of the same dataset. **Cleaning-derived
versions never raise alerts**: a cleaned copy of earlier data is not new data, so its run keeps its
summary but is not compared. The run summary is still stored, so a later upload is compared with it.

## Version list

`GET /datasets/{id}/versions` returns version, created date, `source` (upload or cleaning), rows,
quality score, status, the latest analysis status and how many alerts that version's runs raised. The
dataset page shows it as "Version history"; choosing a row opens that version with `?v=N`. The
dashboard and the other pages always use the dataset's current version.

## Known limits

- The compatibility check compares with the nearest lower ready version, not with the current one.
- The outcome panel lives in the page state: reload the page and it is gone (the version list still
  shows the result).
- "No new alerts" is shown after a short wait (2.5 s) for the alert check that runs right after the
  analysis; on a very slow worker the alert can still appear a moment later in the bell.
