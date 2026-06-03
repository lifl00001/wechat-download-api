# CINNO Features Migration Design

Date: 2026-06-03

## Goal

Migrate four specific features from the CINNO `develop` branch into the upstream `wechat-download-api` project, stripping all CINNO-specific business logic (ES, Kafka, JWT auth, quest enums).

## Features to Migrate

1. **Visual configuration** — Web UI for all settings, persisted to database
2. **Dynamic proxy** — API-based proxy pool (e.g., 快代理) in addition to static proxies
3. **MySQL support** — Dual SQLite/MySQL backend via a database abstraction layer
4. **Article library** — Browse, search, and view cached articles in the web UI

## Architecture

### New Modules

#### `utils/db_manager.py` (from CINNO, unchanged)

Database abstraction layer. Auto-detects MySQL vs SQLite based on environment variables (`DB_HOST`, `DB_DATABASE`, `DB_USERNAME`, `DB_PASSWORD`). Provides unified interface: `get_conn()`, `fetchone()`, `fetchall()`, `execute()`, `commit()`, `adapt_sql()`. Includes SQL dialect adaptation (`?` → `%s`, `AUTOINCREMENT` → `AUTO_INCREMENT`, etc.).

#### `utils/settings_manager.py` (from CINNO, pruned)

Singleton configuration manager. Persists settings to `wechat_api_settings` table. Falls back to environment variables when no database value exists. Supports watcher callbacks for live configuration changes.

**Core settings (22 items):**

| Group | Keys |
|-------|------|
| Base | `site_url`, `port`, `host` |
| Rate limiting | `rate_limit_global`, `rate_limit_per_ip`, `rate_limit_article_interval` |
| RSS | `rss_poll_interval`, `articles_per_poll`, `rss_fetch_full_content` |
| Proxy | `proxy_mode`, `proxy_urls`, `dynamic_proxy_api_url`, `dynamic_proxy_refresh_interval`, `dynamic_proxy_jq_path`, `dynamic_proxy_protocol`, `dynamic_proxy_batch_mode`, `dynamic_proxy_format`, `dynamic_proxy_separator`, `dynamic_proxy_username`, `dynamic_proxy_password` |
| Webhook | `webhook_url`, `webhook_notification_interval` |

**Excluded CINNO-specific items:** `article_fetch_use_credentials`, `article_list_use_proxy`, `proxy_max_retries`, `proxy_fail_cooldown`, `skip_fetched_articles`, `rss_max_full_content_fetch`, `article_fetch_max_concurrency`, `history_max_batches`.

### Modified Modules

#### `utils/proxy_pool.py` — Dual-mode proxy pool

- **Static mode** (default): Reads `proxy_urls` from settings_manager, round-robin rotation. Identical behavior to current project.
- **Dynamic mode**: Periodically calls a vendor API (configured via `dynamic_proxy_api_url`), extracts proxy list from JSON or plain-text response, auto-refreshes on expiry.
- All configuration reads via `settings_manager.get()` instead of `os.getenv()`.
- Registers watchers on proxy-related settings for live reconfiguration.
- External interface unchanged: `next()`, `mark_failed()`, `mark_ok()`, `get_status()`.

#### `utils/rss_store.py` — Database abstraction migration

- All `sqlite3.connect()` → `db_manager.get_conn()`
- All `conn.execute()` → `db_manager.execute()`
- All direct SQL operations → `db_manager.fetchone()`, `db_manager.fetchall()`
- `ON CONFLICT` and other SQLite-specific syntax gets MySQL branches
- Three new functions for article library:
  - `list_cached_articles(limit, offset, fakeid, source, keyword)` — paginated query, JOINs subscriptions for display names
  - `count_cached_articles(fakeid, source, keyword)` — count matching articles
  - `get_cached_article(article_id)` — single article detail by ID

#### `routes/admin.py` — Settings API + Article library API

New endpoints:

| Method | Path | Purpose |
|--------|------|---------|
| `GET` | `/api/admin/settings` | Return all settings with descriptions |
| `PUT` | `/api/admin/settings` | Batch update settings, trigger watchers |
| `GET` | `/api/admin/articles` | Paginated article list with filters |
| `GET` | `/api/admin/articles/{article_id}` | Single article detail |

#### `app.py` — New route + initialization order

- New page route: `GET /articles.html`
- Startup order: `db_manager` ready → `settings_manager` init → `init_db()` → `rss_poller.start()` → `login_reminder.start()`

### New Static Files

| File | Source | Purpose |
|------|--------|---------|
| `static/articles.html` | From CINNO | Article library browser with search, filter, detail view |
| `static/common.css` | From CINNO | Shared styles |
| `static/common.js` | From CINNO | Shared utilities |

### Modified Static Files

#### `static/admin.html` — Settings panel

Adds a settings section with grouped configuration inputs. On load, fetches from `GET /api/admin/settings`. On save, calls `PUT /api/admin/settings`. Proxy mode toggle dynamically shows/hides static vs dynamic proxy fields. Also adds navigation link to "文章库".

### Dependency Updates (`requirements.txt`)

```
+ pymysql>=1.1.0       # MySQL backend
+ requests>=2.31.0     # Dynamic proxy API calls
```

### Configuration Updates (`env.example`)

New optional sections:

```env
# MySQL (optional, leave empty for SQLite)
# DB_HOST=
# DB_PORT=3306
# DB_DATABASE=
# DB_USERNAME=
# DB_PASSWORD=

# Dynamic proxy (optional, used when proxy_mode=dynamic)
# PROXY_MODE=static
# DYNAMIC_PROXY_API_URL=
# DYNAMIC_PROXY_REFRESH_INTERVAL=300
# DYNAMIC_PROXY_JQ_PATH=.data.proxy_list
# DYNAMIC_PROXY_PROTOCOL=socks5
# DYNAMIC_PROXY_BATCH_MODE=batch
# DYNAMIC_PROXY_FORMAT=json
# DYNAMIC_PROXY_SEPARATOR=newline
# DYNAMIC_PROXY_USERNAME=
# DYNAMIC_PROXY_PASSWORD=
```

## NOT Migrated

- `utils/cinno_engine_client.py` (ES + Kafka)
- `utils/quest_enums.py` (CINNO business enums)
- `utils/jwt_util.py` (JWT auth)
- `routes/auth.py` (system login)
- `static/login-page.html` (system login page)
- ES_HOST, KAFKA_BOOTSTRAP_SERVERS, CINNO_QID, CINNO_Q_TYPE, CINNO_JOB_TYPE configs
- All CINNO Engine send logic in rss_poller.py

## File Change Summary

| File | Action |
|------|--------|
| `utils/db_manager.py` | **New** — from CINNO |
| `utils/settings_manager.py` | **New** — from CINNO, pruned |
| `utils/proxy_pool.py` | **Rewrite** — add dynamic mode |
| `utils/rss_store.py` | **Modify** — db_manager + article library queries |
| `routes/admin.py` | **Modify** — settings API + article library API |
| `app.py` | **Modify** — new route + init order |
| `static/admin.html` | **Modify** — settings panel |
| `static/articles.html` | **New** — from CINNO |
| `static/common.css` | **New** — from CINNO |
| `static/common.js` | **New** — from CINNO |
| `requirements.txt` | **Modify** — add pymysql, requests |
| `env.example` | **Modify** — add MySQL + dynamic proxy options |
