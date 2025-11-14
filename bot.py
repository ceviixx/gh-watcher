#!/usr/bin/env python3
import os
import re
import json
import time
import signal
import logging
from logging.handlers import RotatingFileHandler
from pathlib import Path
from typing import Dict, Any, List, Tuple, Optional
from datetime import datetime
import requests

# -------------------------
# Configuration via ENV
# -------------------------
REPOS = [r.strip() for r in os.getenv("REPOS", "").split(",") if r.strip()]

DISCORD_WEBHOOK = os.getenv("DISCORD_WEBHOOK_URL", "")
DISCORD_USERNAME = os.getenv("DISCORD_USERNAME", "")
DISCORD_AVATAR_URL = os.getenv("DISCORD_AVATAR_URL", "")
USE_EMBEDS = os.getenv("USE_EMBEDS", "false").lower() in {"1","true","yes"}

# Only ONE optional token. If empty, the bot uses the public API.
GITHUB_TOKEN = os.getenv("GITHUB_TOKEN", "").strip()
GITHUB_API_BASE = os.getenv("GITHUB_API_BASE", "https://api.github.com")

POLL_INTERVAL = int(os.getenv("POLL_INTERVAL", "300"))
ONLY_LATEST = os.getenv("ONLY_LATEST", "false").lower() in {"1","true","yes"}
INCLUDE_PRERELEASE = os.getenv("INCLUDE_PRERELEASE", "true").lower() in {"1","true","yes"}
INCLUDE_DRAFT = os.getenv("INCLUDE_DRAFT", "false").lower() in {"1","true","yes"}

NOTIFY_ON = {s.strip() for s in os.getenv("NOTIFY_ON", "new_release,new_asset,dl_increase").split(",") if s.strip()}
ASSET_NAME_INCLUDE = os.getenv("ASSET_NAME_INCLUDE", "")
ASSET_NAME_EXCLUDE = os.getenv("ASSET_NAME_EXCLUDE", "")

# If true, all existing releases will be marked as known on first start (no notifications)
# If false, all releases will be reported as new on first start
SKIP_EXISTING_ON_INIT = os.getenv("SKIP_EXISTING_ON_INIT", "true").lower() in {"1","true","yes"}

TIMEOUT = int(os.getenv("HTTP_TIMEOUT", "20"))
STATE_DIR = Path(os.getenv("STATE_DIR", "./state"))
LOG_LEVEL = os.getenv("LOG_LEVEL", "INFO").upper()

# Logging configuration
LOG_DIR = Path(os.getenv("LOG_DIR", "./logs"))
LOG_TO_FILE = os.getenv("LOG_TO_FILE", "true").lower() in {"1","true","yes"}
LOG_MAX_BYTES = int(os.getenv("LOG_MAX_BYTES", "10485760"))  # 10MB default
LOG_BACKUP_COUNT = int(os.getenv("LOG_BACKUP_COUNT", "5"))

# PostgreSQL configuration (optional)
POSTGRES_DSN = os.getenv("POSTGRES_DSN", "").strip()
POSTGRES_TABLE = os.getenv("POSTGRES_TABLE", "gh_watcher_logs")

SESSION = requests.Session()

# Setup logging
log = logging.getLogger("gh-release-bot")
log.setLevel(LOG_LEVEL)
log.handlers.clear()

# Console handler
console_handler = logging.StreamHandler()
console_handler.setLevel(LOG_LEVEL)
console_formatter = logging.Formatter("%(asctime)s | %(levelname)s | %(message)s")
console_handler.setFormatter(console_formatter)
log.addHandler(console_handler)

# File handler (optional)
if LOG_TO_FILE:
    try:
        LOG_DIR.mkdir(parents=True, exist_ok=True)
    except PermissionError:
        log.warning("Cannot create log directory %s (permission denied), file logging disabled", LOG_DIR)
    else:
        file_handler = RotatingFileHandler(
            LOG_DIR / "gh-watcher.log",
            maxBytes=LOG_MAX_BYTES,
            backupCount=LOG_BACKUP_COUNT,
            encoding="utf-8"
        )
        file_handler.setLevel(LOG_LEVEL)
        file_formatter = logging.Formatter("%(asctime)s | %(levelname)s | %(message)s")
        file_handler.setFormatter(file_formatter)
        log.addHandler(file_handler)
        log.info("File logging enabled: %s", LOG_DIR / "gh-watcher.log")

