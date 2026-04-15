# Database Migration — Singapore Supabase (2026-04-16)

## Summary

Production database migrated from the legacy Supabase project (Japan region) to a new Supabase project in **Singapore (ap-southeast-1)** to reduce latency for the Railway backend.

## What changed

### 1. Railway deployment region

`backend/railway.toml` — added Singapore region pinning:

```toml
[deploy]
numReplicas = 1
region = "asia-southeast1-eqsg3a"
healthcheckPath = "/health"
healthcheckTimeout = 30
restartPolicyType = "ON_FAILURE"
restartPolicyMaxRetries = 3
```

### 2. Production DATABASE_URL

Railway environment variable `DATABASE_URL` was updated to point at the new Singapore Supabase project (via Supavisor connection string). No code changes were required.

## Verification

- **Railway deployment status**: `SUCCESS`
- **Health check** (`GET https://backend.jilly.app/ready`):
  - HTTP 200
  - Response: `{"status":"ready","database":"ok"}`
- **Supavisor dashboard**: active connection from the Railway service confirmed.

## Cleanup

- Local SQL dump files used for migration were deleted.
- The old database was left in **read-only** mode as a historical backup.
- Attempt to drop the probe table `public.__readonly_probe` on the old read-only DB failed as expected and does not affect the new production instance.

## Rollback reference

If an emergency rollback is ever needed, revert `DATABASE_URL` in Railway to the legacy project connection string (stored in the team password manager / 1Password) and redeploy.
