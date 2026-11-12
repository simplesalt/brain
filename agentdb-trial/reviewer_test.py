import json
import sys
import time

sys.path.insert(0, "/probe")
from probe import Client

DB = "t934"
TABLE = "notes"
COACH = {
    "purpose": "Live test of the change reviewer and table guidance on the agentdb trial (projects#934).",
    "future_use": "Read only by this test Job, which drops the table and database when it finishes.",
    "other_identities": "None; this is a throwaway test database that no other identity will use.",
}
COLUMNS = [
    {"name": "title", "type": "text"},
    {"name": "body", "type": "text", "nullable": True},
    {"name": "status", "type": "text", "default": "'open'"},
    {"name": "link", "type": "text", "nullable": True},
]
GUIDANCE = {
    "purpose": "Short notes agents leave for each other about ongoing work.",
    "use": "Insert one note per finding with a title and a short body; update status when the work closes.",
    "rules": [
        {"column": "body", "kind": "max_bytes", "max": 2000, "instead": "Store long content elsewhere and put its URL in link."},
        {"column": "status", "kind": "one_of", "values": ["open", "done"]},
        {"column": "title", "kind": "required"},
    ],
}


def log(kind, **kw):
    print(json.dumps(dict(kind=kind, **kw)), flush=True)


def call(c, name, args):
    log("call", tool=name, arguments=args)
    try:
        res = c.request("tools/call", {"name": name, "arguments": args})
        is_error = bool(res.get("isError"))
        text = "\n".join(x.get("text", "") for x in res.get("content", []) if isinstance(x, dict))
    except RuntimeError as e:
        is_error, text = True, str(e)
    log("response", tool=name, isError=is_error, text=text)
    return is_error, text


def words(s):
    return len((s or "").split())


def tools(c):
    return c.request("tools/list", {}).get("tools", [])


def report_descriptions(label, ts):
    counts = []
    for t in ts:
        n = words(t.get("description"))
        counts.append(n)
        log("description", set=label, tool=t["name"], words=n, description=t.get("description"))
    if counts:
        log("description_stats", set=label, tools=len(counts), max=max(counts), avg=round(sum(counts) / len(counts), 1),
            over_100=sum(1 for n in counts if n > 100), at_or_under_30=sum(1 for n in counts if n <= 30))


def structural(c, name, args):
    for confirm in (False, True):
        err, text = call(c, name, dict(args, **COACH, confirm=confirm))
        if err:
            return False, text
    return True, text


def create_table(c):
    guidance = GUIDANCE
    for rnd in range(1, 6):
        log("round", n=rnd)
        args = dict(database=DB, table=TABLE, columns=COLUMNS, guidance=guidance, **COACH)
        err, text = call(c, "create_table", dict(args, confirm=False))
        if err:
            return False
        r = json.loads(text)
        rv = r.get("review", {})
        if rv.get("agreed"):
            err, text = call(c, "create_table", dict(args, confirm=True))
            if err:
                return False
            r = json.loads(text)
            if r.get("applied"):
                log("agreed_guidance", guidance=guidance, round=rnd)
                return True
            rv = r.get("review", {})
        if rv.get("proposed_guidance"):
            guidance = rv["proposed_guidance"]
    return False


def main():
    c = Client()
    c.initialize()
    report_descriptions("before", tools(c))
    ok, _ = structural(c, "create_database", {"database": DB})
    if not ok:
        log("fatal", step="create_database")
        return 1
    rc = 0
    try:
        if not create_table(c):
            log("fatal", step="create_table")
            return 1
        prefix = DB + "__" + TABLE + "__"
        for _ in range(12):
            ts = tools(c)
            if any(t["name"] == prefix + "write" for t in ts):
                break
            time.sleep(5)
        report_descriptions("table", [t for t in ts if t["name"].startswith(prefix)])
        report_descriptions("all", ts)
        err, _ = call(c, prefix + "write", {"operation": "insert", "record": {"title": "Reviewer test", "body": "A short note that fits the rules.", "status": "open"}})
        log("check", name="common_call_works", ok=not err)
        err2, _ = call(c, prefix + "find", {"where": {"title": "Reviewer test"}})
        log("check", name="common_find_works", ok=not err2)
        err3, text3 = call(c, prefix + "write", {"operation": "insert", "record": {"title": "Oversized", "body": "x" * 10000, "status": "open"}})
        log("check", name="oversized_refused_with_steering", ok=err3 and "too big" in text3, refusal=text3)
        err4, text4 = call(c, "insert_record", {"database": DB, "table": TABLE, "record": {"title": "Bad status", "status": "pending"}})
        log("check", name="one_of_refused_with_steering", ok=err4 and "not an agreed value" in text4, refusal=text4)
    finally:
        ok1, _ = structural(c, "drop_table", {"database": DB, "table": TABLE})
        ok2, _ = structural(c, "drop_database", {"database": DB})
        log("cleanup", drop_table=ok1, drop_database=ok2)
        if not (ok1 and ok2):
            rc = 1
    return rc


if __name__ == "__main__":
    sys.exit(main())
