# feedr

A modern recreation of Google Reader built as a server-rendered FastAPI application for subscribing to feeds, reading articles, organizing subscriptions, and sharing articles with friends.

## What It Does

- Google OAuth login
- Local username/password login for development and testing
- Feed subscription and removal
- Folder organization
- Shared normalized feed/article storage across subscriptions
- Read and unread tracking
- Unread-only default reader view
- Article sharing with friends
- Friend requests, acceptance, decline, removal, and profile management
- OPML import and export
- Full-text article search
- Keyboard shortcuts and dark mode
- Mobile-friendly reader layout
- Initial automated test coverage with `pytest`

## Stack

- FastAPI
- Starlette sessions
- Jinja2 templates with inline CSS and JavaScript
- SQLAlchemy ORM
- SQLite by default
- Docker image deployment on port `80`

## Project Layout

- `main.py`: application entry point, data models, schema bootstrap, API routes, background fetcher, and HTML routes
- `templates/`: server-rendered UI templates
- `static/`: static assets
- `tests/`: pytest-based automated tests
- `docs/`: architecture and decision records
- `Dockerfile`: production container build
- `migrate_v1_to_v2.py`: migration utility for older schema/data

## Quick Start

```bash
python -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
cp .env.example .env
uvicorn main:app --reload --port 80
```

Open `http://localhost/login`.

## Environment

Important environment variables:

- `SECRET_KEY`: session signing key
- `GOOGLE_CLIENT_ID`: Google OAuth client id
- `GOOGLE_CLIENT_SECRET`: Google OAuth client secret
- `APP_URL`: external base URL used for OAuth callback generation
- `DATABASE_URL`: defaults to `sqlite:////storage/feedr.db`
- `LOCAL_AUTH_ENABLED`: enables local auth outside localhost when truthy
- `FEEDR_DISABLE_BACKGROUND_FETCHER`: disables the background fetch thread, mainly for tests

## Authentication

### Google OAuth

1. Go to [Google Cloud Console](https://console.cloud.google.com/apis/credentials)
2. Create OAuth 2.0 credentials
3. Add `http://localhost/auth/callback` as an authorized redirect URI
4. Copy the client id and secret into `.env`

### Local Auth

Local auth is enabled automatically on localhost and can be enabled elsewhere with `LOCAL_AUTH_ENABLED=true`.

- The first successful local login creates the account automatically.
- Local accounts use `username@local.feedr` when you sign in without an email address.
- This flow exists mainly for local development, testing, and demos.

## Development Workflow

Run locally with reload:

```bash
uvicorn main:app --reload --port 80
```

Run locally on a different port:

```bash
uvicorn main:app --reload --port 8000
```

Basic health check:

```bash
curl http://localhost/up
```

Basic syntax check:

```bash
python -m py_compile main.py
```

Run tests:

```bash
pytest
```

Run a single test file:

```bash
pytest tests/test_reader_and_friends.py
```

## Docker

Build the image:

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

The container stores data under `/storage` for Once compatibility.

## Health Endpoint

`GET /up` returns:

```json
{
  "status": "ok",
  "service": "feedr",
  "timestamp": "2026-04-15T...",
  "version": "0.1.0"
}
```

## Architecture Docs

- [Architecture Overview](docs/architecture.md)
- [Scaling ADR](docs/adr/0001-scaling-strategy.md)

## Current Constraints

- The application is still centered in a single `main.py` file.
- SQLite is the default persistence layer and works best for single-node deployment.
- Background feed refresh currently runs as an in-process thread.
- Templates keep CSS and JavaScript inline rather than using a separate frontend build.

Those choices keep the app simple today, but they shape the scaling options discussed in the ADR.
