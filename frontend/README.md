# Frontend

This is the Next.js (App Router) frontend: TypeScript strict, Tailwind 4, shadcn/ui, TanStack Query and next-intl (English and Arabic with RTL). The visual design is documented in [`docs/design.md`](../docs/design.md).

## Run it

```
make up && make migrate              # backend stack (the frontend container is on :3000)
npm --prefix frontend run dev        # or: develop on the host with hot reload
```

`npm run dev` sends `/api/v1/*` to `API_BASE_URL`, which defaults to `http://localhost:8000`. The app runs on port 3000, the same port as the frontend container, so start one or the other: run `npm run dev -- -p 3100` while the container is up.

## Checks

| Command | What |
| --- | --- |
| `make frontend-lint` | ESLint. Physical direction classes (`ml-`, `left-`, ...) and `dangerouslySetInnerHTML` are errors. |
| `make frontend-typecheck` | `tsc --noEmit` (strict) |
| `make frontend-test` | Vitest + Testing Library + MSW |
| `make frontend-build` | Production build (standalone output) |
| `make api-client` | Dumps the backend's OpenAPI schema (no server needed) and regenerates `src/lib/api/schema.d.ts`. CI fails if the committed copy is out of date. |
| `make frontend-e2e` | Playwright against the running stack. Use `E2E_BASE_URL` to target another origin. |
| `npm run lighthouse` | Lighthouse accessibility score per page, in EN and AR (target 90 or more) |
| `SCREENSHOTS=1 npx playwright test screenshots.spec.ts && npm run screenshots` | Captures screenshots and optimises them into `docs/screenshots/` |

The e2e journey registers throwaway users. The API's per-IP rate limits apply to everyone behind the Next.js proxy (see known-limits), so for repeated local runs start the stack with `RATE_LIMIT_ENABLED=false make up`.

## Rules

- The access token lives in memory only. Refreshes are serialised per tab and across tabs (Web Locks); see `src/lib/auth/session.ts`.
- Only `NEXT_PUBLIC_APP_NAME` and `NEXT_PUBLIC_MAX_UPLOAD_MB` reach the browser. Never add secrets as `NEXT_PUBLIC_*`.
- Dataset values and column names are untrusted. Render them as text, with `dir="auto"` or `<bdi>`.
- Every UI string goes in both `messages/en.json` and `messages/ar.json`; a test checks the two files have the same keys and arguments.