# PostgreSQL connection (optional)
pg_conn = None
if POSTGRES_DSN:
    try:
        import psycopg2
        pg_conn = psycopg2.connect(POSTGRES_DSN)
        pg_conn.autocommit = True
        
        # Create table if not exists
        with pg_conn.cursor() as cur:
            cur.execute(f"""
                CREATE TABLE IF NOT EXISTS {POSTGRES_TABLE} (
                    id SERIAL PRIMARY KEY,
                    timestamp TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                    log_level VARCHAR(20),
                    repo VARCHAR(255),
                    event_type VARCHAR(50),
                    message TEXT,
                    data JSONB
                )
            """)
            cur.execute(f"CREATE INDEX IF NOT EXISTS idx_{POSTGRES_TABLE}_timestamp ON {POSTGRES_TABLE}(timestamp)")
            cur.execute(f"CREATE INDEX IF NOT EXISTS idx_{POSTGRES_TABLE}_repo ON {POSTGRES_TABLE}(repo)")
        log.info("PostgreSQL logging enabled: %s", POSTGRES_TABLE)
    except Exception as e:
        log.warning("PostgreSQL connection failed (continuing without DB logging): %s", e)
        pg_conn = None

inc_re = re.compile(ASSET_NAME_INCLUDE) if ASSET_NAME_INCLUDE else None
exc_re = re.compile(ASSET_NAME_EXCLUDE) if ASSET_NAME_EXCLUDE else None

shutdown = False
def _sig_handler(signum, frame):
    global shutdown
    shutdown = True
signal.signal(signal.SIGTERM, _sig_handler)
signal.signal(signal.SIGINT, _sig_handler)

# -------------------------
# Helpers
# -------------------------
def _headers(etag: Optional[str] = None) -> Dict[str, str]:
    h = {"Accept": "application/vnd.github+json", "User-Agent": "gh-release-discord-bot"}
    if GITHUB_TOKEN:
        h["Authorization"] = f"Bearer {GITHUB_TOKEN}"
    if etag:
        h["If-None-Match"] = etag
    return h

def _state_path(repo: str) -> Path:
    return STATE_DIR / f"{repo.replace('/', '__')}.json"

def load_state(repo: str) -> Dict[str, Any]:
    p = _state_path(repo)
    if p.exists():
        try:
            return json.loads(p.read_text(encoding="utf-8"))
        except Exception:
            pass
    return {"etag": None, "releases": {}}

def save_state(repo: str, state: Dict[str, Any]) -> None:
    STATE_DIR.mkdir(parents=True, exist_ok=True)
    _state_path(repo).write_text(json.dumps(state, ensure_ascii=False, indent=2), encoding="utf-8")

def log_to_postgres(repo: str, event_type: str, log_level: str, message: str, data: Optional[Dict[str, Any]] = None) -> None:
    """Log event to PostgreSQL database if configured"""
    if not pg_conn:
        return
    
    try:
        with pg_conn.cursor() as cur:
            cur.execute(
                f"""
                INSERT INTO {POSTGRES_TABLE} (log_level, repo, event_type, message, data)
                VALUES (%s, %s, %s, %s, %s)
                """,
                (log_level, repo, event_type, message, json.dumps(data) if data else None)
            )
    except Exception as e:
        log.warning("Failed to log to PostgreSQL: %s", e)

def fetch_releases(repo: str, etag: Optional[str]) -> Tuple[int, Any, Optional[str], Optional[requests.Response]]:
    url = f"{GITHUB_API_BASE}/repos/{repo}/releases"
    if ONLY_LATEST:
        url += "/latest"
    resp = SESSION.get(url, headers=_headers(etag), timeout=TIMEOUT)
    new_etag = resp.headers.get("ETag")
    if resp.status_code == 304:
        return 304, None, new_etag, resp
    if resp.status_code >= 400:
        raise requests.HTTPError(f"{resp.status_code} {resp.text}", response=resp)
    return resp.status_code, resp.json(), new_etag, resp

