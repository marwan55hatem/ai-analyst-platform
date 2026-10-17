# Step 1 test plan: backend core and auth

Real Postgres (database `analyst_test`, schema built by `alembic upgrade head`), no mocks for the DB.

## Happy paths
- register creates tenant + owner; login returns access JWT + httpOnly/Secure/SameSite=Lax refresh cookie; /me returns user + tenant.
- refresh rotates the token (old revoked, new works); logout revokes it.
- team: list, create member, change role, deactivate (deactivated user cannot log in, tokens revoked).

## Edge cases
- Email case-insensitive and unique; weak password (short, common, contains email) -> 422 `weak_password`.
- Wrong password / unknown email give the same 401. Inactive user cannot log in or use an existing token.
- Expired, tampered, `alg=none`, wrong-secret and missing-claim access tokens -> 401. Missing token -> 401.
- Expired or unknown refresh token -> 401; missing cookie -> 401.
- Last-owner protection (demote, deactivate); admin cannot demote/deactivate/create owners; member gets 403 on /team.
- Validation errors use `{code,message,details}` and never echo the password.

## Abuse cases
- Reusing a rotated refresh token -> 401 and every refresh token of that user is revoked.
- `tenant_id` in any request body -> 422 (extra=forbid); no OpenAPI request param/body is named `tenant_id`.
- A valid user id paired with another tenant's id in a forged-but-signed token -> 401.
- Tenant B calling every `{member_id}` route with tenant A ids -> 404 (never 403); list endpoints only show own tenant.
- 6th login attempt within a minute for the same IP+email -> 429 with Retry-After.
- Migration runs twice on an empty database without error.
