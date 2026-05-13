# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## Environment

- Python 3.13.2 (CPython)
- Virtual environment at `.venv/` — activate with `source .venv/bin/activate`
- IDE: PyCharm (`.idea/` config present)

## Setup

```bash
source .venv/bin/activate
pip install -r requirements.txt
```

## Running the application

### Local development
```bash
source .venv/bin/activate
python run.py
# App runs at http://127.0.0.1:8000
```

Or directly with uvicorn:
```bash
uvicorn app.main:app --reload
```

### First-time setup
1. Copy `.env.example` to `.env`
2. Generate a password hash: `python generate_password_hash.py`
3. Set `APP_PASSWORD_HASH` in `.env` with the generated hash
4. Set `SECRET_KEY` to a long random string in `.env`
5. Run the app — the SQLite DB is created automatically at `data/coldplunge.db`

## Project structure

```
app/
  main.py          # FastAPI app and all routes
  models.py        # SQLAlchemy models (Transaction, Location)
  database.py      # DB engine/session setup
  auth.py          # bcrypt password check, itsdangerous session tokens
  templates/       # Jinja2 HTML templates
    base.html
    login.html
    dashboard.html
    transactions.html
    edit_transaction.html
    locations.html
static/
  style.css        # All styles (dark blue/teal mobile-first)
data/              # SQLite database file (gitignored)
requirements.txt
render.yaml        # Render.com deployment config
.env.example
run.py             # Local dev entrypoint
generate_password_hash.py
```

## Deployment (Render.com)

1. Push to GitHub
2. Create a new Web Service on Render, connect the repo
3. Render auto-detects `render.yaml`
4. Set `APP_PASSWORD_HASH` and `SECRET_KEY` as secret environment variables in Render dashboard

**Important**: Render free tier has ephemeral disk — the SQLite database is wiped on each deploy/restart.
For persistence, either:
- Upgrade to Render's persistent disk add-on
- Migrate to PostgreSQL (change `DATABASE_URL` to a Postgres connection string; SQLAlchemy handles it)

## Architecture notes

- Auth: single shared password → bcrypt verify → `itsdangerous.URLSafeTimedSerializer` signs a session token stored in `session_token` cookie
- All pages are server-rendered Jinja2 templates (no JS fetch API)
- Forms use standard HTML POST; category options are updated via vanilla JS `onchange`
- Categories are hard-coded in `models.py` (`INCOME_CATEGORIES`, `EXPENSE_CATEGORIES`) and passed to all templates as Jinja2 globals
