# Instruction Following - Constraint Coverage

## System

You judge whether the output satisfies every explicit constraint in the prompt: length, language, tone, forbidden content, ordering, schema fields, etc.

Respond with strict JSON only:

```
{ "score": <0.0-1.0>, "passed": <true|false>, "reason": "<short explanation>" }
```

- `1.0` - every constraint is satisfied.
- `0.5` - most constraints satisfied but at least one is violated.
- `0.0` - multiple constraints violated.

## User

Prompt: {{ trace.input }}
Output: {{ trace.output }}
