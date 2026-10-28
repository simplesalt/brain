#!/usr/bin/env python3
"""Pick the Notion items moved to trash since the last run, for Hermes to review.

Runs nightly as the trigger for the deletion-review cron job: it wakes Hermes only when something new
is in the Notion trash. It lists the trashed items with the dedicated Deletion Review key, folds items
trashed inside a trashed parent into that parent, reads what each deleted item said, who deleted it and
when, and gathers evidence: live pages that may replace it, and comments or parent text that may explain
the removal. It prints a batch for Hermes. With nothing new it prints {"wakeAgent": false}, so the job
skips the model call. Items handed out are kept on the gbrain page ops/deletion-review, with the log.

LOG ONLY: this script only reads Notion (search, users, blocks, pages, comments). It never restores,
edits, moves or deletes anything there.

Hermes only judges: its answer lists L, R and E per item as JSON after a DELETION-REVIEW-RESULT line.
Before picking the next batch, this script reads those answers from the job's saved output, computes the
risk itself and appends a log entry per item to the state page, so the arithmetic never depends on the model.
"""
import datetime
import json
import os
import re
import sys
import time
import urllib.error
import urllib.request

URL = os.environ.get("GBRAIN_URL", "http://gbrain:3131/mcp")
TOKEN = os.environ.get("GBRAIN_REMOTE_TOKEN", "gbrain-mcp")
NOTION = "https://api.notion.com/v1"
NOTION_VERSION = "2025-09-03"
NOTION_TOKEN = os.environ.get("NOTION_DELETION_REVIEW_TOKEN", "")
STATE = "ops/deletion-review"
BATCH = int(os.environ.get("DELETION_REVIEW_BATCH", "10"))
BACKLOG_HOURS = 48
MAX_TEXT = 4000
MAX_EXCERPT = 600
MAX_REPLACEMENTS = 5
MAX_LOG = 500
MAX_PENDING = 200
OUTPUT_DIR = os.path.join(os.environ.get("HERMES_HOME", "/opt/data"), "cron", "output")
MARKER = "DELETION-REVIEW-RESULT"
DRY_RUN = os.environ.get("DELETION_REVIEW_DRY_RUN") == "1"
# Dry runs still restore and re-trash a page to read it; this turns that off too.
NO_RESTORE = os.environ.get("DELETION_REVIEW_NO_RESTORE") == "1"
# Risk = S * L * (1 - 0.5 * R) * (1 - 0.3 * E): Substance of what was deleted, Loss (how much of it is
# not found elsewhere), Replacement (a live page clearly replaces it), Explanation (a reason is recorded).
THRESHOLD = 0.25
STOPWORDS = {"the", "and", "for", "with", "from", "that", "this", "copy", "untitled", "notes", "page", "new"}


def call(tool, args):
    body = json.dumps({"jsonrpc": "2.0", "id": 1, "method": "tools/call",
                       "params": {"name": tool, "arguments": args}}).encode()
    req = urllib.request.Request(URL, data=body, headers={
        "Content-Type": "application/json", "Accept": "application/json, text/event-stream",
        "Authorization": "Bearer " + TOKEN})
    with urllib.request.urlopen(req, timeout=60) as r:
        for line in r.read().decode().splitlines():
            if line.startswith("data: "):
                msg = json.loads(line[6:])
                if "error" in msg:
                    raise RuntimeError(f"{tool}: {msg['error']}")
                text = msg["result"]["content"][0]["text"]
                if msg["result"].get("isError"):
                    raise RuntimeError(f"{tool}: {text[:300]}")
                try:
                    return json.loads(text)
                except json.JSONDecodeError:
                    return text
    raise RuntimeError(f"{tool}: no response")


def notion(method, path, body=None):
    """One Notion API call; raises RuntimeError on any HTTP error. The only writes are the restore and re-trash in read_trashed()."""
    for attempt in range(2):
        req = urllib.request.Request(NOTION + path, method=method, data=json.dumps(body).encode() if body is not None else None)
        req.add_header("Authorization", "Bearer " + NOTION_TOKEN)
        req.add_header("Notion-Version", NOTION_VERSION)
        if body is not None:
            req.add_header("Content-Type", "application/json")
        try:
            with urllib.request.urlopen(req, timeout=60) as r:
                return json.loads(r.read())
        except urllib.error.HTTPError as e:
            if e.code == 429 and attempt == 0:
                time.sleep(float(e.headers.get("Retry-After") or 2))
                continue
            raise RuntimeError(f"notion {method} {path.split('?')[0]}: HTTP {e.code}")
    raise RuntimeError(f"notion {method} {path}: rate limited")


