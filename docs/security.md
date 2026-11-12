# Security

This document records what protects the platform and how each protection was checked. Step 14 is
split in two pull requests: this first part covers the `run_python` sandbox and customer-id codes
toward the model. The threat model, web and API hardening, the production checklist and the
remaining gaps follow in the second part.

## Sandbox isolation (measured)

`sandbox/isolation_check.py` checks every guarantee in `docs/agent-design.md` §6 against the real
sandbox image with the real runtime flags. It has two modes:

- **All layers** (`--socket`): a client container that shares only the `sandbox-ipc` volume posts
  jobs to the running sandbox server over its unix socket, the same way the api does.
- **No static check** (`--direct`): the sandbox image with the sandbox service's flags calls the
  executor with the AST check switched off. This proves that the deeper layers (restricted builtins
  and imports, the audit hook, rlimits and the container) hold on their own.

Run it with `make sandbox-isolation`. CI runs both modes after the image build and keeps the full
tables as the `sandbox-isolation` artifact. `backend/tests/test_sandbox_isolation.py` checks the
compose flags behind each guarantee on every test run, and it runs the live suite when
`SANDBOX_IT=1`.

Measured on 2026-11-10 (Docker Desktop, WSL2). All 54 checks passed after one fix, described below
the table.

| Guarantee | All layers (socket) | No static check (direct) |
|---|---|---|
| No network | `urllib` blocked by the audit hook; `import socket` rejected | `socket` import refused; pandas `urlopen` blocked |
| Read-only root filesystem | writing `/usr/evil.csv` blocked | blocked |
| No storage volume | `/app/storage/x` not found | `/app`, `/app/storage` and `/storage` do not exist |
| No app environment | `import os` rejected | child environment has only `HOME`, `LANG`, `PATH`, thread and bytecode variables; no `JWT_SECRET`, `DATABASE_URL`, `GROQ_API_KEY`, `PSEUDONYM_SECRET` and so on |
| Memory (400 MB per job, 512 MB container) | 600 MB allocation stopped: `memory_limit` | same |
| CPU and time (15 s) | busy loop killed at 15.0 s | blocking read with no CPU killed at 15.0 s |
| Processes | fork rejected | `os.fork` blocked by the audit hook; the container pid limit is proven by `sandbox-container-check` |
| File size (32 MB) | 41 MB write fails: `File too large` | same |
| Blocked builtins | `open`, `eval`, `exec`, `__import__`, `getattr`, `compile` rejected | `open`, `eval` and `exec` are not defined; `__import__('os')` refused |
| Blocked modules | `os`, `sys`, `subprocess`, `socket`, `ctypes`, `pickle`, `shutil`, `importlib`, `urllib.request` rejected | `os`, `socket`, `subprocess` and `ctypes` refused |
| Output caps | stdout cut at 64 KB; result over 256 KB refused | n/a |
| Healthy after a crash, out of memory, a hang or a file-size kill | `/health` ok and `result = 1 + 1` returns 2 in under 1.2 s each time | n/a |
| Tenant separation | a job sees only its own rows | a job's folder is removed when it ends; another job cannot read that path or list `/tmp`; no `job-*` folder is left in `/tmp` |

The api also checks that the dataset version belongs to the caller's tenant before it builds the
CSV for the sandbox (`test_build_data_refuses_another_tenants_version`). Only one job runs at a
time (`SANDBOX_CONCURRENCY=1`).

**Finding (fixed): the 512 MB container limit was really 1 GB.** The `sandbox` service set
`mem_limit: 512m` without `memswap_limit`. Docker then allows as much swap again, and the container
showed `MemorySwap=1073741824` and `memory.swap.max=536870912`. The service now sets
`memswap_limit: 512m`, and `test_sandbox_memory_limit_includes_swap` fails if it is removed.
`docker inspect` after the fix: `Memory=536870912 MemorySwap=536870912`. The per-job 400 MB rlimit
already stopped single jobs first, so the gap only mattered if that layer failed.

## Customer ids toward the model

The model never receives a real customer id. It receives codes such as `CUST-7F3K9QAB`, and the
server maps them back to the real ids for people. Design, key rotation and what a person sees are
described in `docs/agent-design.md` §7 (Customer codes). The tests:

- `tests/test_pseudonym_wire.py` runs a full turn over a mocked provider. The turn uses `run_sql`,
  `run_python`, `get_segments`, `get_churn_risk` and `get_insights`, and the question contains a
  real id typed in lower case. No real id appears in any request body. The typed id arrives as its
  code. The streamed and stored answer, the evidence rows and the messages API show the real id.
- `tests/test_pseudonym_data_plane.py` checks that numeric customer ids never change revenue
  numbers, that `WHERE customer_id = 'CUST-…'` works, and that Python gets codes even when the
  company allows personal data.
- `tests/test_pseudonyms.py` checks the format, that codes are stable per tenant and dataset and
  differ between tenants, collision handling, and that production refuses a missing or weak
  `PSEUDONYM_SECRET` (api, worker and beat).
- `tests/test_demask.py` checks a code split across stream deltas, codes inside SQL and nested
  evidence, typed ids with punctuation, and the hidden personal-id display.
