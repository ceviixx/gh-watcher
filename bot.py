#!/usr/bin/env python3
import os
import re
import json
import time
import signal
import logging
from pathlib import Path
from typing import Dict, Any, List, Tuple, Optional
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

SESSION = requests.Session()
logging.basicConfig(level=LOG_LEVEL, format="%(asctime)s | %(levelname)s | %(message)s")
log = logging.getLogger("gh-release-bot")

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
            if not old_rel:
                continue
            for asset_id, asset in rel["assets"].items():
                old_asset = (old_rel.get("assets") or {}).get(asset_id)
                if not old_asset:
                    continue
                old_dl = int(old_asset.get("download_count", 0))
                new_dl = int(asset.get("download_count", 0))
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
    
    try:
        status, data, new_etag, resp = fetch_releases(repo, state.get("etag"))
    except requests.HTTPError as e:
        r = e.response
        if r is not None and r.status_code == 403 and "rate limit" in r.text.lower():
            reset = r.headers.get("x-ratelimit-reset")
            log.warning("Rate limit for %s. Reset header: %s", repo, reset)
        else:
            log.error("HTTPError %s: %s", repo, e)
        return
    except Exception as e:
        log.error("Error fetching releases %s: %s", repo, e)
        return

    if status == 304:
        log.debug("[%s] 304 Not Modified", repo)
        return

    snapshot = build_snapshot(data)
    
    # On first start: Either skip all releases or report all
    if is_first_run and SKIP_EXISTING_ON_INIT:
        log.info("[%s] First start: %d existing release(s) marked as known (no notifications)", 
                 repo, len(snapshot))
        state["etag"] = new_etag
        state["releases"] = snapshot
        save_state(repo, state)
        return
    
    events = detect_changes(state, snapshot)
    if not events:
        log.info("[%s] No changes", repo)
    else:
        for ev in events:
            try:
                format_and_send_event(repo, ev)
            except Exception as ex:
                log.error("Discord error (%s): %s", repo, ex)

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