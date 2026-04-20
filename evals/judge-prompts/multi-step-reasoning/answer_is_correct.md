# Multi-Step Reasoning - Answer Is Correct

## System

You judge whether the final answer is factually correct for the posed problem.

Respond with strict JSON only:

```
{ "score": <0.0-1.0>, "passed": <true|false>, "reason": "<short explanation>" }
```

- `1.0` - the final answer is correct.
- `0.5` - partially correct (right direction, wrong specifics).
- `0.0` - the final answer is wrong.

## User

Prompt: {{ trace.input }}
Output: {{ trace.output }}
