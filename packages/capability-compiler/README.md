# flychain-capability-compiler

Domain package for FlyChain capabilities. It defines the `CapabilitySpec`
schema and implements the natural-language compiler, template and recipe
loaders, LLM-as-judge eval engine, slice matching, failure clustering, dataset
synthesis, training backend selection, and promotion gate.

Deep dive:
[../../docs/architecture/capability-flywheel.md](../../docs/architecture/capability-flywheel.md)

## Main Modules

- `schema.py`: `CapabilitySpec` and related Pydantic models.
- `compiler.py`: plain-language description to validated spec.
- `eval.py`: slice matching, judge prompt rendering, score parsing, aggregation.
- `cluster.py`: failure signatures, embedding, HDBSCAN, SFT/DPO dataset rows.
- `recipe.py`: recipe schema and loader.
- `training.py`: dry-run, MLX-LM, and unsloth backends.
- `gate.py`: promotion/archive decision logic.
- `llm.py` and `embeddings.py`: local/cloud LLM and embedding adapters.
