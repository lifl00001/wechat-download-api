# CINNO Features Migration Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Migrate visual configuration, dynamic proxy, MySQL support, and article library from CINNO develop branch into the upstream project, stripping all CINNO-specific business logic.

**Architecture:** Add a database abstraction layer (`db_manager.py`) and settings manager (`settings_manager.py`) as foundation. Rewrite `proxy_pool.py` for dual-mode support. Migrate all `rss_store.py` SQL through `db_manager`. Add settings + article library UI and API endpoints.

**Tech Stack:** FastAPI, SQLite/MySQL (pymysql), curl_cffi, requests (for dynamic proxy API calls)

---

## Task 1: Add `utils/db_manager.py` — Database Abstraction Layer

**Files:**
- Create: `utils/db_manager.py`

This module provides a unified interface for SQLite and MySQL, auto-detected by environment variables. It is the foundation for all other tasks.

- [ ] **Step 1: Create `utils/db_manager.py`**

Copy from CINNO `origin/develop:utils/db_manager.py` verbatim. The module:

- Reads `DB_HOST`, `DB_DATABASE`, `DB_USERNAME`, `DB_PASSWORD` env vars to detect MySQL
- Falls back to SQLite if no MySQL config
- Provides: `get_conn()`, `fetchone()`, `fetchall()`, `execute()`, `commit()`, `adapt_sql()`, `adapt_executescript()`
- Wraps pymysql connection with sqlite3-compatible interface (`_MySQLConnectionWrapper`)
- SQL dialect adaptation: `?` → `%s`, `AUTOINCREMENT` → `AUTO_INCREMENT`, `INSERT OR IGNORE` → `INSERT IGNORE`

```bash
cd D:/workspace/cinno-wechat-collect && git show origin/develop:utils/db_manager.py > D:/studyspace/wechat-download-api/utils/db_manager.py
```

- [ ] **Step 2: Verify the file was created correctly**

Run: `python -c "from utils.db_manager import get_conn, fetchone, fetchall, execute, commit; print('OK')"`
Expected: `OK`

- [ ] **Step 3: Commit**

```bash
git add utils/db_manager.py
git commit -m "feat: add db_manager.py — dual SQLite/MySQL abstraction layer"
```

---

## Task 2: Add `utils/settings_manager.py` — Configuration Manager

**Files:**
- Create: `utils/settings_manager.py`

Pruned version of CINNO's settings manager with only core settings (22 items instead of 30). Depends on `db_manager`.

- [ ] **Step 1: Create `utils/settings_manager.py`**

Copy from CINNO and prune the `DEFAULT_SETTINGS` list to remove these CINNO-specific items:
- `skip_fetched_articles`
- `rss_max_full_content_fetch`
- `article_fetch_max_concurrency`
- `article_fetch_use_credentials`
- `article_list_use_proxy`
- `proxy_max_retries`
- `proxy_fail_cooldown`
- `history_max_batches`

The remaining 22 settings cover: site_url, port, host, rate limiting (3), RSS poll (3), proxy (11), webhook (2).

Key changes from CINNO version:
- Remove `proxy_fail_cooldown` and `proxy_max_retries` from `DEFAULT_SETTINGS`
- In `mark_failed()` method of proxy_pool, keep fixed 120s cooldown (not from settings)
- All other logic identical to CINNO

```bash
cd D:/workspace/cinno-wechat-collect && git show origin/develop:utils/settings_manager.py > /tmp/cinno_settings.py
```

Then manually edit `/tmp/cinno_settings.py` to remove the 8 excluded items from `DEFAULT_SETTINGS`, and copy to target:
```bash
cp /tmp/cinno_settings.py D:/studyspace/wechat-download-api/utils/settings_manager.py
```

- [ ] **Step 2: Verify the module loads**

Run: `python -c "from utils.settings_manager import settings_manager; print('OK, settings count:', len(settings_manager.list_all()))"`
Expected: `OK, settings count: 22`

- [ ] **Step 3: Commit**

```bash
git add utils/settings_manager.py
git commit -m "feat: add settings_manager.py — visual configuration with DB persistence"
```