def summarize_release(rel: Dict[str, Any]) -> Dict[str, Any]:
    assets = rel.get("assets", []) or []
    asset_info = {}
    for a in assets:
        name = a.get("name") or ""
        if inc_re and not inc_re.search(name):
            continue
        if exc_re and exc_re.search(name):
            continue
        asset_info[a["id"]] = {
            "name": name,
            "download_count": int(a.get("download_count", 0)),
            "browser_download_url": a.get("browser_download_url"),
        }
    return {
        "id": rel.get("id"),
        "tag_name": rel.get("tag_name"),
        "name": rel.get("name"),
        "draft": rel.get("draft"),
        "prerelease": rel.get("prerelease"),
        "html_url": rel.get("html_url"),
        "published_at": rel.get("published_at"),
        "assets": asset_info,
    }

def build_snapshot(api_data: Any) -> Dict[str, Any]:
    if isinstance(api_data, dict) and "id" in api_data:
        rels = [api_data]
    else:
        rels = api_data or []
    snapshot = {}
    for rel in rels:
        if not INCLUDE_DRAFT and rel.get("draft"):
            continue
        if not INCLUDE_PRERELEASE and rel.get("prerelease"):
            continue
        s = summarize_release(rel)
        snapshot[str(s["id"])] = s
    return snapshot

def detect_changes(old: Dict[str, Any], new: Dict[str, Any]) -> List[Dict[str, Any]]:
    events = []
    old_rels = old.get("releases", {})

    if "new_release" in NOTIFY_ON:
        for rel_id, rel in new.items():
            if rel_id not in old_rels:
                events.append({"type": "new_release", "release": rel})

    if "new_asset" in NOTIFY_ON:
        for rel_id, rel in new.items():
            old_rel = old_rels.get(rel_id)
            if not old_rel:
                continue
            for asset_id, asset in rel["assets"].items():
                if asset_id not in (old_rel.get("assets") or {}):
                    events.append({"type": "new_asset", "release": rel, "asset": asset})

    if "dl_increase" in NOTIFY_ON:
        for rel_id, rel in new.items():
            old_rel = old_rels.get(rel_id)
            for asset_id, asset in rel["assets"].items():
                old_asset = (old_rel.get("assets") or {}).get(asset_id) if old_rel else None
                
                # Determine old download count (0 if asset or release is new)
                old_dl = int(old_asset.get("download_count", 0)) if old_asset else 0
                new_dl = int(asset.get("download_count", 0))
                
                # Report increase if downloads increased (including from 0 for new releases/assets)
                if new_dl > old_dl:
                    events.append({
                        "type": "dl_increase",
                        "release": rel,
                        "asset": asset,
                        "delta": new_dl - old_dl,
                        "from": old_dl,
                        "to": new_dl
                    })
    return events

def send_discord_text(content: str) -> None:
    if not DISCORD_WEBHOOK:
        log.warning("DISCORD_WEBHOOK_URL not set. Logging only: %s", content)
        return
    payload: Dict[str, Any] = {"content": content}
    if DISCORD_USERNAME:
        payload["username"] = DISCORD_USERNAME
    if DISCORD_AVATAR_URL:
        payload["avatar_url"] = DISCORD_AVATAR_URL
    r = SESSION.post(DISCORD_WEBHOOK, json=payload, timeout=TIMEOUT)
    r.raise_for_status()

def send_discord_embed(title: str, description: str, url: Optional[str] = None, fields: Optional[List[Dict[str,str]]] = None) -> None:
    if not DISCORD_WEBHOOK:
        log.warning("DISCORD_WEBHOOK_URL not set. Logging only: %s | %s", title, description)
        return
    embed: Dict[str, Any] = {"title": title, "description": description}
    if url:
        embed["url"] = url
    if fields:
        embed["fields"] = fields
    payload: Dict[str, Any] = {"embeds": [embed]}
    if DISCORD_USERNAME:
        payload["username"] = DISCORD_USERNAME
    if DISCORD_AVATAR_URL:
        payload["avatar_url"] = DISCORD_AVATAR_URL
    r = SESSION.post(DISCORD_WEBHOOK, json=payload, timeout=TIMEOUT)
    r.raise_for_status()

