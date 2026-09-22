# TaleTomo — AI Web-Novel Maker

> **Grow a premise into a world.**

TaleTomo is an AI-assisted web-novel creation platform designed to support projects from 1 to 4,000 chapters. It preserves long-term continuity through structured story state, hierarchical planning horizons, machine-checkable chapter contracts, and atomic canon commits.

## Architecture

- **Backend**: Django 5.1 modular monolith with Celery background workers.
- **Database**: PostgreSQL 16 (with pgvector support).
- **Transport**: Redis 7.
- **Frontend**: Server-rendered multi-page templates + vendored page-local Vue 3 islands (WCAG 2.2 AA compliant).
- **Security**: AES-GCM credential encryption at rest, strict outbound SSRF defense, tenant isolation.
- **Long-term Continuity**: Two-phase canon review, hybrid retrieval with anti-leakage filters, deterministic integrity checker.

## Quickstart

```bash
# Setup virtual environment
uv venv .venv --python 3.12
.venv\Scripts\activate

# Install dependencies
uv pip install -e ".[dev]"

# Run tests
pytest
```
