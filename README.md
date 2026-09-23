# TaleTomo — AI web-novel maker

**Grow a premise into a world.** TaleTomo is an in-development, server-rendered Django application for planning and drafting serialized fiction. Projects can target **1–4,000 chapters**; that is a supported configuration bound, **not** proof that a complete 4,000-chapter writing workflow has been validated.

> **Status (September 23, 2026, WIB): development prototype, not a production release.** This working tree has local, uncommitted implementation changes. See [current architecture and verified gaps](docs/ARCHITECTURE.md); the [PRD](docs/PRD.md) is the **target specification**, not a list of completed features.

## Current capabilities

- Login-protected, owner-scoped project and job pages; server-rendered pages with small Vue islands. On an empty installation, the login page guides the first user through browser-based account setup; setup closes once an account exists. Additional administrative accounts can be created with Django's `createsuperuser`.
- One-page project creation, editable story bible and chapter contracts, a scaffolded first volume/arc and initial five chapter plans. Plans for later volumes/arcs and true rolling-horizon advancement are not generated yet.
- Fake provider for offline tests and an OpenAI-compatible API adapter; per-user provider settings with encrypted keys. A real provider/context-capability verification workflow is not complete.
- Chapter drafting through a Celery job record, versioned prose, heuristic/LLM continuity findings, draft approval, and a guarded canon commit. Proposed facts are **not** extracted and reviewed from prose; the current commit view creates a summary event with no extracted facts.
- Markdown export and checksummed JSON import/export for a **subset** of project state. Restore creates a new project and is not a complete or idempotent backup of all application records.
- SQLite fallback for local development; Compose defines PostgreSQL, Redis, web and worker services.

**Not yet implemented or proven:** real hybrid/full-text/vector retrieval, end-to-end verified 250k-token provider support, scene-wise long-chapter generation, robust cancellation/retry/lease recovery and spending limits, complete backup parity, production deployment hardening, and a full 4,000-chapter continuity/load gate. Do not use this prototype to store irreplaceable manuscripts or production API credentials without an independent backup and security review.

## Quickstart (local development)

Requirements: Python 3.12+ and the dependencies in `pyproject.toml`. From the repository root:

```bash
uv venv .venv --python 3.12
uv pip install --python .venv/Scripts/python.exe -e ".[dev]"  # Windows
# On macOS/Linux, use .venv/bin/python instead.
.venv/Scripts/python.exe manage.py migrate
.venv/Scripts/python.exe manage.py createsuperuser
```

With no `DATABASE_URL`, Django uses local SQLite. Configure a **new, private** Django `SECRET_KEY` and Fernet-compatible `TALETOMO_ENCRYPTION_KEY` for anything beyond disposable development. Do not commit `.env` files. Environment variable names and fallbacks are in `config/settings.py`. There is **no** `.env.example` in this repository; do not assume Django loads `.env` automatically.

For a one-process fake-provider demo without Redis, explicitly set `CELERY_ALWAYS_EAGER=True` and run `.venv/Scripts/python.exe manage.py runserver`. Eager mode executes generation **inside the request**: it is not asynchronous. For actual background jobs, set `CELERY_ALWAYS_EAGER=False`, start Redis and a Celery worker, and then run the web server. On Windows use a compatible Celery pool (for example, `--pool=solo`); Compose runs the worker in Linux.

> On the Hermes desktop host, an inherited `PYTHONPATH` can mix package installations from different Python versions. In Git Bash, prefix project commands with `env -u PYTHONPATH` if imports come from outside `.venv`.

## Docker Compose (development only)

```bash
docker compose config --quiet
docker compose up --build
```

The development web server is configured at **http://localhost:8088/**, PostgreSQL at host port 5433, and Redis at host port 6380. The web service runs migrations before `runserver`. Compose uses development credentials, `DEBUG=True`, a source bind mount and Django's development server; **do not deploy this file as production configuration**. Configure secrets and external ingress separately before any public exposure. Compose service details are documented in [current architecture](docs/ARCHITECTURE.md).

## Checks

```bash
# Git Bash / Windows host; remove the unrelated inherited PYTHONPATH:
env -u PYTHONPATH ./.venv/Scripts/python.exe -m pytest -q
env -u PYTHONPATH ./.venv/Scripts/python.exe manage.py check
env -u PYTHONPATH ./.venv/Scripts/python.exe manage.py makemigrations --check --dry-run
env -u PYTHONPATH ./.venv/Scripts/python.exe manage.py check --deploy
```

At this review, **31 tests passed**; `check` and migration parity passed. `check --deploy` reported **six security warnings** with the default development settings. Passing tests exercise a happy path and selected regression boundaries, not all PRD acceptance or production gates.

## Documentation

- [Current-state architecture, evidence, limitations, and PRD comparison](docs/ARCHITECTURE.md)
- [Product requirements and acceptance criteria (target design)](docs/PRD.md)

`README.md` is also required by `pyproject.toml` for packaging. The earlier `IMPLEMENTATION_REVIEW.md` is already absent from this working tree; its historical conclusions have been reassessed in the current architecture document rather than preserved as a stale second status report.
