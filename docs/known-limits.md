# Known limits

Accepted trade-offs of the current backend. Each has an owner step.

| Limit | Behaviour | Planned fix |
| --- | --- | --- |
| Redis-down rate limiter | If Redis is unreachable the limiter lets requests through and logs `rate_limiter_unavailable`. Logins stay available but are unthrottled. | Kept on purpose. |
| Register reveals emails | `POST /auth/register` returns 409 `email_taken` for an existing email, so registered addresses can be probed (limited to 5 registrations per IP per hour). | Kept on purpose. |
| Proxy IP and rate limits | `X-Forwarded-For` is ignored; limits use the direct peer IP. Behind a reverse proxy all users share one IP and per-IP limits (30 logins/min, 5 registers/hour, 30 refreshes/min) apply to everyone. | Step 14/15 (security, deployment): trusted-proxy handling. |
| Simultaneous refresh race | Two requests refreshing with the same cookie at once: the second sees a revoked token and revokes the user's whole token family, logging them out everywhere. | Step 11 (frontend): add a short grace window for the just-rotated token. |