def quiet(method, path, body=None):
    """Like notion(), but a 404 or 403 on one call gives None instead of stopping the run."""
    try:
        return notion(method, path, body)
    except (RuntimeError, urllib.error.URLError, OSError) as e:
        print(f"deletion-review: {e}", file=sys.stderr)
        return None


def search(body, cap=3000):
    items, cursor = [], None
    while len(items) < cap:
        r = notion("POST", "/search", dict(body, page_size=100, **({"start_cursor": cursor} if cursor else {})))
        items += r.get("results", [])
        if not r.get("has_more"):
            break
        cursor = r.get("next_cursor")
    return items


def title_of(obj):
    for p in (obj.get("properties") or {}).values():
        if isinstance(p, dict) and p.get("type") == "title":
            return "".join(t.get("plain_text", "") for t in p.get("title", []))
    return "".join(t.get("plain_text", "") for t in obj.get("title", []) or [])


def parent_ref(obj):
    """(kind, id) of an object's parent; kind is page, database, data_source, block or workspace."""
    p = obj.get("parent") or {}
    kind = (p.get("type") or "workspace").replace("_id", "")
    return kind, p.get(p.get("type") or "")


def read_text(block_id, budget, depth=2):
    """Plain text of a block's children (recursing a little), plus the child pages and databases it holds."""
    text, children, cursor = [], [], None
    for _ in range(3):
        r = quiet("GET", f"/blocks/{block_id}/children?page_size=100" + (f"&start_cursor={cursor}" if cursor else ""))
        if not r:
            break
        for b in r.get("results", []):
            kind = b.get("type")
            if kind in ("child_page", "child_database"):
                children.append(b.get(kind, {}).get("title", ""))
                continue
            line = "".join(t.get("plain_text", "") for t in (b.get(kind) or {}).get("rich_text", []))
            if line:
                text.append(line)
            if b.get("has_children") and depth > 0 and sum(map(len, text)) < budget:
                sub, subkids = read_text(b["id"], budget - sum(map(len, text)), depth - 1)
                text += sub
                children += subkids
        cursor = r.get("next_cursor")
        if not r.get("has_more") or sum(map(len, text)) >= budget:
            break
    return text, children


def read_trashed(page_id):
    """The text and child pages of a page in trash. The API will not read a trashed page's blocks, so the page is
    restored for the read and put straight back in trash, even if the read fails."""
    if NO_RESTORE:
        return [], []
    if quiet("PATCH", f"/pages/{page_id}", {"in_trash": False}) is None:
        return [], []
    try:
        return read_text(page_id, MAX_TEXT)
    finally:
        if quiet("PATCH", f"/pages/{page_id}", {"in_trash": True}) is None:
            print(f"deletion-review: {page_id} was restored to read it and could not be trashed again", file=sys.stderr)


def excerpt(block_id, limit):
    text, _ = read_text(block_id, limit)
    return " ".join(" ".join(text).split())[:limit]


def load_users():
    users, cursor = {}, None
    while True:
        r = quiet("GET", "/users?page_size=100" + (f"&start_cursor={cursor}" if cursor else ""))
        if not r:
            return users
        for u in r.get("results", []):
            users[u["id"]] = {"name": u.get("name") or "unknown", "type": u.get("type") or "unknown"}
        if not r.get("has_more"):
            return users
        cursor = r.get("next_cursor")


def group(trashed):
    """One entry per trashed root: {"obj": root, "children": [items trashed inside it]}."""
    by_id = {o["id"]: o for o in trashed}
    roots = {}
    for o in trashed:
        top = o
        while parent_ref(top)[1] in by_id:
            top = by_id[parent_ref(top)[1]]
        entry = roots.setdefault(top["id"], {"obj": top, "children": []})
        if o is not top:
            entry["children"].append(o)
    return list(roots.values())


def substance(words, pages, folded):
    """S in 0..1 from word count and structure: 1 - (1 - words/300) * (1 - units/8), each part capped at 1.
    units = child pages, database rows and folded children; an empty page scores 0, 300 words or 8 units scores 1."""
    return round(1 - (1 - min(words / 300, 1)) * (1 - min((pages + folded) / 8, 1)), 3)


