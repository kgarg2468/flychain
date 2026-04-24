# @flychain/sdk

Thin TypeScript SDK package. The current public surface is configuration
helpers, not a full gateway client.

Deep dive:
[../../docs/architecture/dashboard-cli-sdks.md](../../docs/architecture/dashboard-cli-sdks.md)

## API

```ts
import { resolveConfig } from '@flychain/sdk';

const config = resolveConfig();
```

`resolveConfig` combines caller overrides with `FLYCHAIN_GATEWAY_URL`,
`FLYCHAIN_API_KEY`, and `FLYCHAIN_PROJECT_ID` environment variables.
