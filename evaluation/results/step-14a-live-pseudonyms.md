# Step 14a live check: customer codes with Groq

Date: 2026-11-11. Model: `openai/gpt-oss-120b` on Groq. Dataset: `sales_large_clean.csv` (309,254
rows). The analysis ran on the fake model; only the api was moved to Groq. The app's own daily
budget was capped at 30,000 tokens for this check, and today's earlier Groq use from this app was 0
tokens, so the 40K stop rule was not reached. Requests were paced under 8K tokens per minute (the
client waited up to 41 s; `x-ratelimit-limit-tokens: 8000`). Provider request bodies were recorded
for this run only, in a temp folder outside the repository.

| # | Question | Tools | Verifier | Tokens in / out | Real ids in provider bodies | Codes sent | What people see |
|---|---|---|---|---|---|---|---|
| 1 | What was the total net revenue? | get_dataset_overview, get_insights | 5 checked, 0 untraced | 8,499 / 306 | 0 | 0 (no customers) | 89,601,650.37 from 152,619 orders, refunds already subtracted |
| 2 | Who are the top 5 customers by revenue? | run_sql | 6 checked, 1 untraced (`309,254`, a row count from the earlier turn, not in this turn's result) | 7,282 / 238 | 0 | 5 | top 5 with real ids (`C018472`, `C010736`, `C012185`, `C012910`, `C018131`) after the fix below |
| 3 | Which customers are at risk of leaving? | get_churn_risk | 48 checked, 0 untraced | 9,420 / 769 | 0 | 11 | ranked table with real ids (`C001262`, `C001795`, …) after the fix below |
| 4 | How much did customer C000469 spend in total, and how many orders did they place? | not run | - | - | - | - | stopped by the app's 30K budget (needed about 6,800 more tokens after 26,514 used) |

Total: 7 provider requests, 26,514 tokens. **No real customer id appeared in any request body.**

## Finding fixed during the check

The model writes codes with a non-breaking hyphen (`CUST‑Z8B862HR`, U+2011), not `-`. The display
mapping only matched `CUST-`, so answers 2 and 3 showed codes instead of real ids. The code pattern
now accepts the hyphen variants (U+2010-2014, U+2212) and maps them back
(`test_codes_written_with_other_hyphens_are_mapped`). Re-reading the stored answers through the
messages API after the fix: answer 2 shows 5 real ids and answer 3 shows 6, with no codes left.

## Token overhead of the codes

Measured with tiktoken `o200k_base` (an approximation of the provider's tokenizer) on the 7 recorded
bodies, compared with the same bodies with each code put back to its real id: 27,243 tokens against
27,127, **+116 tokens (+0.43%)** for 21 codes. One code is 7 tokens; an id such as `C000469` is
about 3.
