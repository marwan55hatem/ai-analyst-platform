# Step 14a live check: customer codes with Groq

Date: 2026-11-11 (questions 1-3 at 01:50 UTC, question 4 at 15:45 UTC). Model: `openai/gpt-oss-120b` on Groq. Dataset: `sales_large_clean.csv` (309,254
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
| 4 | How much did customer C000469 spend in total, and how many orders did they place? (id typed by the owner) | get_dataset_overview, run_sql (`WHERE customer_id = 'CUST-YKZNZCB8'` on the wire; shown as `'C000469'`) | 2 checked, 0 untraced | 8,205 / 286 | 0 | 1: the typed id's code `CUST-YKZNZCB8` was in the first request | C000469 spent 65,413.88 over 123 orders, which matches the CSV exactly; streamed answer, stored answer and evidence show `C000469`, no codes left |

Total: 10 provider requests, 35,005 tokens (26,514 for questions 1-3, 8,491 for question 4). **No real customer id appeared in any request body.**

Question 4 was first stopped by the app's 30K budget (it needed about 6,800 more tokens after 26,514 used). The owner raised the budget for the check to 38K, and question 4 ran later the same UTC day in a new conversation. Before it ran, Groq use from this app that day was 26,514 tokens, far from the 40K-left stop rule.

## Finding fixed during the check

The model writes codes with a non-breaking hyphen (`CUST‑Z8B862HR`, U+2011), not `-`. The display
mapping only matched `CUST-`, so answers 2 and 3 showed codes instead of real ids. The code pattern
now accepts the hyphen variants (U+2010-2014, U+2212) and maps them back
(`test_codes_written_with_other_hyphens_are_mapped`). Re-reading the stored answers through the
messages API after the fix: answer 2 shows 5 real ids and answer 3 shows 6, with no codes left.

## Token overhead of the codes

Measured with tiktoken `o200k_base` (an approximation of the provider's tokenizer) on the 7 recorded
bodies of questions 1-3, compared with the same bodies with each code put back to its real id: 27,243 tokens
against 27,127, **+116 tokens (+0.43%)** for 21 codes. Question 4: 8,719 against 8,699, +20 tokens. All
10 requests: 35,962 against 35,826, **+136 tokens (+0.38%)**. One code is 7 tokens; an id such as `C000469` is
about 3.