---

## Task 3: Rewrite `utils/proxy_pool.py` — Dual-Mode Proxy Pool

**Files:**
- Modify: `utils/proxy_pool.py` (full rewrite)

Replace the current 121-line static-only proxy pool with the CINNO 445-line dual-mode version. Key adaptation: cooldown stays fixed at 120s (not from settings).

- [ ] **Step 1: Rewrite `utils/proxy_pool.py`**

Copy from CINNO `origin/develop:utils/proxy_pool.py` with one change:
- In `mark_failed()`, replace `settings_manager.get_int("proxy_fail_cooldown", 120)` with fixed `FAIL_COOLDOWN = 120`

The module provides:
- Static mode: identical behavior to current code, reads from `settings_manager`
- Dynamic mode: batch (periodic API refresh) or single (real-time per-request fetch)
- Settings watchers for hot-reload
- `_build_proxy_urls()`: injects protocol + auth credentials into raw proxy entries
- `_parse_plain_text()`: handles plain text responses with configurable separators
- `_extract_by_path()`: jq-style JSON path extraction
- External interface unchanged: `next()`, `mark_failed()`, `mark_ok()`, `get_status()`

```bash
cd D:/workspace/cinno-wechat-collect && git show origin/develop:utils/proxy_pool.py > D:/studyspace/wechat-download-api/utils/proxy_pool.py
```

Then edit `mark_failed` to use fixed cooldown:
```python
FAIL_COOLDOWN = 120  # fixed, not from settings

def mark_failed(self, proxy: str):
    with self._lock:
        self._fail_until[proxy] = time.time() + FAIL_COOLDOWN
    logger.warning("Proxy %s marked failed, cooldown %ds", proxy, FAIL_COOLDOWN)
```

- [ ] **Step 2: Verify import works**

Run: `python -c "from utils.proxy_pool import proxy_pool; print('OK, mode:', proxy_pool.mode)"`
Expected: `OK, mode: static`

- [ ] **Step 3: Commit**

```bash
git add utils/proxy_pool.py
git commit -m "feat: rewrite proxy_pool.py — dual static/dynamic proxy mode"
```

---

## Task 4: Migrate `utils/rss_store.py` to `db_manager`

**Files:**
- Modify: `utils/rss_store.py`

This is the largest task. Migrate all database operations from direct `sqlite3` calls to `db_manager` unified interface. Add three article library query functions.

- [ ] **Step 1: Update imports and remove SQLite-specific code**

Replace the top of `rss_store.py`:

```python
# REMOVE these imports:
import sqlite3
from pathlib import Path

# ADD this import:
from utils import db_manager
```

Remove `_get_conn()` function and `DB_PATH` variable entirely.

- [ ] **Step 2: Rewrite `init_db()` to use `db_manager`**

Replace all `conn.executescript(...)` with `db_manager.adapt_executescript(conn, ...)`.
Replace all `conn.execute(...)` with `db_manager.execute(conn, ...)`.
Replace `conn.fetchone()` with `db_manager.fetchone(conn, ...)`.
Replace `conn.fetchall()` with `db_manager.fetchall(conn, ...)`.
Replace `cursor.fetchone()` with `db_manager.fetchone(conn, sql, params)`.
Replace `cursor.fetchall()` with `db_manager.fetchall(conn, sql, params)`.

For `PRAGMA table_info(...)` calls (checking if columns exist), use `db_manager.check_column_exists()`.

For `sqlite_master` table check, use `db_manager.check_table_exists()`.

The ON CONFLICT UPSERT in `save_articles()` needs a MySQL branch:

