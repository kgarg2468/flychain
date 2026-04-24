# flychain-sdk

Thin Python SDK package. The current public surface is a version constant and a
gateway URL helper.

Deep dive:
[../../docs/architecture/dashboard-cli-sdks.md](../../docs/architecture/dashboard-cli-sdks.md)

## API

```python
from flychain import gateway_base_url

url = gateway_base_url()
```

`gateway_base_url` reads `FLYCHAIN_GATEWAY_URL` and otherwise returns
`http://localhost:8080` or the caller-supplied default.
