# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## Project Overview

WeChat public account (公众号) article fetcher and RSS subscription service. FastAPI backend that fetches articles via WeChat MP platform APIs, with anti-detection measures (Chrome TLS fingerprint simulation via `curl_cffi`, SOCKS5 proxy pool rotation, three-layer rate limiting).

**License:** AGPL-3.0 | **Language:** Python 3.8+ | **Framework:** FastAPI + Uvicorn

## Commands

```bash
# Run locally (dev mode with hot reload)
python app.py                    # reads HOST/PORT/DEBUG from .env

# Run via uvicorn directly
uvicorn app:app --host 0.0.0.0 --port 5000 --reload

# Docker
docker-compose up -d

# Install dependencies
pip install -r requirements.txt

# One-click scripts
bash start.sh                    # Linux/macOS (creates venv, installs deps, registers systemd service)
start.bat                        # Windows
```

No test suite exists in this repository. No linting configuration is present.

## Architecture

### Entry Point: `app.py`

Single FastAPI app with lifespan manager that:
1. Initializes SQLite DB (`init_db()`)
2. Starts RSS background poller (`rss_poller.start()`)
3. Starts login expiry monitor (`login_reminder.start()`)
4. Registers 10 route modules under prefixes `/api`, `/api/public`, `/api/admin`, `/api/login`, `/api/rss`

**Route registration order matters:** `articles.router` must be registered before `search.router` to avoid route conflicts (both mount under `/api/public`).

### Routes (`routes/`)

| Module | Prefix | Purpose |
|--------|--------|---------|
| `article.py` | `/api` | `POST /api/article` — fetch single article content by URL |
| `articles.py` | `/api/public` | `GET /articles` + `/articles/search` — article listing/search via WeChat MP API |
| `search.py` | `/api/public` | `GET /searchbiz` — search public accounts by name |
| `account.py` | `/api/public` | `GET /accountinfo` — account identity/verification info |
| `admin.py` | `/api/admin` | Status, logout, blacklist CRUD, categories CRUD, history fetch |
| `login.py` | `/api/login` | QR code login flow (session → qrcode → scan → bizlogin) |
| `rss.py` | `/api` | RSS subscribe/unsubscribe, poll trigger, RSS XML output, export |
| `health.py` | `/api` | Health check with proxy pool status |
| `image.py` | `/api` | Streaming image proxy (WeChat CDN only, 30MB cap) |
| `stats.py` | `/api` | Rate limit statistics |

### Utils (`utils/`) — All Singletons

Core logic lives here. Every utility module exports a singleton instance initialized at import time:

- **`auth_manager.py`** — Credential storage with 30s TTL cache. Dual storage: `data/.credentials.json` (Docker) + `.env` (local). All routes depend on this for WeChat tokens/cookies.
- **`http_client.py`** — `fetch_page()` function: `curl_cffi` with Chrome 120 TLS fingerprint, auto-fallback to `httpx`. Uses sync `CurlSession` + thread pool because `curl_cffi` async has SOCKS5 issues.
- **`proxy_pool.py`** — SOCKS5 proxy rotation with health tracking. Failed proxy gets 120s cooldown. Falls back to direct connection when all proxies are down.
- **`rate_limiter.py`** — Three-layer sliding window: global, per-IP, per-article interval.
- **`rss_store.py`** — SQLite (WAL mode) at `data/rss.db`. Tables: `subscriptions`, `articles`, `categories`, `fakeid_blacklist`. Article statuses: `pending` → `fetched` | `failed` (retryable) | `permanent_fail`.
- **`rss_poller.py`** — Background loop fetching article lists via WeChat MP API. Optionally enriches with full content. Auto-blacklists fakeids after 8 verification triggers (`WechatInvalidFakeidError`).
- **`rss_streaming.py`** — Streaming RSS XML generators (single account, aggregated, category-based, historical) to avoid loading entire feeds into memory.
- **`content_processor.py`** — Article content extraction, image URL proxy rewriting, HTML cleaning. Calls into `helpers.py` for type detection.
- **`helpers.py`** — HTML parsing, content type detection by `item_show_type` (0=rich text, 7=audio share, 8=image-text message, 10=short content), unavailability detection (deleted, banned, privacy, verification page). See `CONTENT_TYPES.md` for full reference.
- **`article_fetcher.py`** — Batch concurrent article content fetcher with proxy support.
- **`webhook.py`** — WeChat Work robot / generic webhook notifications with dedup interval.
- **`login_reminder.py`** — Checks credential expiry every 6h, sends 24h/6h warnings and expiry notifications via webhook.

### Key Data Flows

**Article fetch:** `routes/article.py` → `helpers.parse_article_url()` → `http_client.fetch_page()` → `content_processor.process_article_content()` → `helpers` type detection → proxy-rewritten HTML

**RSS poll cycle:** `rss_poller` loop → WeChat MP API (`appmsgpublish`) → `rss_store` save article metadata → optionally `article_fetcher` batch content fetch → `content_processor` → `rss_store` update with full content

**RSS output:** `routes/rss.py` → `rss_streaming` generators → streaming XML response (never holds full feed in memory)

## Configuration

All config via `.env` file (template at `env.example`). Key groups:

- **Auth** (auto-filled after login): `WECHAT_TOKEN`, `WECHAT_COOKIE`, `WECHAT_FAKEID`, `WECHAT_EXPIRE_TIME`
- **Rate limiting**: `RATE_LIMIT_GLOBAL`, `RATE_LIMIT_PER_IP`, `RATE_LIMIT_ARTICLE_INTERVAL`
- **RSS**: `RSS_POLL_INTERVAL`, `ARTICLES_PER_POLL`, `RSS_FETCH_FULL_CONTENT`
- **Proxy**: `PROXY_URLS` (comma-separated SOCKS5)
- **Webhook**: `WEBHOOK_URL`, `WEBHOOK_NOTIFICATION_INTERVAL`
- **Server**: `SITE_URL` (required for RSS image proxy), `PORT`, `HOST`, `DEBUG`

## Content Type System

WeChat articles have different formats identified by `item_show_type`. The detection and extraction logic is in `utils/helpers.py`. See `CONTENT_TYPES.md` for the complete reference on types, unavailability states, and extraction strategies.

**Critical parsing rule:** Always use strict regex matching for HTML tag detection (e.g., audio via `<mpvoice>` tag). Do NOT use simple `in` string checks — they match JS variable names and cause false positives.

## Database

SQLite at `data/rss.db` (WAL mode). Managed directly via `sqlite3` stdlib — no ORM. Schema is in `utils/rss_store.py`. Article `status` field lifecycle: `pending` → `fetched` (success) | `failed` (retryable, max 3 retries) | `permanent_fail` (deleted/banned/privacy).

## Frontend

Single-page HTML files in `static/` (no build step, no framework). Each page is self-contained with inline JS calling the API endpoints.