def format_and_send_event(repo: str, ev: Dict[str, Any]) -> None:
    rel = ev["release"]
    tag = rel.get("tag_name") or rel.get("name") or "(no name)"
    rel_url = rel.get("html_url", "")
    t = ev["type"]

    if not USE_EMBEDS:
        if t == "new_release":
            send_discord_text(f"📦 New Release **{repo}**: **{tag}**\n{rel_url}")
        elif t == "new_asset":
            a = ev["asset"]
            send_discord_text(f"🆕 New Asset in **{repo} {tag}**: **{a['name']}** → 0 Downloads\n{rel_url}")
        elif t == "dl_increase":
            a = ev["asset"]
            send_discord_text(
                f"⬇️ Download Increase **{repo} {tag}**: **{a['name']}** "
                f"(+{ev['delta']}, {ev['from']} → {ev['to']})\n{a.get('browser_download_url') or rel_url}"
            )
        else:
            send_discord_text(f"ℹ️ {t} | {repo} {tag}")
        return

    if t == "new_release":
        send_discord_embed(
            title=f"New Release: {repo} {tag}",
            description=f"Link: {rel_url}",
            url=rel_url,
            fields=[
                {"name":"Prerelease", "value":str(rel.get("prerelease")), "inline": True},
                {"name":"Draft", "value":str(rel.get("draft")), "inline": True},
            ],
        )
    elif t == "new_asset":
        a = ev["asset"]
        send_discord_embed(
            title=f"New Asset: {a['name']}",
            description=f"Release: {repo} {tag}",
            url=rel_url,
            fields=[{"name":"Downloads", "value":"0", "inline": True}]
        )
    elif t == "dl_increase":
        a = ev["asset"]
        send_discord_embed(
            title=f"Download Increase: {a['name']}",
            description=f"{repo} {tag}",
            url=a.get("browser_download_url") or rel_url,
            fields=[
                {"name":"Δ", "value":f"+{ev['delta']}", "inline": True},
                {"name":"from → to", "value":f"{ev['from']} → {ev['to']}", "inline": True}
            ]
        )
    else:
        send_discord_embed(title=f"Event: {t}", description=f"{repo} {tag}", url=rel_url)

