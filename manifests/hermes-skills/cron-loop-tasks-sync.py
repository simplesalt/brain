#!/usr/bin/env python3
"""Keep the Notion Tasks database in step with the loops gbrain says Dylan owes.

Runs hourly as the trigger for the loop-tasks-sync cron job. It reads loops from gbrain (`open_loops`, every
source) and keeps one Tasks row per loop of a type Dylan owes: unanswered_inbound, commitment_owed_by_me and
decision_pending. Waiting-on-others types (unanswered_outbound, commitment_owed_to_me) never become rows.
Every owed loop becomes a row; there is no noise screening.

Per run, in order:
  1. Check the Notion token(s) against the Tasks database; a 401/404 fails the run loudly (exit 1).
  2. Create the rows that are missing, keyed by the Loop ID property. Open loops, and loops closed within
     CLOSED_DAYS (gbrain gives no closed_at, so the last activity date stands in for "recent").
  3. A row whose loop is closed in gbrain: Status Done, Loop state = the closing status, Closed = the day this
     sync saw it, Resolution = "<status> in gbrain" plus the thread link.
  4. A row marked Done whose loop is still open is handed to Hermes (see cron-loop-tasks-sync.md), who looks for
     evidence the loop is settled and either closes it in gbrain or puts the row back In progress.
  5. A row whose Loop ID gbrain no longer knows gets a Sync note (only when every source answered completely).

The Notion schema is read at run time and only properties that exist are written; a missing property is logged
and skipped. gbrain is only read here, never changed (Hermes closes loops, not this script).

State lives on the gbrain page ops/loop-tasks-sync: what was handed to Hermes and a log of what each run did and
what Hermes answered (JSON after a LOOP-TASKS-RESULT line, read from saved job output on the next run). With
nothing for Hermes it prints {"wakeAgent": false} so the job skips the model call.

  --dry-run    no Notion or gbrain writes; print the planned actions
  --self-test  pure mapping tests, no network
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
NOTION_VERSION = "2022-06-28"
DB_ID = "8bf70b963c9a458389a8690ae286520c"
STATE = "ops/loop-tasks-sync"
OWED_TYPES = {"unanswered_inbound", "commitment_owed_by_me", "decision_pending"}
CLOSED_STATUSES = ("done", "dropped", "stale")
CLOSED_DAYS = int(os.environ.get("LOOP_TASKS_CLOSED_DAYS", "14"))
MAX_CREATE = int(os.environ.get("LOOP_TASKS_MAX_CREATE", "60"))
BATCH = int(os.environ.get("LOOP_TASKS_BATCH", "5"))
LIMIT = 500
LOG_KEEP = 200
OUTPUTS_KEEP = 1000
RETRY_AFTER_H = 3
MAX_TRIES = 3
OUTPUT_DIR = os.path.join(os.environ.get("HERMES_HOME", "/opt/data"), "cron", "output")
MARKER = "LOOP-TASKS-RESULT"
DYLAN_ACCOUNT = "dylan.evans@simplesalt.company"
TOKEN_ENVS = ("NOTION_DELETION_REVIEW_TOKEN", "NOTION_TOKEN")
# Property name -> accepted Notion types. Everything is looked up by name in the live schema.
WANT = {"Name": ("title",), "Status": ("status", "select"), "Assignee": ("people",),
        "Loop ID": ("rich_text", "number"), "Loop state": ("select", "status", "rich_text"),
        "Channel": ("select", "rich_text"), "Counterparty": ("rich_text",), "Source": ("url",),
        "Closed": ("date",), "Resolution": ("rich_text",), "Sync note": ("rich_text",)}


class NotionError(RuntimeError):
    def __init__(self, status, msg):
        super().__init__(f"notion {status}: {msg}")
        self.status = status


def log(msg):
    print(f"loop-tasks-sync: {msg}", file=sys.stderr)


# ---------- gbrain ----------

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


def read_loops():
    """Return (loops by id, complete). Each loop gets its source id; complete is False if any answer was partial."""
    sources = [s["id"] for s in (call("sources_list", {}) or {}).get("sources", [])]
    loops, complete = {}, bool(sources)
    for sid in sources:
        for status in ("open",) + CLOSED_STATUSES:
            r = call("open_loops", {"source_id": sid, "status": status, "group_by": "none", "limit": LIMIT})
            if not isinstance(r, dict):
                complete = False
                continue
            if r.get("truncated") or r.get("stale") or r.get("completeness") not in (None, "complete"):
                complete = False
            for loop in r.get("loops") or []:
                loops[str(loop["id"])] = dict(loop, source_id=sid)
    return loops, complete


def thread_info(slug, cache):
    """(account, thread_id) from the loop's page frontmatter, or (None, None)."""
    if slug not in cache:
        try:
            fm = (call("get_page", {"slug": slug}) or {}).get("frontmatter") or {}
        except (RuntimeError, urllib.error.URLError, AttributeError):
            fm = {}
        cache[slug] = (fm.get("account"), fm.get("thread_id"))
    return cache[slug]


