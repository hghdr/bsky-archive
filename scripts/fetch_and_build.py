#!/usr/bin/env python3
# -*- coding: utf-8 -*-

"""
Bluesky author feed -> month-archived static HTML (twglog-ish)
- Uses the public API (no auth required) for stable CI builds
- Mobile-friendly, Vercel-friendly
- Supports client-side sort toggle (asc/desc) using data-epoch
- Writes default style.css every build; user.css is created once (if missing) and never overwritten
- Adds cache-busting query using GITHUB_SHA (or epoch) to avoid stale CSS/JS

Env:
  BLUESKY_HANDLE   e.g., yourname.bsky.social   (no leading "@")
Optional:
  GITHUB_SHA       (provided on GitHub Actions) used for cache-busting

Usage:
  python scripts/fetch_and_build.py
  (generates ./docs/ ... which can be hosted by Vercel or GitHub Pages)
"""

import os
import time
import json
import pathlib
import html
import datetime
import textwrap
from urllib.parse import quote
import requests

REPO_ROOT = pathlib.Path(__file__).resolve().parents[1]
OUT_DIR = REPO_ROOT / "docs"
OUT_DIR.mkdir(parents=True, exist_ok=True)

OUT_CSS = OUT_DIR / "style.css"
OUT_USER_CSS = OUT_DIR / "user.css"

HANDLE = os.environ.get("BLUESKY_HANDLE")  # e.g. yourname.bsky.social

# Cache buster for assets
BUILD_VER = os.environ.get("GITHUB_SHA") or str(int(time.time()))

# ATProto public API (no auth required)
API_PUBLIC = "https://public.api.bsky.app/xrpc"

# --------------------------- utils ---------------------------

def resolve_did_via_public(handle: str) -> str:
    """Resolve a handle into DID via public API"""
    r = requests.get(
        f"{API_PUBLIC}/com.atproto.identity.resolveHandle",
        params={"handle": handle},
        timeout=20
    )
    r.raise_for_status()
    return r.json().get("did")

def get_author_feed_public(actor_did_or_handle: str):
    """Fetch ALL posts by the author via public API (no auth)."""
    items, cursor = [], None
    while True:
        params = {"actor": actor_did_or_handle, "limit": 100}
        if cursor:
            params["cursor"] = cursor
        r = requests.get(f"{API_PUBLIC}/app.bsky.feed.getAuthorFeed", params=params, timeout=30)
        if r.status_code >= 400:
            try:
                print("getAuthorFeed error:", r.status_code, r.json())
            except Exception:
                print("getAuthorFeed error:", r.status_code, r.text[:200])
            r.raise_for_status()
        data = r.json()
        feed = data.get("feed", [])
        items.extend(feed)
        cursor = data.get("cursor")
        if not cursor or not feed:
            break
        time.sleep(0.2)
    return items

def at_uri_to_post_url(uri):
    # at://did:plc:xxxx/app.bsky.feed.post/3lxyz... -> https://bsky.app/profile/{did}/post/{rkey}
    try:
        _, did, collection, rkey = uri.split("/", 3)
        return f"https://bsky.app/profile/{did}/post/{rkey}"
    except Exception:
        return None

def is_own_original_post(item):
    """
    Keep simple: exclude pure reposts; keep normal posts & replies.
    Bluesky returns shapes like item["post"]["uri"], item["post"]["record"]["text"], etc.
    """
    rec = (((item.get("post") or {}).get("record")) or {})
    # Reposts have 'reason': {'$type': 'app.bsky.feed.defs#reasonRepost', ...}
    if "reason" in item:
        return False
    if not item.get("post"):
        return False
    # Some feeds may include likes or others; ensure it's a post record
    return rec.get("$type") == "app.bsky.feed.post"

def extract_text(rec):
    # Basic text; we ignore facets for simplicity (escape HTML for safety)
    t = rec.get("text", "")
    return html.escape(t)

def extract_created_at(rec):
    ts = rec.get("createdAt")
    if not ts:
        return None
    try:
        dt = datetime.datetime.fromisoformat(ts.replace("Z", "+00:00"))
    except Exception:
        return None
    return dt

def month_key(dt):
    return dt.strftime("%Y-%m")

def nice_date(dt):
    # Localize to JST for display (UTC→JST +09:00)
    jst = dt.astimezone(datetime.timezone(datetime.timedelta(hours=9)))
    return jst.strftime("%Y-%m-%d %H:%M")

# --------------------------- build ---------------------------

def write_default_css():
    OUT_CSS.write_text(textwrap.dedent("""
    /* default style */
    :root{--maxw:760px;--pad:1rem}
    *{box-sizing:border-box}
    body{font-family:system-ui,-apple-system,Segoe UI,Roboto,Helvetica,Arial,Apple Color Emoji,Segoe UI Emoji;line-height:1.75;margin:2rem auto;max-width:var(--maxw);padding:0 var(--pad)}
    a{text-decoration:none}
    header{margin-bottom:1.25rem}
    h1{font-size:clamp(1.6rem,6vw,2.6rem);line-height:1.2;margin:0 0 .25rem}
    .month-list a{display:block;padding:.35rem 0}
    .post{padding:.9rem 0;border-bottom:1px solid #eee}
    .post .meta{font-size:.95rem;opacity:.7}
    .badge{font-size:.75rem;padding:.1rem .45rem;border:1px solid #ccc;border-radius:.5rem;margin-left:.5rem}
    footer{margin:3rem 0 2rem;font-size:.9rem;opacity:.7}
    button{font-size:.95rem;padding:.25rem .7rem;border:1px solid #ccc;border-radius:.5rem;background:#f9f9f9;cursor:pointer}
    button:hover{background:#eee}
    @media (max-width:480px){
      :root{--pad:.75rem}
      .post .meta{font-size:.9rem}
    }
    """).strip()+"\n")

    # Create user.css if missing (do not overwrite once created)
    if not OUT_USER_CSS.exists():
        OUT_USER_CSS.write_text("/* put your overrides here. this file won't be overwritten. */\n")

