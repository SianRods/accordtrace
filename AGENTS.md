# Dependency management

- Manage Python dependencies in `pyproject.toml` using uv. Use `python scripts/uv.py`
  if uv is not on PATH; the launcher keeps its tooling inside the project.
- Add/remove dependencies with `uv add`/`uv remove`, keep `uv.lock` updated, and sync
  `.venv` after dependency changes. Run Python project commands through `uv run`.
- Commit a resolver-generated lockfile; never fabricate one. If network access
  prevents resolution, installation, or dataset downloads, report the incomplete steps.
- Keep virtual environments, tooling caches, and downloaded MAUD files out of Git.