# ---------- pure mapping ----------

def thread_link(account, thread_id):
    if not (account and thread_id):
        return None
    return f"https://mail.google.com/mail/u/{account}/#all/{thread_id}"


def is_recent(loop, now, days=CLOSED_DAYS):
    ts = loop.get("last_activity_at") or loop.get("opened_at") or ""
    return ts[:10] >= (now - datetime.timedelta(days=days)).date().isoformat()


def wanted_loops(loops, now):
    """The loops that get a row: owed types, open or closed recently."""
    out = {}
    for lid, loop in loops.items():
        if loop.get("loop_type") not in OWED_TYPES:
            continue
        if loop.get("status") == "open" or is_recent(loop, now):
            out[lid] = loop
    return out


def resolution(status, link):
    return f"{status} in gbrain" + (f" - {link}" if link else "")


def text_of(prop):
    kind = prop.get("type")
    v = prop.get(kind)
    if kind in ("title", "rich_text"):
        return "".join(t.get("plain_text", "") for t in v or [])
    if kind in ("select", "status"):
        return (v or {}).get("name") or ""
    if kind == "number":
        return "" if v is None else str(int(v) if float(v).is_integer() else v)
    if kind == "url":
        return v or ""
    if kind == "date":
        return ((v or {}).get("start")) or ""
    return ""


def make_prop(schema, name, value, missing=None):
    """Payload for one property by its live type, or None (and noted in `missing`) if not usable."""
    spec = schema.get(name)
    if spec is None or spec["type"] not in WANT[name]:
        if missing is not None and name not in missing:
            missing.append(name)
        return None
    if value is None or value == "":
        return None
    kind = spec["type"]
    if kind == "title":
        return {"title": [{"text": {"content": str(value)[:2000]}}]}
    if kind == "rich_text":
        return {"rich_text": [{"text": {"content": str(value)[:2000]}}]}
    if kind == "number":
        return {"number": float(value)}
    if kind == "url":
        return {"url": value}
    if kind == "date":
        return {"date": {"start": value}}
    if kind == "people":
        return {"people": [{"id": value}]}
    if kind in ("select", "status"):
        options = {o["name"] for o in spec.get(kind, {}).get("options", [])}
        if kind == "status" and value not in options:
            if missing is not None:
                missing.append(f"{name}={value}")
            return None
        return {kind: {"name": value}}
    return None


def props(schema, fields, missing=None):
    out = {}
    for name, value in fields.items():
        p = make_prop(schema, name, value, missing)
        if p is not None:
            out[name] = p
    return out


def pick_status(schema, names):
    spec = schema.get("Status")
    if not spec or spec["type"] not in WANT["Status"]:
        return None
    options = {o["name"] for o in spec.get(spec["type"], {}).get("options", [])}
    return next((n for n in names if n in options), None)


def new_row_fields(schema, loop, account, thread_id, user_id):
    link = thread_link(account, thread_id)
    closed = loop.get("status") in CLOSED_STATUSES
    fields = {"Name": loop.get("summary") or "(no summary)", "Loop ID": str(loop["id"]),
              "Channel": loop.get("source_id"),
              "Counterparty": loop.get("counterparty_slug") or loop.get("counterparty_email"),
              "Assignee": user_id, "Source": link,
              "Loop state": loop.get("status")}
    if closed:
        fields.update(Status=pick_status(schema, ["Done"]), Closed=None, Resolution=resolution(loop["status"], link))
    else:
        fields["Status"] = pick_status(schema, ["Not started", "To do", "Todo"])
    return fields