def risk(s, l, r, e):
    return round(s * l * (1 - 0.5 * r) * (1 - 0.3 * e), 3)


def unit(v):
    try:
        return min(max(float(v), 0.0), 1.0)
    except (TypeError, ValueError):
        return 0.0


def keywords(title):
    words = [w for w in re.findall(r"[A-Za-z0-9][A-Za-z0-9'+&-]*", title) if len(w) > 3 and w.lower() not in STOPWORDS]
    return words[:4]


def live_replacements(item, users, skip):
    """Up to 5 live pages or data sources that might replace the deleted item, with a short text excerpt."""
    words, found, seen = keywords(item["title"]), [], set(skip)
    queries = [" ".join(words)] + words[:2] if words else []
    for q in dict.fromkeys(queries):
        r = quiet("POST", "/search", {"query": q, "page_size": 10})
        for o in (r or {}).get("results", []):
            if len(found) >= MAX_REPLACEMENTS:
                break
            if o["id"] in seen or o.get("in_trash"):
                continue
            seen.add(o["id"])
            found.append({"id": o["id"], "kind": o.get("object"), "title": title_of(o), "url": o.get("url"),
                          "last_edited_time": o.get("last_edited_time"),
                          "last_edited_by": users.get((o.get("last_edited_by") or {}).get("id"), {}).get("name"),
                          "excerpt": excerpt(o["id"], MAX_EXCERPT) if o.get("object") == "page" else ""})
    return found


def comments(block_id):
    r = quiet("GET", f"/comments?block_id={block_id}&page_size=50") or {}
    return [{"created_time": c.get("created_time"),
             "text": "".join(t.get("plain_text", "") for t in c.get("rich_text", []))[:400]}
            for c in r.get("results", [])]


def review_item(entry, users):
    """Everything Hermes needs about one trashed root, with the Substance score."""
    obj, kids = entry["obj"], entry["children"]
    is_page = obj.get("object") == "page"
    text, child_pages = read_trashed(obj["id"]) if is_page else ([], [])
    body = " ".join(" ".join(text).split())
    if not is_page:
        # A data source: its rows are the trashed items folded into it (the query endpoint is not used).
        child_pages = [title_of(k) for k in kids]
    words = len(body.split()) + sum(len(t.split()) for t in (title_of(k) for k in kids))
    # The deleter comes from the search result taken before the restore, so the job never shows as the deleter.
    editor = users.get((obj.get("last_edited_by") or {}).get("id"), {})
    kind, pid = parent_ref(obj)
    parent = {"kind": kind, "id": pid, "title": None, "url": None, "excerpt": ""}
    if kind == "page" and pid:
        p = quiet("GET", f"/pages/{pid}")
        if p:
            parent.update(title=title_of(p), url=p.get("url"), excerpt=excerpt(pid, MAX_EXCERPT))
    expl = []
    if is_page:
        expl += [dict(c, on="deleted item") for c in comments(obj["id"])]
    if kind in ("page", "block") and pid:
        expl += [dict(c, on="parent") for c in comments(pid)]
    return {
        "id": obj["id"], "kind": obj.get("object"), "title": title_of(obj) or "(untitled)", "url": obj.get("url"),
        "parent": parent,
        "deleter": editor.get("name", "unknown"), "deleter_type": editor.get("type", "unknown"),
        # last_edited_time is the last edit of the item, which is the moment it was trashed in practice.
        "deleted_at": obj.get("last_edited_time"),
        "S": substance(words, len(child_pages), len(kids)),
        "words": words, "child_pages": child_pages[:20], "folded_children": len(kids),
        "content_excerpt": body[:MAX_TEXT],
        "replacement_candidates": live_replacements({"title": title_of(obj)}, users, [obj["id"]] + [k["id"] for k in kids]),
        "explanation_candidates": expl[:20],
        "_all_ids": [obj["id"]] + [k["id"] for k in kids],
    }