```python
def save_articles(fakeid: str, articles: list, source: str = "poll") -> int:
    conn = db_manager.get_conn()
    inserted = 0
    try:
        for a in articles:
            content = a.get("content", "")
            plain_content = a.get("plain_content", "")
            try:
                if db_manager.USE_MYSQL:
                    cursor = db_manager.execute(conn, """
                        INSERT INTO articles
                        (fakeid, aid, title, link, digest, cover, author,
                         content, plain_content, publish_time, fetched_at, source)
                        VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s)
                        ON DUPLICATE KEY UPDATE
                        content = CASE WHEN VALUES(content) != '' AND articles.content = ''
                          THEN VALUES(content) ELSE articles.content END,
                        plain_content = CASE WHEN VALUES(plain_content) != '' AND articles.plain_content = ''
                          THEN VALUES(plain_content) ELSE articles.plain_content END,
                        author = CASE WHEN VALUES(author) != '' AND articles.author = ''
                          THEN VALUES(author) ELSE articles.author END
                    """, (...params...))
                else:
                    cursor = db_manager.execute(conn, """
                        INSERT INTO articles ...
                        ON CONFLICT(fakeid, link) DO UPDATE SET ...
                    """, (...params...))
                if cursor.rowcount > 0:
                    inserted += 1
            except db_manager.IntegrityError:
                pass
        db_manager.commit(conn)
        return inserted
    finally:
        db_manager.close_conn(conn)
```

- [ ] **Step 3: Update all remaining functions**

Pattern for every function:
- `conn = _get_conn()` → `conn = db_manager.get_conn()`
- `conn.execute(sql, params)` → `db_manager.execute(conn, sql, params)`
- `conn.execute(sql).fetchall()` → `db_manager.fetchall(conn, sql)`
- `conn.execute(sql).fetchone()` → `db_manager.fetchone(conn, sql)`
- `conn.commit()` → `db_manager.commit(conn)`
- `conn.close()` → `db_manager.close_conn(conn)`
- `conn.total_changes` → check `cursor.rowcount` instead
- `dict(r)` → already handled by db_manager (returns dicts)
- `sqlite3.IntegrityError` → `db_manager.IntegrityError`

Special cases:
- `INSERT OR IGNORE` → `db_manager.adapt_sql()` handles this
- `INSERT OR REPLACE` → `db_manager.adapt_sql()` handles this
- Window function queries (in `get_all_articles`, `get_articles_by_category`) — these work in both SQLite 3.25+ and MySQL 8+

- [ ] **Step 4: Add three article library functions**

Append at the end of `rss_store.py`:

```python
# ── 文章库查询 ─────────────────────────────────────────────

def list_cached_articles(limit: int = 20, offset: int = 0,
                         fakeid: str = None, source: str = None,
                         keyword: str = None) -> list:
    """分页查询缓存文章，支持按公众号/来源/关键词过滤"""
    conn = db_manager.get_conn()
    try:
        conditions = []
        params = []

        if fakeid:
            conditions.append("a.fakeid = ?")
            params.append(fakeid)
        if source:
            conditions.append("a.source = ?")
            params.append(source)
        if keyword:
            kw = f"%{keyword}%"
            conditions.append("(a.title LIKE ? OR a.digest LIKE ? OR a.author LIKE ? OR a.plain_content LIKE ?)")
            params.extend([kw, kw, kw, kw])

        where = (" WHERE " + " AND ".join(conditions)) if conditions else ""

        rows = db_manager.fetchall(
            conn,
            f"SELECT a.*, s.nickname FROM articles a "
            f"LEFT JOIN subscriptions s ON a.fakeid = s.fakeid"
            f"{where} ORDER BY a.publish_time DESC LIMIT ? OFFSET ?",
            (*params, limit, offset),
        )
        return rows
    finally:
        db_manager.close_conn(conn)


def count_cached_articles(fakeid: str = None, source: str = None,
                          keyword: str = None) -> int:
    """统计符合条件的文章数量"""
    conn = db_manager.get_conn()
    try:
        conditions = []
        params = []

        if fakeid:
            conditions.append("fakeid = ?")
            params.append(fakeid)
        if source:
            conditions.append("source = ?")
            params.append(source)
        if keyword:
            kw = f"%{keyword}%"
            conditions.append("(title LIKE ? OR digest LIKE ? OR author LIKE ? OR plain_content LIKE ?)")
            params.extend([kw, kw, kw, kw])

        where = (" WHERE " + " AND ".join(conditions)) if conditions else ""

        row = db_manager.fetchone(conn, f"SELECT COUNT(*) as cnt FROM articles{where}", tuple(params))
        return row["cnt"] if row else 0
    finally:
        db_manager.close_conn(conn)


def get_cached_article(article_id: int) -> Optional[Dict]:
    """获取单篇文章详情"""
    conn = db_manager.get_conn()
    try:
        return db_manager.fetchone(
            conn,
            "SELECT * FROM articles WHERE id = ?",
            (article_id,),
        )
    finally:
        db_manager.close_conn(conn)
```

