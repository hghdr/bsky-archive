#!/usr/bin/env python3
import os, re, json, time, pathlib, html, datetime, textwrap
from urllib.parse import quote
import requests

REPO_ROOT = pathlib.Path(__file__).resolve().parents[1]
OUT_DIR = REPO_ROOT / "docs"
OUT_DIR.mkdir(parents=True, exist_ok=True)

HANDLE = os.environ.get("BLUESKY_HANDLE")  # e.g. yourname.bsky.social
APP_PASSWORD = os.environ.get("BLUESKY_APP_PASSWORD")

API = "https://bsky.social/xrpc"
SESSION_FILE = REPO_ROOT / ".cache_session.json"
OUT_CSS = OUT_DIR / "style.css"

def create_session():
    r = requests.post(
        f"{API}/com.atproto.server.createSession",
        json={"identifier": HANDLE, "password": APP_PASSWORD},
        timeout=30
    )
    r.raise_for_status()
    data = r.json()
    (REPO_ROOT / ".cache").mkdir(exist_ok=True)
    SESSION_FILE.write_text(json.dumps(data))
    return data

def get_session():
    if SESSION_FILE.exists():
        data = json.loads(SESSION_FILE.read_text())
        return data
    return create_session()

def auth_headers(jwt):
    return {"Authorization": f"Bearer {jwt}"}

def get_author_feed(did_or_handle, jwt):
    """Fetch ALL posts by the author, paginating. Returns list of feed items."""
    items = []
    cursor = None
    while True:
        params = {"actor": did_or_handle, "limit": 100}
        if cursor:
            params["cursor"] = cursor
        r = requests.get(
            f"{API}/app.bsky.feed.getAuthorFeed",
            headers=auth_headers(jwt),
            params=params,
            timeout=30
        )
        r.raise_for_status()
        data = r.json()
        feed = data.get("feed", [])
        items.extend(feed)
        cursor = data.get("cursor")
        # Safety: stop if no more pages
        if not cursor or len(feed) == 0:
            break
        # polite pause
        time.sleep(0.2)
    return items

def at_uri_to_post_url(uri):
    # at://did:plc:xxxx/app.bsky.feed.post/3lxyz...
    # → https://bsky.app/profile/{did}/post/{rkey}
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
    # Only posts from the actor (feed already is author feed, but be safe)
    if not item.get("post"):
        return False
    return rec.get("$type") == "app.bsky.feed.post"

def extract_text(rec):
    # Basic text; we ignore facets for simplicity
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

def build():
    sess = get_session()
    jwt = sess["accessJwt"]
    did = sess["did"]  # for building links

    feed = get_author_feed(HANDLE, jwt)

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
        # include reply context marker
        is_reply = "reply" in it and it["reply"] is not None
        posts.append({
            "dt": dt,
            "text": text,
            "url": url,
            "is_reply": is_reply
        })

    # Group by month
    posts.sort(key=lambda x: x["dt"], reverse=True)
    months = {}
    for p in posts:
        key = month_key(p["dt"])
        months.setdefault(key, []).append(p)

    # Write styles
    OUT_CSS.write_text(textwrap.dedent("""
    body{font-family:system-ui,-apple-system,Segoe UI,Roboto,Helvetica,Arial,Apple Color Emoji,Segoe UI Emoji;line-height:1.6;margin:2rem auto;max-width:760px;padding:0 1rem;}
    a{text-decoration:none}
    header{margin-bottom:2rem}
    .month-list a{display:block;padding:.25rem 0}
    .post{padding:0.75rem 0;border-bottom:1px solid #eee}
    .post .meta{font-size:.9rem;opacity:.7}
    .badge{font-size:.75rem;padding:.1rem .4rem;border:1px solid #ccc;border-radius:.4rem;margin-left:.5rem}
    footer{margin:3rem 0;font-size:.9rem;opacity:.7}
    """).strip()+"\n")

    # Month pages
    for key, arr in months.items():
        year, mon = key.split("-")
        (OUT_DIR / key).mkdir(parents=True, exist_ok=True)
        with open(OUT_DIR / key / "index.html", "w", encoding="utf-8") as f:
    f.write(f"""<!doctype html>
<html lang="ja">
<meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1">
<title>{key} | Bluesky 月別アーカイブ</title>
<link rel="stylesheet" href="../style.css">
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
                f.write(f"""  <article class="post">
    <div class="meta">{nice_date(p["dt"])} {badge}</div>
    <p>{p["text"]}</p>
    <div><a href="{p["url"]}" target="_blank" rel="noopener">Blueskyで開く ↗</a></div>
  </article>
""")
            f.write("""</main>
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
<link rel="stylesheet" href="style.css">
<body>
<header>
  <h1>Bluesky 月別アーカイブ</h1>
  <p>twglogっぽい素朴な一覧。月を選んで閲覧。</p>
</header>
<main class="month-list">
""")
        for mk in month_keys:
            cnt = len(months[mk])
            f.write(f'  <a href="{mk}/">{mk} <span class="badge">{cnt}</span></a>\n')
        f.write("""
</main>
<script>
const btn = document.getElementById('sortToggle');
const container = document.getElementById('posts');
let desc = true;

btn.addEventListener('click', () => {
  const posts = Array.from(container.querySelectorAll('.post'));
  posts.reverse().forEach(p => container.appendChild(p));
  desc = !desc;
  btn.textContent = desc ? "▼ 降順（新→旧）" : "▲ 昇順（旧→新）";
});
</script>
<footer>Generated from Bluesky via ATProto • 個人用静的アーカイブ</footer>
</body></html>
""")


    print(f"Built {len(posts)} posts into {len(months)} month folders at {OUT_DIR}")

if __name__ == "__main__":
    assert HANDLE and APP_PASSWORD, "BLUESKY_HANDLE / BLUESKY_APP_PASSWORD not set"
    build()