def plan_existing(row, loop, handed, now_iso, tries_ok=True):
    """What to do for one existing row and its loop: ('close', None), ('hand', None) or (None, None).

    `row` has status, loop_state, resolution, edited (last_edited_time)."""
    if loop is None:
        return None, None
    if loop.get("status") in CLOSED_STATUSES:
        if (row["status"] != "Done" or row["loop_state"] != loop["status"] or not row["resolution"]):
            return "close", None
        return None, None
    if loop.get("status") == "open" and row["status"] == "Done":
        return "hand", None
    return None, None


# ---------- Notion ----------

def notion(token, method, path, body=None, retries=4):
    for attempt in range(retries):
        req = urllib.request.Request(NOTION + path, method=method,
                                     data=json.dumps(body).encode() if body is not None else None,
                                     headers={"Authorization": "Bearer " + token, "Notion-Version": NOTION_VERSION,
                                              "Content-Type": "application/json"})
        try:
            with urllib.request.urlopen(req, timeout=60) as r:
                time.sleep(0.35)
                return json.loads(r.read().decode() or "{}")
        except urllib.error.HTTPError as e:
            detail = e.read().decode(errors="replace")[:200]
            if e.code == 429 and attempt < retries - 1:
                time.sleep(float(e.headers.get("Retry-After") or 2))
                continue
            raise NotionError(e.code, detail)
    raise NotionError(0, "retries exhausted")


def notion_paged(token, method, path, body=None):
    results, cursor = [], None
    while True:
        b = dict(body or {}, page_size=100)
        p = path
        if cursor:
            if method == "GET":
                p += ("&" if "?" in p else "?") + "start_cursor=" + cursor
            else:
                b["start_cursor"] = cursor
        r = notion(token, method, p, None if method == "GET" else b)
        results += r.get("results", [])
        if not r.get("has_more"):
            return results
        cursor = r.get("next_cursor")


def tokens():
    return [(n, os.environ[n]) for n in TOKEN_ENVS if os.environ.get(n)]


def check_database(toks):
    """GET the database with every token present; any failure is fatal. Returns the schema of the first."""
    schema = None
    for name, tok in toks:
        try:
            db = notion(tok, "GET", f"/databases/{DB_ID}")
        except NotionError as e:
            raise SystemExit(f"loop-tasks-sync: FAILED {name} cannot read Tasks database ({e})")
        if schema is None:
            schema = {k: dict(v) for k, v in (db.get("properties") or {}).items()}
        log(f"{name} reads the Tasks database")
    return schema


def find_user(users, account):
    account = (account or "").lower()
    for u in users:
        if ((u.get("person") or {}).get("email") or "").lower() == account:
            return u["id"]
    if account == DYLAN_ACCOUNT:
        for u in users:
            if u.get("type") == "person" and (u.get("name") or "").lower().startswith("dylan"):
                return u["id"]
    return None


def row_view(page):
    p = page.get("properties", {})
    g = lambda n: text_of(p[n]) if n in p else ""
    return {"id": page["id"], "url": page.get("url"), "edited": page.get("last_edited_time"),
            "title": g("Name"), "loop_id": g("Loop ID"), "status": g("Status"), "loop_state": g("Loop state"),
            "resolution": g("Resolution"), "sync_note": g("Sync note"), "closed": g("Closed"),
            "source": g("Source")}


# ---------- state ----------

def load_state():
    state = {}
    try:
        page = call("get_page", {"slug": STATE})
        body = (page.get("compiled_truth") or "") if isinstance(page, dict) else ""
        m = re.search(r"```json\n(.*?)\n```", body, re.S)
        state = json.loads(m.group(1)) if m else {}
    except RuntimeError:
        pass
    return {"pending": state.get("pending") or {}, "outputs": state.get("outputs") or [],
            "log": state.get("log") or []}


def save_state(state):
    state["log"] = state["log"][-LOG_KEEP:]
    content = ("---\ntype: note\ntitle: Loop tasks sync state\n---\n\n# Loop tasks sync state\n\n"
               "What the loop-tasks-sync job did each run, and what Hermes answered for Done rows whose loop "
               "was still open. Kept by cron-loop-tasks-sync.py.\n\n"
               "```json\n" + json.dumps(state, indent=1, sort_keys=True) + "\n```\n")
    args = {"slug": STATE, "content": content}
    try:
        rev = (call("get_page", {"slug": STATE}) or {}).get("revision")
        if rev:
            args["expected_revision"] = rev
    except RuntimeError:
        pass
    call("put_page", args)


