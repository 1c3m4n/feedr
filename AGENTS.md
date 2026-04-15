# AGENTS.md

## Purpose

This file is for coding agents working in the `feedr` repository.
It documents the actual build, run, verification, and coding conventions used here.
Prefer following the existing codebase over generic framework defaults.

## Repository Summary

- Stack: FastAPI, Starlette sessions, Jinja2 templates, SQLAlchemy ORM, SQLite by default.
- Entry point: `main.py`.
- UI: server-rendered HTML templates with inline CSS and inline JavaScript.
- Storage: SQLite database under `/storage` in containers, local `feedr.db` fallback in development.
- Deployment target: Docker image, Once-compatible, port `80`, health endpoint at `/up`.
- Project workflow: maintained using OpenCode and various coding models/agents collaborating in the same repo.

## Workflow Rules

- Always push the package image to GitHub Container Registry after completing requested app changes: `ghcr.io/1c3m4n/feedr:latest`.
- Fizzy is required for work tracking. Create or update the relevant Fizzy card for every meaningful task, feature, fix, or polish pass.
- Assume multiple agents or models may be working in the repo; avoid undoing unrelated work.
- Keep this file aligned with the real workflow if the team changes tools or release steps.

## Repository Layout

- `main.py`: app setup, models, schema bootstrap, auth, API routes, background fetcher, HTML routes.
- `templates/`: `reader.html`, `login.html`, `settings.html`.
- `static/`: mounted static assets.
- `requirements.txt`: Python dependencies.
- `Dockerfile`: production container build.
- `migrate_v1_to_v2.py`: migration utility for older schema/data.

## Rule Files

- No repo-local Cursor rules were found in `.cursor/rules/`.
- No repo-local `.cursorrules` file was found.
- No `.github/copilot-instructions.md` file was found.
- If any of those files are added later, update this document to summarize them.

## Environment Setup

Typical local setup:

```bash
python -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
cp .env.example .env
```

Important environment variables used by the app:

- `SECRET_KEY`: session signing key.
- `GOOGLE_CLIENT_ID`: Google OAuth client id.
- `GOOGLE_CLIENT_SECRET`: Google OAuth client secret.
- `APP_URL`: external app base URL used for OAuth callback generation.
- `DATABASE_URL`: defaults to `sqlite:////storage/feedr.db`.
- `LOCAL_AUTH_ENABLED`: enables local auth outside localhost when set to truthy.

## Build / Run Commands

Install dependencies:

```bash
pip install -r requirements.txt
```

Run locally with reload:

```bash
uvicorn main:app --reload --port 80
```

Run locally on an alternate port:

```bash
uvicorn main:app --reload --port 8000
```

Health check:

```bash
curl http://localhost/up
```

Basic syntax check:

```bash
python -m py_compile main.py
```

Build the production image:

```bash
docker build -t ghcr.io/1c3m4n/feedr:latest .
```

Run the container locally:

```bash
docker run --rm -p 80:80 \
  -e SECRET_KEY=dev-secret \
  -e APP_URL=http://localhost \
  -v feedr-data:/storage \
  ghcr.io/1c3m4n/feedr:latest
```

## Lint / Format Commands

There is currently no configured linter or formatter in the repository.
There is no `pyproject.toml`, `ruff.toml`, `pytest.ini`, `tox.ini`, or `.flake8`.

For now, use lightweight verification:

```bash
python -m py_compile main.py
```

If you introduce a formatter or linter, keep the change explicit and repo-wide rather than partially formatting files.

## Test Commands

There is currently no automated test suite checked into the repository.
No `tests/` directory or `test_*.py` files were found.

Current verification is mostly:

- run the app locally
- hit `/up`
- exercise login and reader flows manually
- use browser automation or screenshots for UI changes when appropriate

If you add pytest tests later, use these conventions:

Run all tests:

```bash
pytest
```

Run one file:

```bash
pytest tests/test_some_feature.py
```

Run a single test:

```bash
pytest tests/test_some_feature.py::test_specific_case
```

Run tests matching a keyword:

```bash
pytest -k subscription
```

## Agent Expectations

