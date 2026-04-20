# Contributing to FlyChain

Thanks for your interest in FlyChain. This guide covers dev setup, project layout, and the conventions we follow.

## Dev setup

Prerequisites:

- macOS with Apple Silicon (M1+) OR Linux with CUDA 12+
- Docker Desktop (or equivalent)
- Node.js 20+, pnpm 9+
- Python 3.11+, [`uv`](https://github.com/astral-sh/uv)
- Ollama (docker image is included in `docker-compose.yml` for convenience)

Clone and install:

```bash
git clone <repo>
cd flychain
pnpm install
uv sync
docker compose up -d
```

## Repo layout

The repo is a multi-language monorepo:

- **JS/TS workspaces** managed by **pnpm** (`pnpm-workspace.yaml`). Apps and packages with a `package.json` are members.
- **Python workspaces** managed by **uv** (`[tool.uv.workspace]` in root `pyproject.toml`). Apps and packages with a `pyproject.toml` are members.

See [plan.md](./plan.md) for the full architecture and roadmap.

## Commands

```bash
# JS/TS
pnpm -r lint         # lint all packages
pnpm -r typecheck    # typecheck all packages
pnpm -r test         # test all packages
pnpm -r build        # build all packages

# Python
uv run ruff check .
uv run ruff format --check .
uv run pytest
```

## Conventions

- **Formatting:** ruff for Python, prettier for JS/TS, 2-space indent for YAML and JSON.
- **Line endings:** LF.
- **License headers:** not required on every file; the repo-level `LICENSE` covers everything.
- **Commits:** imperative mood, short first line (<72 chars), optional body.
- **Branches:** feature branches off `main`; PRs require green CI.

## Adding a new capability template

See [capabilities/templates/README.md](./capabilities/templates/README.md).

## Adding a new recipe

See [recipes/README.md](./recipes/README.md).

## Reporting issues

Open a GitHub issue with:

1. What you were trying to do.
2. What happened vs what you expected.
3. OS, architecture, Docker/Node/Python versions.
4. Relevant logs (redact secrets).