Note: For MySQL compatibility, the `?` placeholders in these new functions will be auto-adapted by `db_manager.execute()` which calls `adapt_sql()` internally.

- [ ] **Step 5: Verify the module loads**

Run: `python -c "from utils.rss_store import init_db, list_cached_articles, count_cached_articles, get_cached_article; print('OK')"`
Expected: `OK`

- [ ] **Step 6: Commit**

```bash
git add utils/rss_store.py
git commit -m "feat: migrate rss_store.py to db_manager + add article library queries"
```

---

## Task 5: Update `routes/admin.py` — Settings + Article Library API

**Files:**
- Modify: `routes/admin.py`

Add four new endpoints: settings CRUD (GET/PUT) and article library (GET list + GET detail).

- [ ] **Step 1: Add imports**

At the top of `routes/admin.py`, add:

```python
from utils.settings_manager import settings_manager
from utils import rss_store
```

(Note: `rss_store` may already be imported; verify and add if missing.)

- [ ] **Step 2: Add settings endpoints**

Append before the history fetch section:

```python
# ── 系统配置 ─────────────────────────────────────────────

@router.get("/settings", summary="获取所有配置项")
async def get_settings():
    """获取所有系统配置"""
    items = settings_manager.list_all()
    return {"success": True, "data": items}


class SettingsUpdateRequest(BaseModel):
    settings: dict = Field(..., description="配置项键值对")


@router.put("/settings", summary="批量更新配置项")
async def update_settings(req: SettingsUpdateRequest):
    """批量更新系统配置"""
    settings_manager.set_many(req.settings)
    return {"success": True, "message": "配置已更新"}


# ── 文章库 ─────────────────────────────────────────────

@router.get("/articles", summary="获取文章库列表")
async def list_articles(
    page: int = Query(1, ge=1),
    page_size: int = Query(20, ge=1, le=100),
    fakeid: Optional[str] = Query(None),
    source: Optional[str] = Query(None),
    keyword: Optional[str] = Query(None),
):
    """分页查询文章库"""
    offset = (page - 1) * page_size
    articles = rss_store.list_cached_articles(
        limit=page_size, offset=offset,
        fakeid=fakeid, source=source, keyword=keyword,
    )
    total = rss_store.count_cached_articles(
        fakeid=fakeid, source=source, keyword=keyword,
    )
    return {
        "success": True,
        "data": {
            "articles": articles,
            "total": total,
            "page": page,
            "page_size": page_size,
        },
    }


@router.get("/articles/{article_id}", summary="获取文章详情")
async def get_article_detail(article_id: int):
    """获取单篇文章完整内容"""
    article = rss_store.get_cached_article(article_id)
    if not article:
        raise HTTPException(status_code=404, detail="文章不存在")
    return {"success": True, "data": article}
```

Add the necessary imports at the top:
```python
from fastapi import Query, HTTPException
from pydantic import BaseModel, Field
from typing import Optional
```

(Check which of these are already imported and only add missing ones.)

- [ ] **Step 3: Verify the API loads**

Run: `python -c "from routes.admin import router; print('OK, routes loaded')"`
Expected: `OK, routes loaded`

- [ ] **Step 4: Commit**

```bash
git add routes/admin.py
git commit -m "feat: add settings API + article library API to admin routes"
```

---

## Task 6: Update `app.py` — Initialization Order + New Route

**Files:**
- Modify: `app.py`

- [ ] **Step 1: Add articles page route**

After the existing `history_page()` route, add:

```python
@app.get("/articles.html", include_in_schema=False)
async def articles_page():
    """文章库页面"""
    return FileResponse(static_dir / "articles.html")
```

- [ ] **Step 2: Ensure initialization order in lifespan**

The current lifespan already calls `init_db()` then `rss_poller.start()`. Since `settings_manager` is a module-level singleton that initializes on import, and it depends on `db_manager` which is also import-time, no explicit initialization call is needed in `app.py`. The import chain handles it:

```
app.py imports rss_store → rss_store imports db_manager (auto-init)
settings_manager imports db_manager (auto-init)
proxy_pool imports settings_manager (auto-init)
```

The `init_db()` call in lifespan still works because `rss_store.init_db()` now uses `db_manager.get_conn()` internally.

Verify no changes needed to the lifespan function — just ensure the import order is correct at the top of `app.py`.

- [ ] **Step 3: Commit**

```bash
git add app.py
git commit -m "feat: add articles.html page route"
```

---

## Task 7: Add Static Files — common.css, common.js, articles.html

**Files:**
- Create: `static/common.css`
- Create: `static/common.js`
- Create: `static/articles.html`

- [ ] **Step 1: Copy common.css and common.js from CINNO**

```bash
cd D:/workspace/cinno-wechat-collect
git show origin/develop:static/common.css > D:/studyspace/wechat-download-api/static/common.css
git show origin/develop:static/common.js > D:/studyspace/wechat-download-api/static/common.js
```

Edit `static/common.js` to remove CINNO-specific behavior:
- Remove Bearer token logic from `apiFetch()` — replace with plain `fetch()`
- Remove logout redirect to `/login-page.html` — redirect to `/login.html` instead
- Remove `cinno_status` related rendering helpers if present

The simplified `apiFetch()`:
```javascript
async function apiFetch(url, options = {}) {
    const resp = await fetch(url, options);
    return resp;
}
```

- [ ] **Step 2: Copy articles.html from CINNO and strip CINNO-specific features**

```bash
cd D:/workspace/cinno-wechat-collect
git show origin/develop:static/articles.html > D:/studyspace/wechat-download-api/static/articles.html
```

Then edit `articles.html` to remove:
- All CINNO-related UI elements (send to CINNO buttons, CINNO status badges, batch CINNO send)
- All `cinno_status`, `sendToCinno()`, `batchSendToCinno()`, `openCinnoLogs()` JavaScript functions
- CINNO-specific API calls (`/api/admin/articles/{id}/send-to-cinno`, `/batch-send-to-cinno`, `/cinno-logs`)
- Replace `apiFetch()` calls with plain `fetch()` calls
- Remove Bearer token headers from fetch options

Keep all article library features:
- Paginated list with cover images
- Filter by fakeid (public account)
- Filter by source (poll/deep_fetch)
- Keyword search (title, digest, author, content)
- Article detail modal with HTML/plain text toggle
- Link to original WeChat article

- [ ] **Step 3: Verify files are valid HTML**

Open `http://localhost:5000/articles.html` in a browser after starting the server. Check:
- Navigation loads
- Article list renders (or shows empty state)
- Filters work
- Article detail modal opens on click

- [ ] **Step 4: Commit**

```bash
git add static/common.css static/common.js static/articles.html
git commit -m "feat: add article library page with shared CSS/JS"
```

---

## Task 8: Update `static/admin.html` — Settings Panel

**Files:**
- Modify: `static/admin.html`

- [ ] **Step 1: Add settings section HTML**

Add a "系统设置" section to admin.html with grouped configuration inputs. The section should include:

1. A tab/group structure matching the settings groups:
   - 基础配置 (site_url, port, host)
   - 限频配置 (rate_limit_global, rate_limit_per_ip, rate_limit_article_interval)
   - RSS 配置 (rss_poll_interval, articles_per_poll, rss_fetch_full_content)
   - 代理配置 (proxy_mode toggle + static fields + dynamic fields)
   - Webhook 配置 (webhook_url, webhook_notification_interval)

2. Proxy mode toggle behavior:
   - When `proxy_mode=static`: show `proxy_urls` textarea
   - When `proxy_mode=dynamic`: show all `dynamic_proxy_*` fields