- Prefer minimal, targeted changes.
- Preserve Docker/Once compatibility.
- Keep data stored under `/storage` in container-oriented changes.
- Do not change the app off port `80` in the Dockerfile unless explicitly asked.
- Build and push `ghcr.io/1c3m4n/feedr:latest` after requested code changes unless the user explicitly says not to.
- Fizzy is mandatory. Record and track work in Fizzy for every meaningful change, not just larger tasks.
- Verify UI-facing changes in a browser when practical.
- Avoid broad refactors unless they are necessary for correctness.

## Python Style

- Follow existing PEP 8 style with 4-space indentation.
- Use double quotes consistently, matching `main.py`.
- Keep functions small enough to scan, but do not invent abstractions unnecessarily.
- Prefer explicit helper functions when logic is reused or non-trivial.
- Keep module-level constants uppercase, like `DB_PATH`.
- Use `snake_case` for functions, variables, and route helpers.
- Use `PascalCase` for SQLAlchemy model classes.
- Keep route handler names descriptive, typically prefixed with `api_` for JSON endpoints.

## Imports

- Group imports in this order: standard library, third-party, local.
- Within a group, keep imports roughly alphabetical unless readability is better otherwise.
- Prefer one `from sqlalchemy import (...)` grouped import, matching the current style.
- Avoid unused imports.
- Keep `typing` imports explicit, e.g. `Optional`, `List`.

## Types

- Add type hints for helper functions and route parameters where practical.
- Match existing style: `Optional[int]`, `str`, `dict`, and SQLAlchemy model return values.
- Do not introduce a different typing style in one file only.
- If you add a new complex payload shape, prefer a small response model or a clearly structured dict.

## FastAPI / Route Conventions

- Use `Depends(get_db)` for request-scoped database sessions.
- Do not create long-lived per-request sessions manually.
- For HTML routes, return `TemplateResponse` or redirects.
- For JSON errors, return `JSONResponse({"error": "..."}, status_code=...)`.
- Keep auth checks near the top of each handler.
- Use simple status codes consistently:
  - `401` for unauthenticated access
  - `400` for invalid input
  - `404` for missing resources
  - `409` for conflicts like duplicate subscriptions or friendships

## Database / SQLAlchemy Conventions

- Add new models in `main.py` unless the project is explicitly being reorganized.
- Use `db.add(...)`, `db.commit()`, and `db.refresh(...)` after inserts when the object is needed immediately.
- Scope queries to the current user wherever user-owned data is involved.
- Preserve uniqueness constraints and ownership checks.
- Be careful with schema changes: this app currently performs lightweight schema bootstrap in code.
- If adding a column to an existing table, check whether `ensure_schema()` must be updated.

## Error Handling

- Prefer explicit guard clauses over deeply nested conditionals.
- Return user-facing errors that are short and concrete.
- Do not swallow exceptions silently unless there is a clear operational reason.
- The background fetcher currently suppresses exceptions; avoid spreading that pattern into request handlers.
- When catching exceptions, keep state consistent before returning, especially around `is_fetching` flags and commits.

## Template / Frontend Conventions

- Templates currently keep CSS and JavaScript inline; follow that pattern unless a larger reorganization is requested.
- Reuse existing theme tokens like `--bg`, `--surface`, `--accent`, and related dark theme variables.
- Preserve the three-pane reader layout unless explicitly changing UX.
- Keep UI additions calm and minimal, consistent with the current Google Reader-inspired styling.
- Prefer direct DOM updates and small helper functions over framework-style rewrites.
- When adding hotkeys, update both the key handler and the visible hotkey list.

## Verification Checklist

After changing backend behavior:

- run `python -m py_compile main.py`
- start the app
- verify `/up`
- verify the affected route manually

After changing templates or JS behavior:

- open the affected page in light and dark themes
- verify desktop and narrow/mobile layout if relevant
- verify any new hotkeys, buttons, and error states

After changing Docker behavior:

- rebuild the image
- run the container locally
- verify `/up` inside the running container

## Avoid

- Do not add unrelated formatting churn.
- Do not silently change persistence paths away from `/storage` for container usage.
- Do not replace existing session handling or auth flow without a clear need.
- Do not introduce multiple style systems into the templates.
