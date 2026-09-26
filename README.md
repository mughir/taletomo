# TaleTomo — AI web-novel maker

**Grow a premise into a world.** TaleTomo is a server-rendered Django application for planning and drafting serialized fiction. Projects can target **1–4,000 chapters**; that is a supported configuration bound, **not** proof that a complete 4,000-chapter writing workflow has been validated.

> **Status (September 26, 2026, WIB): development prototype, not a production release.** The working tree carries architecture improvements (lease heartbeat, stale-job recovery, canon extraction and review, relevance-ranked context assembly) on top of `main` at commit `ea775c7`. See [current architecture and verified gaps](docs/ARCHITECTURE.md); the [PRD](docs/PRD.md) is the **target specification**, not a list of completed features.

## Current capabilities

- **Authentication & Setup:** Login-protected, owner-scoped project and job pages; server-rendered pages with small Vue islands. On an empty installation, the login page guides the first user through browser-based account setup; setup closes once an account exists. Additional administrative accounts can be created with Django's `createsuperuser`.
- **Style Dictionary:** Genre, subgenre, tone, POV, tense, pacing, and protagonist type are backed by a seeded dictionary where every term carries a definition and an example. Multi-value axes (genre, subgenre, tone) use a chip picker — click terms to combine several ("Fantasy / Xianxia", "Grim, Mysterious") or type your own — while single axes suggest inline; a management page lets authors add their own terms. During generation, the project's chosen terms are resolved and their definitions/examples ride into the drafting prompt, so the model shares the author's vocabulary instead of guessing what a genre means.
- **Hierarchical Planning:** Single-form project creation, editable story bible (pitch, themes, style constraints, world rules), series spine, chapter contracts with scene plans, and a scaffolded first volume/arc with initial chapter plans. True rolling-horizon automatic advancement remains target design.
- **BYOK & Provider Security:** Fake provider for offline tests and an OpenAI-compatible API adapter with Fernet-encrypted keys at rest. Custom provider endpoints are guarded against SSRF (rejecting loopback, cloud metadata, private subnets, CGNAT, and unsafe URI schemes). Context window limits are configurable with up to 250k-token budget support.
- **Drafting & Job Safety:** Asynchronous chapter drafting powered by Celery and Redis. Generation jobs enforce atomic database row leases kept alive by a worker heartbeat during long provider calls (so a slow draft can never let a second worker double-generate), cooperative cancellation checks, draft versioning, heuristic/LLM continuity checks, and budget reservations derived from the assembled context and the model's pricing profile (held when provider timeouts leave billing outcomes unconfirmed, preventing blind retries). The output window is sized from the chapter's word target (never truncating long chapters), prompts carry the previous chapter's closing prose and per-scene word budgets for seamless flow, and the OpenAI-compatible adapter retries pre-submission failures (connection errors, 429) exactly once — timeouts after submission remain billing-unknown. A periodic stale-job reaper (Celery Beat + `manage.py reap_stale_jobs`) recovers jobs abandoned by dead workers: jobs that never reached the provider are requeued automatically, while unknown-outcome jobs fail with reservations held for manual reconciliation.
- **Canon Extraction & Review:** After drafting, a structured extraction pass proposes story events, subject–predicate–value canon facts, plot-thread updates, and **character-state updates** (new wounds, deaths, goal changes) from the prose. Proposals are inert until the author approves or rejects them on a per-chapter review page; only approved proposals flow into the atomic canon commit (facts get provenance, thread updates advance or close plot threads, character updates rewrite the cast records that continuity checking depends on), and committed proposals are marked consumed so the review trail stays auditable. Extraction skips claims canon already confirms and only proposes character updates for known cast members.
- **Guarded Canon Commit:** Two-phase lifecycle separating draft prose approval from atomic canonical story commits. Canon commits strictly enforce project ownership, branch-head verification, and require a non-empty rationale to override open blocker findings.
- **Continuity Guardrails:** Deterministic checks run on generated *and* manually saved drafts, deduplicated so re-saves never pile up identical findings. Injury detection is derived from each character's wound record (side + limb + impairment wording) instead of a fixed phrase list, so a shattered knee flags "his right knee screamed" while a fear of crowds blocks nothing.
- **Backup & Export:** Markdown export and SHA-256 verified JSON backup/restore supporting projects, bibles, spines, volumes, arcs, chapters, plans, scenes, versioned drafts (retaining parent lineage), characters, locations, factions, world rules, timeline events, plot threads, story events, and confirmed canon facts. Restore creates a new isolated project.
- **Local Development:** SQLite fallback for zero-dependency development; Docker Compose environment configuring PostgreSQL 16 (pgvector image), Redis 7, web, Celery worker, and Celery Beat services.

**Not yet implemented or proven:** full-text/vector retrieval ranking (entity selection is relevance-ranked over bounded ORM queries, but there is no inverted index, embedding model, or hybrid merge), end-to-end verified 250k-token live provider calls, scene-wise multi-prompt long-chapter generation, production deployment hardening (HTTPS, HSTS, secrets rotation), and a full 4,000-chapter continuity/load verification gate. Do not use this prototype to store irreplaceable manuscripts or production API credentials without an independent backup and security review.

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

At this review:
- **`pytest`:** **109 passed, 1 skipped** in ~20 s (PostgreSQL lease concurrency test skipped cleanly under SQLite fallback). Suites cover lease-heartbeat behavior, stale-job reaper policy, canon extraction/review/commit flow (including character-state updates), relevance-ranked retrieval, prompt-quality sizing, generalized continuity detection, provider retry semantics, and the style dictionary (including the protagonist-type axis).
- **`manage.py check`:** System check identified **0 issues**.
- **`makemigrations --check --dry-run`:** **No changes detected**; migrations match models.
- **`docker compose config --quiet`:** Parses **five services** (db, redis, web, worker, beat) with expected published ports.
- **`check --deploy`:** Reported **six security warnings** with default development settings (HSTS, SSL redirect, secret key, session/CSRF cookies, DEBUG).

Passing tests exercise core domain isolation, security boundaries, and selected regression scenarios; they are not a substitute for deployed-system verification or full PRD acceptance gates.

## Documentation

- [Current-state architecture, evidence, limitations, and PRD comparison](docs/ARCHITECTURE.md)
- [Product requirements and acceptance criteria (target design)](docs/PRD.md)

`README.md` is also required by `pyproject.toml` for packaging. Historical implementation review notes have been reassessed and consolidated in the current architecture document.
