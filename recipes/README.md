# Recipes

Each YAML file describes how to turn a dataset into a candidate adapter:

```yaml
base_model: meta-llama/Llama-3.2-3B-Instruct
method: sft # sft | dpo | kto | grpo
dataset_ref: cluster-12-sft.jsonl
backend: mlx-lm # mlx-lm | unsloth | axolotl-modal | sagemaker | together
hyperparams:
  lora_r: 8
  lora_alpha: 16
  lr: 2.0e-4
  epochs: 3
eval_suite_ref: groundedness.yaml
promotion_threshold: 0.05
```

V1 ships (Phase 6):

- `sft-mlx-lora.yaml`
- `sft-unsloth-lora.yaml`

V1 stretch (Phase 7):

- `dpo-mlx-lora.yaml`
