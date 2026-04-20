# Judge prompts

Markdown templates used by the auto-eval engine (Phase 4) as LLM-as-judge instructions. Each eval dimension references one template by path.

Each template has the same shape:

```markdown
# <title>

## System

<system instructions to the judge model>

## User

Input: {{ trace.input }}
Output: {{ trace.output }}
Context: {{ trace.context }}

Return strict JSON: { "score": 0.0 - 1.0, "passed": true | false, "reason": "..." }
```
