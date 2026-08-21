# Repository Guidelines

## Project Structure & Module Organization

`papersearch/` contains the Python application. The CLI entry point is `papersearch/__main__.py`; orchestration lives in `pipeline.py`, persistence in `db.py`, and the local HTTP API in `server.py`. Source adapters are under `papersearch/sources/`, while the browser UI is in `papersearch/static/`. Tests mirror major modules in `tests/test_*.py`. Shared defaults live in `configs/`, reusable interest rules in `profiles/`, and optional Codex/Papis workflows in `skills/`. SQLite databases and user-specific configuration are local state and must remain untracked.

## Build, Test, and Development Commands

This project uses the Python standard library and needs no build step.

```bash
cp configs/local.example.json configs/local.json  # create local settings
python3 -m papersearch config-check                # validate merged config/profile
python3 -m papersearch init-db                     # initialize SQLite
python3 -m papersearch update --bootstrap          # perform initial discovery
python3 -m papersearch update                      # run an incremental update
python3 -m papersearch serve --port 8765           # start the local web UI
python3 -m unittest discover -s tests              # run the complete test suite
```

Use `python3 -m papersearch --help` for PDF, summary, reclassification, and Papis commands.

## Coding Style & Naming Conventions

Follow existing Python conventions: four-space indentation, `snake_case` functions and modules, `PascalCase` classes, and uppercase constants. Keep imports grouped as standard library, then local modules. Prefer type annotations, `pathlib.Path`, structured JSON handling, and small functions with explicit return values. There is no enforced formatter; keep changes PEP 8-compatible and avoid unrelated formatting churn. Frontend changes should preserve the existing plain HTML/CSS/JavaScript architecture.

## Testing Guidelines

Tests use `unittest`; name files `test_<module>.py`, classes `<Feature>Tests`, and methods `test_<behavior>`. Use `tempfile.TemporaryDirectory` for database, config, and Papis fixtures. Add focused regression tests for classification, source parsing, schema migrations, API behavior, and PDF/Papis state transitions. No formal coverage threshold exists, but all tests should pass before submission.

## Commit & Pull Request Guidelines

History uses short, imperative summaries such as `Add Papis integration and Codex skills`. Keep commits narrowly scoped and do not include generated databases or credentials. Pull requests should explain user-visible behavior, configuration/schema changes, and verification performed. Link relevant issues and include screenshots for UI changes.

## Security & Configuration

Never commit `configs/local.json`, `configs/interests.json`, `.env`, API keys, personal email addresses, `data/*.sqlite*`, logs, or Papis library contents. Add shareable settings only to example files, reference secrets through environment variables, and verify `git diff --cached` before committing.
