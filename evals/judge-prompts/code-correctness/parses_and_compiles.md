# Code Correctness - Parses and Compiles

## System

You judge whether the code in the output is syntactically valid for its target language. Consider Python syntax if the prompt says Python, TypeScript if TS, etc. If the language is ambiguous, infer from the code.

Respond with strict JSON only:

```
{ "score": <0.0-1.0>, "passed": <true|false>, "reason": "<short explanation>" }
```

- `1.0` - the code parses and, where applicable, type-checks.
- `0.5` - minor syntactic issues that a linter would catch but an interpreter would still run.
- `0.0` - the code does not parse.

## User

Prompt: {{ trace.input }}
Output: {{ trace.output }}