3. JavaScript logic:
   - On page load: `fetch('/api/admin/settings')` → populate form
   - On save: `fetch('/api/admin/settings', {method:'PUT', body: JSON.stringify({settings: {...}})})`
   - Proxy mode dropdown `onchange` handler to show/hide relevant sections

4. Add navigation link to articles.html

This is the most UI-intensive task. Reference CINNO's admin.html settings section for layout, but strip CINNO-specific fields.

- [ ] **Step 2: Verify settings UI works**

Start the server, open `http://localhost:5000/admin.html`, check:
- Settings section loads with current values
- Changing values and clicking save persists (refresh page to verify)
- Proxy mode toggle shows/hides correct fields

- [ ] **Step 3: Commit**

```bash
git add static/admin.html
git commit -m "feat: add visual settings panel to admin page"
```

---

## Task 9: Update `requirements.txt` and `env.example`

**Files:**
- Modify: `requirements.txt`
- Modify: `env.example`

- [ ] **Step 1: Update requirements.txt**

Add two new dependencies:

```
pymysql>=1.1.0
requests>=2.31.0
```

Final `requirements.txt`:
```
fastapi==0.104.1
uvicorn[standard]==0.24.0
pydantic==2.5.0
httpx==0.25.2
python-dotenv==1.0.0
curl_cffi>=0.7.0
beautifulsoup4>=4.12.0
pymysql>=1.1.0
requests>=2.31.0
```

- [ ] **Step 2: Update env.example**

Add new optional sections after the existing `PROXY_URLS` section:

```env
# MySQL 数据库（可选，留空则使用 SQLite）
# DB_HOST=
# DB_PORT=3306
# DB_DATABASE=
# DB_USERNAME=
# DB_PASSWORD=

# 动态代理配置（可选，proxy_mode=dynamic 时使用）
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

- [ ] **Step 3: Commit**

```bash
git add requirements.txt env.example
git commit -m "feat: add pymysql/requests deps + MySQL and dynamic proxy config"
```

---

## Task 10: Integration Test — Start Server and Verify All Features

**Files:**
- None (verification only)

- [ ] **Step 1: Install new dependencies**

```bash
pip install -r requirements.txt
```

- [ ] **Step 2: Start the server**

```bash
python app.py
```

Expected: Server starts without errors, shows "Settings: seeded default ..." log lines.

- [ ] **Step 3: Verify health check**

```bash
curl -s http://localhost:5000/api/health
```

Expected: `{"status":"healthy",...,"proxy_pool":{"mode":"static","enabled":false,...}}`

- [ ] **Step 4: Verify settings API**

```bash
curl -s http://localhost:5000/api/admin/settings | python -m json.tool
```

Expected: Returns 22 settings with descriptions.

- [ ] **Step 5: Verify settings update**

```bash
curl -s -X PUT http://localhost:5000/api/admin/settings \
  -H "Content-Type: application/json" \
  -d '{"settings":{"rate_limit_global":"15"}}'
```

Expected: `{"success":true,"message":"配置已更新"}`

Then verify it persisted:
```bash
curl -s http://localhost:5000/api/admin/settings | python -c "import sys,json; [print(s['key_name'],s['value']) for s in json.load(sys.stdin)['data'] if s['key_name']=='rate_limit_global']"
```

Expected: `rate_limit_global 15`

- [ ] **Step 6: Verify article library API**

```bash
curl -s "http://localhost:5000/api/admin/articles?page=1&page_size=5"
```

Expected: `{"success":true,"data":{"articles":[...],"total":N,...}}`

- [ ] **Step 7: Verify pages load**

Open in browser:
- `http://localhost:5000/admin.html` — should show settings panel
- `http://localhost:5000/articles.html` — should show article library

- [ ] **Step 8: Verify backward compatibility**

Confirm existing features still work:
- `http://localhost:5000/api/rss/subscriptions` returns subscription list
- `http://localhost:5000/api/rss/all` returns RSS feed
- `http://localhost:5000/api/health` returns healthy status
