import json
import sys

sys.path.insert(0, "/probe")
from probe import Client
import probe

probe.TIMEOUT = 180

DB = "t934c"
TABLE = "notes"
COACH = {
    "purpose": "Live test of the change reviewer's cached agreed verdict and the named drop_table prompt on the agentdb trial (projects#934).",
    "future_use": "Read only by this test Job, which drops the table and database when it finishes.",
    "other_identities": "None; this is a throwaway test database that no other identity will use.",
}
COLUMNS = [
    {"name": "title", "type": "text"},
    {"name": "body", "type": "text", "nullable": True},
    {"name": "status", "type": "text", "default": "'open'"},
]
GUIDANCE = {
    "purpose": "Short notes agents leave for each other about ongoing work.",
    "use": "Insert one note per finding with a title and a short body; update status when the work closes.",
    "rules": [
        {"column": "body", "kind": "max_bytes", "max": 2000, "instead": "Store long content elsewhere and link to it."},
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


def parse(text):
    try:
        return json.loads(text)
    except ValueError:
        return {}


def agree_then_confirm(c, name, args, rounds=5):
    for rnd in range(1, rounds + 1):
        err, text = call(c, name, dict(args, confirm=False))
        if err:
            return {"ok": False, "round": rnd, "stage": "preview"}
        r = parse(text)
        rv = r.get("review", {})
        if rv.get("agreed"):
            err, text = call(c, name, dict(args, confirm=True))
            r2 = parse(text)
            rv2 = r2.get("review", {})
            return {"ok": (not err) and bool(r2.get("applied")), "round": rnd, "stage": "confirm",
                    "confirm_applied": r2.get("applied"), "confirm_review_agreed": rv2.get("agreed") if rv2 else None,
                    "second_disagreement": bool(rv2) and not rv2.get("agreed", True)}
        if rv.get("proposed_guidance") and "guidance" in args:
            args = dict(args, guidance=rv["proposed_guidance"])
    return {"ok": False, "round": rounds, "stage": "never_agreed"}


def main():
    c = Client()
    c.initialize()
    res = agree_then_confirm(c, "create_database", dict(database=DB, **COACH))
    log("check", name="create_database_agree_then_confirm", **res)
    if not res["ok"]:
        return 1
    rc = 0
    try:
        res = agree_then_confirm(c, "create_table", dict(database=DB, table=TABLE, columns=COLUMNS, guidance=GUIDANCE, **COACH))
        log("check", name="create_table_agree_then_confirm", **res)
        if not res["ok"]:
            rc = 1
        else:
            res = agree_then_confirm(c, "drop_table", dict(database=DB, table=TABLE, **COACH))
            log("check", name="drop_table_agree_then_confirm", **res)
            if not res["ok"]:
                rc = 1
    finally:
        res = agree_then_confirm(c, "drop_database", dict(database=DB, **COACH))
        log("cleanup", drop_database=res)
        if not res["ok"]:
            rc = 1
    return rc


if __name__ == "__main__":
    sys.exit(main())