def process_repo(repo: str) -> None:
    state = load_state(repo)
    is_first_run = not state.get("releases")  # No state = first run
    
    log.info("[%s] Checking for updates...", repo)
    log_to_postgres(repo, "check_start", "INFO", "Starting release check", {"first_run": is_first_run})
    
    try:
        status, data, new_etag, resp = fetch_releases(repo, state.get("etag"))
    except requests.HTTPError as e:
        r = e.response
        if r is not None and r.status_code == 403 and "rate limit" in r.text.lower():
            reset = r.headers.get("x-ratelimit-reset")
            log.warning("Rate limit for %s. Reset header: %s", repo, reset)
            log_to_postgres(repo, "rate_limit", "WARNING", f"Rate limit reached. Reset: {reset}", {
                "reset_time": reset
            })
        else:
            log.error("HTTPError %s: %s", repo, e)
            log_to_postgres(repo, "error", "ERROR", f"HTTPError: {e}", {
                "status_code": r.status_code if r else None,
                "error": str(e)
            })
        return
    except Exception as e:
        log.error("Error fetching releases %s: %s", repo, e)
        log_to_postgres(repo, "error", "ERROR", f"Error fetching releases: {e}", {"error": str(e)})
        return

    if status == 304:
        log.info("[%s] 304 Not Modified - no changes detected", repo)
        log_to_postgres(repo, "no_change", "INFO", "304 Not Modified - no changes detected", {
            "status_code": 304
        })
        return

    snapshot = build_snapshot(data)
    
    # Log old vs new state
    old_releases = state.get("releases", {})
    log.info("[%s] Old state: %d release(s), New state: %d release(s)", 
             repo, len(old_releases), len(snapshot))
    
    # Log release details for comparison
    for rel_id, rel in snapshot.items():
        old_rel = old_releases.get(rel_id)
        tag = rel.get("tag_name", "unknown")
        asset_count = len(rel.get("assets", {}))
        
        if old_rel:
            # Existing release - check for changes
            old_asset_count = len(old_rel.get("assets", {}))
            if asset_count != old_asset_count:
                log.info("[%s] Release %s: assets changed (%d → %d)", 
                         repo, tag, old_asset_count, asset_count)
            
            # Log download count changes
            for asset_id, asset in rel.get("assets", {}).items():
                old_asset = old_rel.get("assets", {}).get(asset_id)
                if old_asset:
                    old_dl = old_asset.get("download_count", 0)
                    new_dl = asset.get("download_count", 0)
                    if new_dl != old_dl:
                        log.info("[%s] Release %s: Asset '%s' downloads: %d → %d (+%d)", 
                                 repo, tag, asset.get("name"), old_dl, new_dl, new_dl - old_dl)
                else:
                    log.info("[%s] Release %s: New asset '%s' with %d downloads", 
                             repo, tag, asset.get("name"), asset.get("download_count", 0))
        else:
            # New release
            log.info("[%s] New release detected: %s with %d asset(s)", 
                     repo, tag, asset_count)
            for asset_id, asset in rel.get("assets", {}).items():
                log.info("[%s]   - Asset: '%s' (%d downloads)", 
                         repo, asset.get("name"), asset.get("download_count", 0))
    
    # On first start: Either skip all releases or report all
    if is_first_run and SKIP_EXISTING_ON_INIT:
        log.info("[%s] First start: %d existing release(s) marked as known (no notifications)", 
                 repo, len(snapshot))
        log_to_postgres(repo, "first_start", "INFO", f"First start: {len(snapshot)} existing releases marked as known", {
            "release_count": len(snapshot),
            "skip_notifications": True
        })
        state["etag"] = new_etag
        state["releases"] = snapshot
        save_state(repo, state)
        return
    
    events = detect_changes(state, snapshot)
    log.info("[%s] Detected %d event(s) to notify", repo, len(events))
    log_to_postgres(repo, "check", "INFO", f"Detected {len(events)} event(s)", {
        "old_releases": len(old_releases),
        "new_releases": len(snapshot),
        "events": len(events)
    })
    
    if not events:
        log.info("[%s] No notification events generated", repo)
    else:
        for ev in events:
            log.info("[%s] Event: %s", repo, ev["type"])
            
            # Log to PostgreSQL
            event_data = {
                "type": ev["type"],
                "release": {
                    "tag": ev["release"].get("tag_name"),
                    "name": ev["release"].get("name"),
                    "url": ev["release"].get("html_url")
                }
            }
            if "asset" in ev:
                event_data["asset"] = {
                    "name": ev["asset"].get("name"),
                    "download_count": ev["asset"].get("download_count")
                }
            if "delta" in ev:
                event_data["delta"] = ev["delta"]
                event_data["from"] = ev["from"]
                event_data["to"] = ev["to"]
            
            log_to_postgres(repo, ev["type"], "INFO", f"Event: {ev['type']}", event_data)
            
            try:
                format_and_send_event(repo, ev)
            except Exception as ex:
                log.error("Discord error (%s): %s", repo, ex)
                log_to_postgres(repo, "error", "ERROR", f"Discord error: {ex}", {"error": str(ex)})

    state["etag"] = new_etag
    state["releases"] = snapshot
    save_state(repo, state)

def main_loop():
    if not REPOS:
        raise SystemExit("Please set REPOS='owner/repo,owner2/repo2'.")
    
    # Run once for each repo
    for repo in REPOS:
        if shutdown:
            break
        process_repo(repo)
    
    # If POLL_INTERVAL > 0, continue in loop (for Docker/local continuous monitoring)
    if POLL_INTERVAL > 0:
        log.info("Entering continuous monitoring mode (POLL_INTERVAL=%d)", POLL_INTERVAL)
        while not shutdown:
            for repo in REPOS:
                if shutdown:
                    break
                process_repo(repo)
            slept = 0
            while not shutdown and slept < POLL_INTERVAL:
                time.sleep(1)
                slept += 1
    else:
        log.info("Single run completed (POLL_INTERVAL=0)")

if __name__ == "__main__":
    main_loop()