def load_state():
    """The saved state, or None when the page does not exist yet (the very first run)."""
    try:
        page = call("get_page", {"slug": STATE})
    except RuntimeError:
        return None
    body = (page.get("compiled_truth") or "") if isinstance(page, dict) else ""
    m = re.search(r"```json\n(.*?)\n```", body, re.S)
    state = json.loads(m.group(1)) if m else {}
    # gbrain refuses a put_page on an existing page without the revision just read.
    revision = page.get("revision") if isinstance(page, dict) else None
    return {"seen": state.get("seen", []), "applied": state.get("applied", []),
            "pending": state.get("pending", {}), "log": state.get("log", []), "_revision": revision}


def save_state(state):
    revision = state.pop("_revision", None)
    state["log"] = state["log"][-MAX_LOG:]
    if len(state["pending"]) > MAX_PENDING:
        state["pending"] = dict(list(state["pending"].items())[-MAX_PENDING:])
    content = ("---\ntype: note\ntitle: Deletion review state\n---\n\n# Deletion review state\n\n"
               "Notion items moved to trash that the deletion-review job has seen, and its risk log (log only, nothing is "
               "restored or changed in Notion). Kept by cron-deletion-review.py.\n\n"
               "```json\n" + json.dumps(state) + "\n```\n")
    args = {"slug": STATE, "content": content}
    if revision is not None:
        args["expected_revision"] = revision
    call("put_page", args)


def apply_results(state):
    """Log a risk entry for every item in a saved deletion-review answer not applied yet; returns the files applied."""
    applied = []
    try:
        jobs = os.listdir(OUTPUT_DIR)
    except OSError:
        return applied
    for job in jobs:
        d = os.path.join(OUTPUT_DIR, job)
        for f in sorted(os.listdir(d)) if os.path.isdir(d) else []:
            key = f"{job}/{f}"
            if key in state["applied"] or not f.endswith(".md"):
                continue
            text = open(os.path.join(d, f), encoding="utf-8", errors="replace").read()
            if MARKER not in text:
                continue
            m = re.search(r"\[.*\]", text.split(MARKER, 1)[1], re.S)
            try:
                answers = json.loads(m.group(0)) if m else []
            except json.JSONDecodeError:
                answers = []
            for a in answers if isinstance(answers, list) else []:
                item = state["pending"].pop(str(a.get("id")), None) if isinstance(a, dict) else None
                if not item:
                    continue
                l, r, e = unit(a.get("L")), unit(a.get("R")), unit(a.get("E"))
                score = risk(item["S"], l, r, e)
                state["log"].append(dict(item, L=l, R=r, E=e, risk=score, would_flag=score > THRESHOLD,
                                         reason=str(a.get("reason") or "")[:300]))
            applied.append(key)
    return applied


def main():
    if not NOTION_TOKEN:
        print("deletion-review: NOTION_DELETION_REVIEW_TOKEN is not set; nothing to do", file=sys.stderr)
        print(json.dumps({"wakeAgent": False}))
        return
    now = datetime.datetime.now(datetime.timezone.utc)
    state = load_state()
    first_run = state is None
    state = state or {"seen": [], "applied": [], "pending": {}, "log": []}
    applied = apply_results(state)
    state["applied"] += applied
    trashed = [o for o in search({"filter": {"in_trash": True}}) if o.get("in_trash")]
    seen = set(state["seen"])
    roots = [e for e in group(trashed) if e["obj"]["id"] not in seen]
    if first_run:
        # Backlog: what was trashed more than 48h ago is not reviewed, only marked seen.
        cutoff = (now - datetime.timedelta(hours=BACKLOG_HOURS)).isoformat()
        old = [e for e in roots if (e["obj"].get("last_edited_time") or "") < cutoff]
        state["seen"] += [i for e in old for i in [e["obj"]["id"]] + [k["id"] for k in e["children"]]]
        roots = [e for e in roots if e not in old]
    roots.sort(key=lambda e: e["obj"].get("last_edited_time") or "")
    users = load_users() if roots else {}
    batch = []
    for entry in roots[:BATCH]:
        item = review_item(entry, users)
        ids = item.pop("_all_ids")
        state["seen"] += ids
        state["pending"][item["id"]] = {k: item[k] for k in ("id", "title", "url", "deleter", "deleted_at", "S")}
        batch.append(item)
    if (batch or applied or first_run) and not DRY_RUN:
        save_state(state)
    if not batch:
        print(json.dumps({"wakeAgent": False}))
        return
    print(f"{len(batch)} trashed items to review; {len(roots) - len(batch)} wait for the next run.")
    print(json.dumps(batch, indent=1))


if __name__ == "__main__":
    main()