def build():
    assert HANDLE, "BLUESKY_HANDLE is required (e.g., yourname.bsky.social without '@')"

    write_default_css()

    # Resolve handle to DID (public; no auth)
    actor = resolve_did_via_public(HANDLE) or HANDLE

    # Fetch feed via public API
    feed = get_author_feed_public(actor)

    # Collect posts (exclude pure reposts; keep replies)
    posts = []
    for it in feed:
        if not is_own_original_post(it):
            continue
        post = it["post"]
        rec = post.get("record", {})
        uri = post.get("uri")
        url = at_uri_to_post_url(uri)
        dt = extract_created_at(rec)
        if not dt:
            continue
        text = extract_text(rec)
        is_reply = "reply" in it and it["reply"] is not None
        posts.append({
            "dt": dt,
            "text": text,
            "url": url,
            "is_reply": is_reply
        })

    # Sort newest first
    posts.sort(key=lambda x: x["dt"], reverse=True)

    # Group by month
    months = {}
    for p in posts:
        key = month_key(p["dt"])
        months.setdefault(key, []).append(p)

    # Month pages
    for key, arr in months.items():
        (OUT_DIR / key).mkdir(parents=True, exist_ok=True)
        with open(OUT_DIR / key / "index.html", "w", encoding="utf-8") as f:
            f.write(f"""<!doctype html>
<html lang="ja">
<meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1">
<title>{key} | Bluesky 月別アーカイブ</title>
<link rel="stylesheet" href="../style.css?v={BUILD_VER}">
<link rel="stylesheet" href="../user.css?v={BUILD_VER}">
<body>
<header>
  <h1>Bluesky 月別アーカイブ – {key}</h1>
  <nav><a href="../index.html">← 月一覧</a></nav>
  <div style="margin-top:0.5rem">
    <button id="sortToggle">▼ 降順（新→旧）</button>
  </div>
</header>
<main id="posts">
""")
            for p in arr:
                badge = '<span class="badge">reply</span>' if p["is_reply"] else ""
                epoch = int(p["dt"].timestamp())
                f.write(f"""  <article class="post" data-epoch="{epoch}">
    <div class="meta">{nice_date(p["dt"])} {badge}</div>
    <p>{p["text"]}</p>
    <div><a href="{p["url"]}" target="_blank" rel="noopener">Blueskyで開く ↗</a></div>
  </article>
""")
            # sort script (deterministic by data-epoch)
            f.write(f"""</main>
<script>
(function(){{
  const btn = document.getElementById('sortToggle');
  const container = document.getElementById('posts');
  if (!btn || !container) return;
  let desc = true;
  function render() {{
    const posts = Array.from(container.querySelectorAll('.post'));
    posts.sort((a,b) => (desc ? (Number(b.dataset.epoch||0)-Number(a.dataset.epoch||0))
                               : (Number(a.dataset.epoch||0)-Number(b.dataset.epoch||0))));
    posts.forEach(el => container.appendChild(el));
    btn.textContent = desc ? "▼ 降順（新→旧）" : "▲ 昇順（旧→新）";
  }}
  btn.addEventListener('click', () => {{ desc = !desc; render(); }});
  render();
}})();
</script>
<footer>Generated from Bluesky via ATProto • 個人用静的アーカイブ</footer>
</body></html>
""")

    # Top index
    month_keys = sorted(months.keys(), reverse=True)
    with open(OUT_DIR / "index.html", "w", encoding="utf-8") as f:
        f.write(f"""<!doctype html>
<html lang="ja">
<meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1">
<title>Bluesky 月別アーカイブ</title>
<link rel="stylesheet" href="style.css?v={BUILD_VER}">
<link rel="stylesheet" href="user.css?v={BUILD_VER}">
<body>
<header>
  <h1>Bluesky 月別アーカイブ</h1>
  <p>twglogっぽい素朴な一覧。月を選んで閲覧。</p>
</header>
<main class="month-list">
""")
        for mk in month_keys:
            cnt = len(months[mk])
            f.write(f'  <a href="{mk}/">{mk} <span class="badge">{cnt}</span></a>\\n')
        f.write("""</main>
<footer>Generated daily on GitHub Actions</footer>
</body></html>
""")

    print(f"Built {len(posts)} posts into {len(months)} month folders at {OUT_DIR}")

if __name__ == "__main__":
    assert HANDLE, "BLUESKY_HANDLE / ハンドル（例: yourname.bsky.social）を環境変数で指定してください"
    build()
