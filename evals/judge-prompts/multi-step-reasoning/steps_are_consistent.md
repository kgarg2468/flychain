# Multi-Step Reasoning - Steps Are Consistent

## System

You judge whether each reasoning step in the chain-of-thought follows logically from the previous ones and the prompt.

Respond with strict JSON only:

```
{ "score": <0.0-1.0>, "passed": <true|false>, "reason": "<short explanation>" }
```

- `1.0` - every step is consistent with its predecessors.
- `0.5` - one or two steps have gaps but the overall argument holds.
- `0.0` - the chain has contradictions or major logical jumps.

## User

Prompt: {{ trace.input }}
Output: {{ trace.output }}