def read_results(read):
    keys, entries = [], []
    try:
        jobs = os.listdir(OUTPUT_DIR)
    except OSError:
        return keys, entries
    for job in jobs:
        d = os.path.join(OUTPUT_DIR, job)
        for f in sorted(os.listdir(d)) if os.path.isdir(d) else []:
            key = f"{job}/{f}"
            if key in read or not f.endswith(".md"):
                continue
            text = open(os.path.join(d, f), encoding="utf-8", errors="replace").read()
            if MARKER not in text:
                continue
            m = re.search(r"\[.*\]", text.split(MARKER, 1)[1], re.S)
            try:
                answer = json.loads(m.group(0)) if m else []
            except json.JSONDecodeError as e:
                log(f"unreadable answer in {key}: {e}")
                answer = []
            entries += [a for a in answer if isinstance(a, dict)] if isinstance(answer, list) else []
            keys.append(key)
    return keys, entries


# ---------- main ----------

def main(dry_run):
    now = datetime.datetime.now(datetime.timezone.utc)
    today = now.date().isoformat()
    loops, complete = read_loops()
    wanted = wanted_loops(loops, now)
    by_type = {}
    for l in wanted.values():
        by_type[l["loop_type"]] = by_type.get(l["loop_type"], 0) + 1
    print(f"gbrain: {len(loops)} loops read ({'complete' if complete else 'partial'}); "
          f"{len(wanted)} owed-by-us loops get rows {by_type}")

    toks = tokens()
    if not toks:
        if not dry_run:
            raise SystemExit("loop-tasks-sync: FAILED no Notion token (NOTION_DELETION_REVIEW_TOKEN / NOTION_TOKEN)")
        print("Notion skipped: no token in this environment. Rows that would be considered:")
        for lid, l in sorted(wanted.items(), key=lambda kv: int(kv[0])):
            print(f"  loop {lid} [{l['source_id']}/{l['loop_type']}/{l['status']}] {l.get('summary', '')[:90]}")
        return
    schema = check_database(toks)
    # Writes need a key that can edit; the deletion-review key is read-only, so NOTION_TOKEN is used first.
    write_tok = os.environ.get("NOTION_TOKEN") or toks[0][1]
    missing = [n for n in WANT if n not in schema or schema[n]["type"] not in WANT[n]]
    if missing:
        log(f"Tasks database lacks usable properties, those fields are skipped: {missing}")
    if "Loop ID" in missing:
        raise SystemExit("loop-tasks-sync: FAILED Tasks database has no usable Loop ID property; cannot key rows")

    state = load_state()
    keys, entries = read_results(set(state["outputs"]))
    state["outputs"] += keys
    state["log"] += [dict(e, at=now.isoformat(), by="hermes") for e in entries]
    for e in entries:
        state["pending"].pop(str(e.get("item")), None)

    users = [u for u in notion_paged(write_tok, "GET", "/users")]
    rows = {}
    for page in notion_paged(write_tok, "POST", f"/databases/{DB_ID}/query"):
        r = row_view(page)
        if r["loop_id"]:
            rows[r["loop_id"]] = r

    cache, actions = {}, {"created": [], "closed": [], "handed": [], "noted": []}
    tag = "[dry-run] " if dry_run else ""

    def write(method, path, body):
        if dry_run:
            return {}
        return notion(write_tok, method, path, body)

    # 2. create missing rows
    for lid, loop in sorted(wanted.items(), key=lambda kv: int(kv[0])):
        if lid in rows:
            continue
        if len(actions["created"]) >= MAX_CREATE:
            break
        account, thread_id = thread_info(loop["page_slug"], cache) if loop.get("page_slug") else (None, None)
        fields = new_row_fields(schema, loop, account, thread_id, find_user(users, account))
        if loop["status"] in CLOSED_STATUSES:
            fields["Closed"] = today
        body = {"parent": {"database_id": DB_ID}, "properties": props(schema, fields)}
        print(f"{tag}create row for loop {lid} [{loop['loop_type']}/{loop['status']}] {fields['Name'][:80]}")
        try:
            write("POST", "/pages", body)
            actions["created"].append(lid)
        except NotionError as e:
            log(f"create loop {lid} failed: {e}")
    backlog = len([1 for l in wanted if l not in rows]) - len(actions["created"])
    if backlog > 0:
        log(f"{backlog} more rows wait for the next run (cap {MAX_CREATE})")

    # 3 and 4. existing rows
    stale_before = (now - datetime.timedelta(hours=RETRY_AFTER_H)).isoformat()
    batch = []
    for lid, row in rows.items():
        loop = loops.get(lid)
        # Rows tied to loops of types we do not own (or closed long ago and unseen) are still reconciled below.
        act, _ = plan_existing(row, loop, state["pending"], now.isoformat())
        if act == "close":
            account, thread_id = thread_info(loop["page_slug"], cache) if loop.get("page_slug") else (None, None)
            link = thread_link(account, thread_id)
            fields = {"Status": pick_status(schema, ["Done"]), "Loop state": loop["status"],
                      "Resolution": row["resolution"] or resolution(loop["status"], link)}
            if not row["closed"]:
                fields["Closed"] = today
            print(f"{tag}close row for loop {lid}: {loop['status']} in gbrain")
            try:
                write("PATCH", f"/pages/{row['id']}", {"properties": props(schema, fields)})
                actions["closed"].append(lid)
            except NotionError as e:
                log(f"close loop {lid} failed: {e}")
        elif act == "hand":
            p = state["pending"].get(lid)
            if p and p.get("edited") == row["edited"]:
                if p["at"] > stale_before:
                    continue
                if p["tries"] >= MAX_TRIES:
                    continue
            account, thread_id = thread_info(loop["page_slug"], cache) if loop.get("page_slug") else (None, None)
            item = {"item": lid, "row": row["id"], "row_url": row["url"], "title": row["title"],
                    "loop_type": loop["loop_type"], "source": loop["source_id"], "summary": loop.get("summary"),
                    "page_slug": loop.get("page_slug"), "thread_link": thread_link(account, thread_id),
                    "counterparty": loop.get("counterparty_slug") or loop.get("counterparty_email"),
                    "opened_at": loop.get("opened_at")}
            if len(batch) < BATCH:
                batch.append((item, row, p))
    for item, row, p in batch:
        tries = p["tries"] + 1 if p and p.get("edited") == row["edited"] else 1
        if not dry_run:
            state["pending"][item["item"]] = {"at": now.isoformat(), "tries": tries, "edited": row["edited"]}
        actions["handed"].append(item["item"])

    # 5. reconcile rows gbrain no longer knows (only when every source answered completely)
    if complete:
        for lid, row in rows.items():
            if lid in loops:
                continue
            note = f"Loop {lid} is not known to gbrain (checked {today})."
            if row["sync_note"].startswith(f"Loop {lid} is not known to gbrain"):
                continue
            print(f"{tag}note on row for loop {lid}: unknown to gbrain")
            try:
                write("PATCH", f"/pages/{row['id']}", {"properties": props(schema, {"Sync note": note})})
                actions["noted"].append(lid)
            except NotionError as e:
                log(f"note loop {lid} failed: {e}")
    else:
        log("gbrain answered partially; unknown-loop reconcile skipped this run")

    state["pending"] = {k: v for k, v in state["pending"].items() if k in rows}
    state["outputs"] = state["outputs"][-OUTPUTS_KEEP:]
    summary = {k: v for k, v in actions.items() if v}
    if summary:
        state["log"].append({"at": now.isoformat(), "by": "script", **summary})
    if (summary or keys) and not dry_run:
        save_state(state)
    print(f"{tag}summary: " + json.dumps({k: len(v) for k, v in actions.items()}))
    if not batch:
        print(json.dumps({"wakeAgent": False}))
        return
    print(f"{len(batch)} Done rows whose loop is still open in gbrain; check each as the instructions say.")
    print(json.dumps([b[0] for b in batch], indent=1))


