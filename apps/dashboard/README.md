# FlyChain Dashboard

Next.js 14 App Router operator UI for the local FlyChain stack. The dashboard
loads data from the gateway, exposes capability creation, trace exploration,
scorecards, failure triage, clustering, dataset synthesis, training run
controls, replay sets, A/B comparison, settings, and active adapter pointer
operations.

Deep dive:
[../../docs/architecture/dashboard-cli-sdks.md](../../docs/architecture/dashboard-cli-sdks.md)

## Local Dev

```bash
pnpm -F @flychain/dashboard dev
```

The dashboard reads `FLYCHAIN_GATEWAY_URL` on the server and defaults to
`http://localhost:8080`.

## Tests

```bash
pnpm -F @flychain/dashboard test
pnpm -F @flychain/dashboard typecheck
```