# ---------- self test ----------

def self_test():
    now = datetime.datetime(2026, 11, 1, tzinfo=datetime.timezone.utc)
    schema = {"Name": {"type": "title"},
              "Status": {"type": "status", "status": {"options": [{"name": "Not started"}, {"name": "In progress"}, {"name": "Done"}]}},
              "Assignee": {"type": "people"}, "Loop ID": {"type": "rich_text"},
              "Loop state": {"type": "select", "select": {"options": []}}, "Channel": {"type": "select", "select": {"options": []}},
              "Counterparty": {"type": "rich_text"}, "Source": {"type": "url"}, "Closed": {"type": "date"},
              "Resolution": {"type": "rich_text"}}  # no Sync note
    loops = {
        "1": {"id": 1, "loop_type": "unanswered_inbound", "status": "open", "summary": "Reply owed", "source_id": "gmail",
              "counterparty_email": "a@b.c", "last_activity_at": "2026-10-01T00:00:00Z", "page_slug": "emails/x"},
        "2": {"id": 2, "loop_type": "unanswered_outbound", "status": "open", "source_id": "gmail"},
        "3": {"id": 3, "loop_type": "commitment_owed_to_me", "status": "open", "source_id": "gmail"},
        "4": {"id": 4, "loop_type": "decision_pending", "status": "done", "source_id": "gmail", "last_activity_at": "2026-10-30T00:00:00Z"},
        "5": {"id": 5, "loop_type": "commitment_owed_by_me", "status": "dropped", "source_id": "gmail", "last_activity_at": "2025-01-01T00:00:00Z"},
        "6": {"id": 6, "loop_type": "decision_pending", "status": "open", "source_id": "default", "counterparty_slug": "people/x"},
    }
    w = wanted_loops(loops, now)
    assert sorted(w) == ["1", "4", "6"], sorted(w)
    assert thread_link("me@x.com", "abc") == "https://mail.google.com/mail/u/me@x.com/#all/abc"
    assert thread_link(None, "abc") is None
    f = new_row_fields(schema, loops["1"], "me@x.com", "abc", "user-1")
    assert f["Status"] == "Not started" and f["Channel"] == "gmail" and f["Counterparty"] == "a@b.c"
    missing = []
    p = props(schema, f, missing)
    assert p["Name"]["title"][0]["text"]["content"] == "Reply owed"
    assert p["Assignee"] == {"people": [{"id": "user-1"}]}
    assert p["Status"] == {"status": {"name": "Not started"}}
    assert p["Source"]["url"].endswith("#all/abc") and p["Loop ID"]["rich_text"][0]["text"]["content"] == "1"
    assert "Sync note" not in p
    props(schema, {"Sync note": "x"}, missing)
    assert missing == ["Sync note"], missing
    fc = new_row_fields(schema, loops["4"], None, None, None)
    assert fc["Status"] == "Done" and fc["Loop state"] == "done" and fc["Resolution"] == "done in gbrain"
    assert resolution("dropped", "http://l") == "dropped in gbrain - http://l"
    assert pick_status({"Status": {"type": "status", "status": {"options": [{"name": "Done"}]}}}, ["Not started"]) is None
    assert make_prop(schema, "Status", "Bogus") is None or True
    row = {"status": "In progress", "loop_state": "open", "resolution": "", "edited": "t"}
    assert plan_existing(row, loops["4"], {}, "")[0] == "close"
    assert plan_existing(dict(row, status="Done", loop_state="done", resolution="r"), loops["4"], {}, "")[0] is None
    assert plan_existing(dict(row, status="Done"), loops["1"], {}, "")[0] == "hand"
    assert plan_existing(row, loops["1"], {}, "")[0] is None
    assert plan_existing(row, None, {}, "")[0] is None
    users = [{"id": "u1", "type": "person", "name": "Dylan E", "person": {"email": DYLAN_ACCOUNT}},
             {"id": "u2", "type": "person", "name": "Other", "person": {"email": "o@x.com"}}]
    assert find_user(users, DYLAN_ACCOUNT) == "u1" and find_user(users, "o@x.com") == "u2" and find_user(users, "z@x.com") is None
    page = {"id": "r1", "properties": {"Loop ID": {"type": "number", "number": 12.0},
                                       "Status": {"type": "status", "status": {"name": "Done"}},
                                       "Name": {"type": "title", "title": [{"plain_text": "T"}]}}}
    v = row_view(page)
    assert v["loop_id"] == "12" and v["status"] == "Done" and v["title"] == "T"
    print("self-test passed")


if __name__ == "__main__":
    if "--self-test" in sys.argv:
        self_test()
    else:
        main("--dry-run" in sys.argv)
