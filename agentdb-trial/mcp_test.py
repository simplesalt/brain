import datetime
import hashlib
import json
import os
import sys
import time
import urllib.error
import urllib.request
from decimal import Decimal

ENDPOINT = os.environ.get("AGENTDB_ENDPOINT", "")
TIMEOUT = 180
LOG_TEXT = 3000

RESULTS = []
CALLS = []
ROUND = [None]


def enc(v):
    if isinstance(v, Decimal):
        return str(v)
    if isinstance(v, bool) or v is None:
        return json.dumps(v)
    if isinstance(v, (int, float, str)):
        return json.dumps(v, ensure_ascii=False)
    if isinstance(v, dict):
        return "{" + ",".join(json.dumps(k, ensure_ascii=False) + ":" + enc(x) for k, x in v.items()) + "}"
    if isinstance(v, (list, tuple)):
        return "[" + ",".join(enc(x) for x in v) + "]"
    raise TypeError(type(v))


def dec(s):
    return json.loads(s, parse_float=Decimal)


def short(v):
    if isinstance(v, str) and len(v) > 200:
        return "<str len=%d sha256=%s head=%r>" % (len(v), hashlib.sha256(v.encode()).hexdigest()[:16], v[:40])
    if isinstance(v, dict):
        return {k: short(x) for k, x in v.items()}
    if isinstance(v, list):
        return [short(x) for x in v]
    if isinstance(v, Decimal):
        return str(v)
    return v


def log(kind, **kw):
    if kind in ("rpc", "tool") and os.environ.get("QUIET") == "1":
        return
    print(json.dumps(dict(kind=kind, **kw), ensure_ascii=False, default=str), flush=True)


class Client:
    def __init__(self):
        self.session = None
        self.next_id = 1
        self.work_item = ""

    def _post(self, body_text):
        headers = {"Content-Type": "application/json", "Accept": "application/json, text/event-stream"}
        if self.session:
            headers["Mcp-Session-Id"] = self.session
        if self.work_item:
            headers["X-Agentdb-Work-Item"] = self.work_item
        req = urllib.request.Request(ENDPOINT, data=body_text.encode("utf-8"), headers=headers, method="POST")
        try:
            resp = urllib.request.urlopen(req, timeout=TIMEOUT)
        except urllib.error.HTTPError as e:
            return None, "HTTP %d: %s" % (e.code, e.read().decode("utf-8", "replace")[:LOG_TEXT])
        with resp:
            sid = resp.headers.get("Mcp-Session-Id")
            if sid:
                self.session = sid
            ctype = resp.headers.get("Content-Type", "")
            raw = resp.read().decode("utf-8")
        return ctype, raw

    def rpc(self, method, params, quiet=False):
        rid = self.next_id
        self.next_id += 1
        body = '{"jsonrpc":"2.0","id":%d,"method":%s,"params":%s}' % (rid, json.dumps(method), enc(params))
        ctype, raw = self._post(body)
        msg = None
        if ctype is None:
            msg = {"error": {"transport": raw}}
        elif "text/event-stream" in ctype:
            for line in raw.splitlines():
                if line.startswith("data:"):
                    try:
                        m = dec(line[5:].strip())
                    except ValueError:
                        continue
                    if isinstance(m, dict) and m.get("id") == rid:
                        msg = m
            if msg is None:
                msg = {"error": {"parse": "no response with id %d" % rid, "raw": raw[:LOG_TEXT]}}
        else:
            msg = dec(raw) if raw.strip() else {"error": {"parse": "empty body"}}
        if not quiet:
            out = json.dumps(short(msg), ensure_ascii=False, default=str)
            log("rpc", method=method, params=short(params), response=out[:LOG_TEXT], response_len=len(out),
                work_item=self.work_item)
        return msg

    def initialize(self):
        m = self.rpc("initialize", {"protocolVersion": "2025-06-18", "capabilities": {},
                                    "clientInfo": {"name": "agentdb-t1", "version": "1"}})
        self._post('{"jsonrpc":"2.0","method":"notifications/initialized"}')
        return m

    def call(self, name, args=None):
        a0 = time.time()
        m = self.rpc("tools/call", {"name": name, "arguments": args or {}})
        ms = int((time.time() - a0) * 1000)
        if "error" in m:
            return True, None, json.dumps(m["error"], default=str)
        r = m.get("result", {})
        text = "\n".join(c.get("text", "") for c in r.get("content", []) if isinstance(c, dict))
        sc = r.get("structuredContent")
        if sc is None:
            try:
                sc = dec(text)
            except ValueError:
                sc = None
        rv = (sc or {}).get("review") if isinstance(sc, dict) else None
        model = isinstance(rv, dict) and "agreed" in rv
        CALLS.append({"n": len(CALLS) + 1, "tool": name, "ms": ms, "round": ROUND[0], "agreed": rv.get("agreed") if model else None,
                      "model": model, "error": bool(r.get("isError")) or "error" in m})
        print("CALL %d %s %dms round=%s agreed=%s model=%s error=%s" % (len(CALLS), name, ms, ROUND[0] or "-",
              CALLS[-1]["agreed"] if model else "-", "Y" if model else "N", "Y" if CALLS[-1]["error"] else "N"), flush=True)
        return bool(r.get("isError")), sc, text


def record(step, name, ok, detail):
    RESULTS.append({"step": step, "check": name, "pass": bool(ok), "detail": short(detail)})
    log("check", step=step, check=name, result="PASS" if ok else "FAIL", detail=short(detail))


COACH = {
    "purpose": "Effort projects#880 T2: final check of agentdb MCP row fidelity and structural tools on the live trial.",
    "future_use": "Read back by this same test Job only, then dropped at the end of the run; nothing else depends on it.",
    "other_identities": "None; access control is off on this trial and no other identity uses this test database.",
}


def agree(c, tool, args, rounds=5):
    sc, text, err = None, "", True
    for rnd in range(1, rounds + 1):
        ROUND[0] = rnd
        err, sc, text = c.call(tool, dict(args, confirm=False))
        ROUND[0] = None
        if err or not sc:
            return err, sc, text, rnd, False
        rv = sc.get("review") or {}
        if rv.get("agreed") or "agreed" not in rv:
            return err, sc, text, rnd, True
        if rv.get("proposed_guidance") and "guidance" in args:
            args = dict(args, guidance=rv["proposed_guidance"])
            log("adopted_guidance", tool=tool, round=rnd, guidance=args["guidance"])
    return err, sc, text, rounds, False


def structural(step, c, tool, args, label):
    args = dict(args, **COACH)
    err, sc, text, rnd, agreed = agree(c, tool, args)
    ok = (not err) and sc and sc.get("applied") is False and isinstance(sc.get("review"), dict)
    record(step, label + ": confirm=false returns a review and applies nothing (reviewer agreed=%s after %d round(s))" % (agreed, rnd), ok, sc if sc else text)
    if not agreed:
        return None
    if sc and (sc.get("review") or {}).get("proposed_guidance") and "guidance" in args:
        args = dict(args, guidance=sc["review"]["proposed_guidance"])
    err, sc, text = c.call(tool, dict(args, confirm=True))
    ok = (not err) and sc and sc.get("applied") is True and sc.get("change_id")
    record(step, label + ": confirm=true applies and records a change id", ok, sc if sc else text)
    return sc.get("change_id") if ok else None


def ts_equal(a, b):
    def p(s):
        return datetime.datetime.fromisoformat(s.replace("Z", "+00:00"))
    try:
        return p(a) == p(b)
    except Exception:
        return False


def compare(written, got, types):
    diffs = {}
    exact = {}
    for k, w in written.items():
        if k not in got:
            diffs[k] = {"written": w, "read": "<missing>"}
            continue
        g = got[k]
        t = types.get(k, "")
        same_bytes = enc(w) == enc(g)
        if t == "timestamptz":
            same = w is None and g is None or (w is not None and g is not None and ts_equal(w, g))
        elif t == "jsonb":
            same = w == g
        elif t in ("numeric", "integer", "bigint"):
            same = (w is None and g is None) or (w is not None and g is not None and Decimal(str(w)) == Decimal(str(g)))
        else:
            same = w == g and type(w) is type(g)
        exact[k] = same_bytes
        if not same:
            diffs[k] = {"written": w, "read": g, "type": t}
    return diffs, exact


GUIDANCE = {
    "purpose": "Test rows that check every column type round-trips exactly through agentdb.",
    "use": "Insert one row per edge case, read it back and compare; rows are dropped with the table at the end of the test.",
    "rules": [
        {"column": "status", "kind": "one_of", "values": ["open", "done"], "instead": "Use open or done; put other states in note."},
    ],
}

TYPES = {
    "title": "text", "body": "text", "note": "text", "n_int": "integer", "n_big": "bigint",
    "amount": "numeric", "flag": "boolean", "at": "timestamptz", "day": "date", "meta": "jsonb", "ref": "uuid",
    "status": "text",
}

LONG = ("Långt fält — 長いテキスト — 🧪 fidelity " * 4000)[:150000]

CASES = [
    ("unicode", {"title": "Ünïcødé — 日本語 — Ελληνικά — עברית — العربية",
                 "body": "combining é vs é, ZWJ 👨‍👩‍👧‍👦, flag 🇳🇿, math 𝔘𝔫𝔦, "
                         "quotes \"' \\ backslash, <html>&amp;, tab\tnewline\nCRLF\r\nend",
                 "note": " line sep para sep ﻿BOM"}),
    ("emoji-only", {"title": "🎉🔥💾🧠", "body": "😀" * 50, "note": "🏳️‍🌈"}),
    ("empty-vs-null", {"title": "empty and null", "body": "", "note": None}),
    ("whitespace", {"title": "   leading and trailing   ", "body": " ", "note": "\n"}),
    ("long-text-150KB", {"title": "long body", "body": LONG, "note": None}),
    ("int-limits-max", {"title": "int max", "n_int": 2147483647, "n_big": 9223372036854775807}),
    ("int-limits-min", {"title": "int min", "n_int": -2147483648, "n_big": -9223372036854775808}),
    ("int-zero", {"title": "zero", "n_int": 0, "n_big": 0}),
    ("big-beyond-2^53", {"title": "2^53+1", "n_big": 9007199254740993}),
    ("decimal-precise", {"title": "decimal precise", "amount": Decimal("12345678901234567890.123456789012345678")}),
    ("decimal-small", {"title": "decimal small", "amount": Decimal("0.1")}),
    ("decimal-negative-tiny", {"title": "decimal negative tiny", "amount": Decimal("-0.000000000000000001")}),
    ("decimal-trailing-zeros", {"title": "decimal trailing zeros", "amount": Decimal("10.500")}),
    ("bool-true", {"title": "bool true", "flag": True}),
    ("bool-false", {"title": "bool false", "flag": False}),
    ("timestamp-+13", {"title": "ts plus 13", "at": "2026-11-12T18:00:00.123456+13:00"}),
    ("timestamp-Z", {"title": "ts zulu", "at": "2026-02-28T23:59:59Z"}),
    ("timestamp--0930", {"title": "ts minus 0930", "at": "1999-12-31T23:59:59.999999-09:30"}),
    ("date-leap", {"title": "leap day", "day": "2024-02-29"}),
    ("jsonb-object", {"title": "json object", "meta": {"a": [1, Decimal("2.5"), "x", None, True],
                                                        "nested": {"emoji": "🎉", "empty": "", "nul": None},
                                                        "big": 12345678901234567890}}),
    ("jsonb-array", {"title": "json array", "meta": [1, "two", {"three": 3}]}),
    ("jsonb-string", {"title": "json string", "meta": "just a string"}),
    ("uuid", {"title": "uuid", "ref": "0b8f2a52-6a3c-4f63-9d0e-7f1c2b3a4d5e"}),
]

EXPECTED_REFUSALS = [
    ("nul-byte-in-text", {"title": "nul \u0000 byte"}),
    ("int-overflow", {"title": "overflow", "n_int": 2147483648}),
    ("bad-uuid", {"title": "bad uuid", "ref": "not-a-uuid"}),
    ("bad-timestamp", {"title": "bad ts", "at": "yesterday-ish"}),
]


def step1(c, init):
    s = 1
    r = init.get("result", {}) if init else {}
    log("guidance", instructions=r.get("instructions"), capabilities=r.get("capabilities"), serverInfo=r.get("serverInfo"))
    record(s, "initialize returns server instructions", bool(r.get("instructions")), {"instructions": r.get("instructions"), "capabilities": r.get("capabilities")})
    m = c.rpc("tools/list", {})
    tools = m.get("result", {}).get("tools", [])
    log("tools", names=[t["name"] for t in tools])
    for t in tools:
        log("tool", name=t["name"], description=t.get("description"), inputSchema=t.get("inputSchema"))
    record(s, "tools/list returns tools", len(tools) > 0, [t["name"] for t in tools])
    for meth in ("prompts/list", "resources/list", "resources/templates/list"):
        m = c.rpc(meth, {})
        record(s, meth + " (guidance via prompts/resources)", "result" in m and any(m["result"].get(k) for k in ("prompts", "resources", "resourceTemplates")),
               m.get("result", m.get("error")))
    names = {t["name"] for t in tools}
    guidance = [n for n in names if any(w in n for w in ("guide", "help", "coach", "howto", "usage", "instructions"))]
    record(s, "a dedicated guidance/coaching tool is offered", bool(guidance), {"matching_tools": guidance})
    for n, a in (("whoami", {}), ("check_access", {"operation": "admin"}), ("list_tables", {}),
                 ("list_structure_changes", {"limit": 5})):
        err, sc, text = c.call(n, a)
        record(s, "call " + n, not err, sc if sc is not None else text)
    return names


def step2(c, db, table):
    s = 2
    err, sc, text = c.call("create_database", {"database": db})
    record(s, "create_database with no purpose/future_use/other_identities is refused", err and "refused" in text, text)
    err, sc, text = c.call("create_database", {"database": db, "purpose": "test", "future_use": "test", "other_identities": "none", "confirm": True})
    record(s, "create_database with too-short answers and confirm=true is refused", err and "refused" in text, text)
    err, sc, text = c.call("list_tables", {"database": db})
    ch = c.call("list_structure_changes", {"database": db})
    record(s, "nothing was created by the refused calls", not ch[0] and ch[1] is not None and not ch[1].get("changes"), ch[1] if ch[1] else ch[2])
    cid_db = structural(s, c, "create_database", {"database": db}, "create_database")
    cols = [
        {"name": "title", "type": "text"},
        {"name": "body", "type": "text", "nullable": True},
        {"name": "note", "type": "text", "nullable": True},
        {"name": "n_int", "type": "integer", "nullable": True},
        {"name": "n_big", "type": "bigint", "nullable": True},
        {"name": "amount", "type": "numeric", "nullable": True},
        {"name": "flag", "type": "boolean", "nullable": True},
        {"name": "at", "type": "timestamptz", "nullable": True},
        {"name": "day", "type": "date", "nullable": True},
        {"name": "meta", "type": "jsonb", "nullable": True},
        {"name": "ref", "type": "uuid", "nullable": True},
        {"name": "status", "type": "text", "default": "'open'"},
    ]
    err, sc, text = c.call("create_table", {"database": db, "table": table, "columns": cols, "purpose": COACH["purpose"]})
    record(s, "create_table missing future_use/other_identities is refused", err and "refused" in text, text)
    cid_tbl = structural(s, c, "create_table", {"database": db, "table": table, "columns": cols, "guidance": GUIDANCE}, "create_table")
    err, sc, text = c.call("list_tables", {"database": db})
    found = [t for t in (sc or {}).get("tables", []) if (t.get("table") or t.get("name")) == table]
    record(s, "list_tables shows the new table and its column types", bool(found), found)
    m = c.rpc("tools/list", {})
    typed = sorted(t["name"] for t in m.get("result", {}).get("tools", []) if t["name"].startswith(db + "__"))
    record(s, "typed per-table tools appear in tools/list after create_table", len(typed) >= 3, typed)
    return cid_db, cid_tbl, typed


def write_and_check(c, s, db, table, label, row, typed):
    if typed:
        err, sc, text = c.call("%s__%s__write" % (db, table), {"operation": "insert", "record": row})
    else:
        err, sc, text = c.call("insert_record", {"database": db, "table": table, "record": row})
    if err or not sc:
        record(s, label + ": insert", False, text)
        return None
    rid = sc["record"]["id"]
    diffs_ret, _ = compare(row, sc["record"], TYPES)
    if typed:
        err, sc2, text2 = c.call("%s__%s__get" % (db, table), {"key": {"id": rid}})
    else:
        err, sc2, text2 = c.call("get_record", {"database": db, "table": table, "key": {"id": rid}})
    if err or not sc2:
        record(s, label + ": read back", False, text2)
        return rid
    diffs, exact = compare(row, sc2["record"], TYPES)
    not_byte_exact = {k: {"written": enc(row[k])[:120], "read": enc(sc2["record"][k])[:120]} for k, v in exact.items() if not v}
    record(s, label + ": read-back equals written value", not diffs and not diffs_ret,
           {"id": rid, "diffs": diffs, "insert_return_diffs": diffs_ret, "not_byte_identical": not_byte_exact})
    return rid


def step4(c, db, table, typed_tools):
    s = 4
    ids = {}
    for path in ("generic", "typed"):
        typed = path == "typed"
        if typed and "%s__%s__write" % (db, table) not in typed_tools:
            record(s, "typed write tool available", False, typed_tools)
            continue
        for label, row in CASES:
            rid = write_and_check(c, s, db, table, "%s insert %s" % (path, label), row, typed)
            ids[(path, label)] = rid
        for label, row in EXPECTED_REFUSALS:
            if typed:
                err, sc, text = c.call("%s__%s__write" % (db, table), {"operation": "insert", "record": row})
            else:
                err, sc, text = c.call("insert_record", {"database": db, "table": table, "record": row})
            record(s, "%s invalid value refused cleanly: %s" % (path, label), err, text[:500] if text else sc)
    rid = ids.get(("generic", "empty-vs-null"))
    if rid:
        changes = {"body": None, "note": "", "amount": Decimal("-1.25"), "flag": True, "meta": {"k": "🎯"}}
        err, sc, text = c.call("update_record", {"database": db, "table": table, "key": {"id": rid}, "changes": changes})
        err2, sc2, text2 = c.call("get_record", {"database": db, "table": table, "key": {"id": rid}})
        diffs, _ = compare(changes, (sc2 or {}).get("record", {}), TYPES)
        untouched = (sc2 or {}).get("record", {}).get("title") == "empty and null"
        record(s, "update_record swaps empty string and null, sets others; read-back matches; other fields kept",
               not err and not diffs and untouched, {"diffs": diffs, "record": (sc2 or {}).get("record"), "update": sc or text})
    rid = ids.get(("typed", "unicode"))
    if rid:
        changes = {"body": "updated via typed write ✅ 𝔘"}
        err, sc, text = c.call("%s__%s__write" % (db, table), {"operation": "update", "key": {"id": rid}, "record": changes})
        err2, sc2, text2 = c.call("get_record", {"database": db, "table": table, "key": {"id": rid}})
        diffs, _ = compare(changes, (sc2 or {}).get("record", {}), TYPES)
        record(s, "typed write update read back", not err and not diffs, {"diffs": diffs, "resp": sc or text})
    rid = ids.get(("generic", "emoji-only"))
    if rid:
        err, sc, text = c.call("delete_record", {"database": db, "table": table, "key": {"id": rid}})
        err2, sc2, text2 = c.call("get_record", {"database": db, "table": table, "key": {"id": rid}})
        record(s, "delete_record removes the row; get_record then reports not found",
               not err and err2 and "not found" in text2, {"delete": sc or text, "get_after": text2})
        ids["deleted"] = rid
    rid = ids.get(("typed", "emoji-only"))
    if rid:
        err, sc, text = c.call("%s__%s__write" % (db, table), {"operation": "delete", "key": {"id": rid}})
        err2, sc2, text2 = c.call("get_record", {"database": db, "table": table, "key": {"id": rid}})
        record(s, "typed write delete removes the row", not err and err2, {"delete": sc or text, "get_after": text2})
    return ids


def step3(c, db, table):
    s = 3
    cid_add = structural(s, c, "add_column", {"database": db, "table": table,
                                              "column": {"name": "extra", "type": "text", "nullable": True}}, "add_column extra")
    structural(s, c, "add_column", {"database": db, "table": table,
                                    "column": {"name": "score", "type": "integer", "default": "7"}}, "add_column score default 7")
    vals = {}
    for i, v in enumerate(["dropped-value-one ✨", "", None, "dropped value four"]):
        err, sc, text = c.call("insert_record", {"database": db, "table": table, "record": {"title": "column test %d" % i, "extra": v}})
        if not err:
            vals[sc["record"]["id"]] = v
            record(s, "insert into new column extra (value %r) and default score=7 applied" % (v,),
                   sc["record"].get("extra") == v and sc["record"].get("score") == 7, sc["record"])
        else:
            record(s, "insert into new column extra", False, text)
    structural(s, c, "rename_column", {"database": db, "table": table, "from": "extra", "to": "detail"}, "rename_column extra->detail")
    for rid, v in vals.items():
        err, sc, text = c.call("get_record", {"database": db, "table": table, "key": {"id": rid}})
        rec = (sc or {}).get("record", {})
        record(s, "after rename id %s: value under detail, no extra" % rid, "extra" not in rec and rec.get("detail") == v, rec)
    err, sc, text = c.call("rename_column", {"database": db, "table": table, "from": "id", "to": "pk", **COACH, "confirm": False})
    record(s, "rename of a primary key column is refused", err, text)
    cid_drop = structural(s, c, "drop_column", {"database": db, "table": table, "column": "detail"}, "drop_column detail")
    for rid in vals:
        err, sc, text = c.call("get_record", {"database": db, "table": table, "key": {"id": rid}})
        rec = (sc or {}).get("record", {})
        record(s, "after drop id %s: detail gone" % rid, not err and "detail" not in rec, rec)
    return cid_drop, vals


def step5(c, db, table, typed_tools):
    s = 5
    tests = [
        ("title exact unicode", {"title": "Ünïcødé — 日本語 — Ελληνικά — עברית — العربية"}, 2),
        ("note is null", {"note": None}, None),
        ("body empty string", {"body": ""}, None),
        ("n_big max", {"n_big": 9223372036854775807}, 2),
        ("flag false", {"flag": False}, 2),
        ("amount precise", {"amount": Decimal("12345678901234567890.123456789012345678")}, 2),
        ("ref uuid", {"ref": "0b8f2a52-6a3c-4f63-9d0e-7f1c2b3a4d5e"}, 2),
    ]
    for label, where, expect in tests:
        err, sc, text = c.call("find_records", {"database": db, "table": table, "where": where, "limit": 500})
        recs = (sc or {}).get("records", [])
        k, v = next(iter(where.items()))
        allmatch = all((r.get(k) == v) or (v is None and r.get(k) in (None, "")) or
                       (isinstance(v, (int, Decimal)) and r.get(k) is not None and Decimal(str(r.get(k))) == Decimal(str(v))) for r in recs)
        ok = not err and allmatch and (len(recs) == expect if expect is not None else len(recs) > 0)
        record(s, "find_records where %s (expect %s)" % (label, expect if expect is not None else ">0"), ok,
               {"count": len(recs), "ids": [r.get("id") for r in recs], "values": [short(r.get(k)) for r in recs], "error": text if err else None})
    tf = "%s__%s__find" % (db, table)
    if tf in typed_tools:
        err, sc, text = c.call(tf, {"where": {"flag": True}, "order_by": "id", "descending": True})
        recs = (sc or {}).get("records", [])
        record(s, "typed find where flag=true", not err and recs and all(r.get("flag") is True for r in recs), {"ids": [r["id"] for r in recs]})
    searches = [
        ("single word", "fidelity", lambda r: "fidelity" in (r.get("body") or "")),
        ("phrase", '"leading and trailing"', lambda r: "leading and trailing" in r.get("title", "")),
        ("cjk", "日本語", lambda r: "日本語" in r.get("title", "")),
        ("or", "zulu or leap", lambda r: r.get("title") in ("ts zulu", "leap day")),
        ("minus", "json -array", lambda r: "json" in r.get("title", "") and "array" not in r.get("title", "")),
    ]
    for label, q, pred in searches:
        err, sc, text = c.call("search_records", {"database": db, "table": table, "query": q, "limit": 50})
        hits = (sc or {}).get("hits", [])
        ok = not err and hits and all(pred(h["record"]) for h in hits)
        record(s, "search_records %s %r" % (label, q), ok,
               {"count": len(hits), "titles": [short(h["record"].get("title")) for h in hits], "ranks": [h.get("rank") for h in hits], "error": text if err else None})
    ts = "%s__%s__search" % (db, table)
    if ts in typed_tools:
        err, sc, text = c.call(ts, {"query": "decimal"})
        hits = (sc or {}).get("hits", [])
        record(s, "typed search 'decimal'", not err and len(hits) == 8, {"count": len(hits)})


def step6(c, db, table, ids, cid_drop, vals, t0):
    s = 6
    rid = ids.get(("generic", "empty-vs-null"))
    if rid:
        err, sc, text = c.call("record_history", {"database": db, "table": table, "key": {"id": rid}})
        ch = (sc or {}).get("changes", [])
        ops = [x.get("op") for x in ch]
        record(s, "record_history lists insert and update newest first with actor and old/new rows",
               not err and len(ch) >= 2 and ops[0].lower().startswith("u") and ops[-1].lower().startswith("i") and all(x.get("actor", {}).get("username") for x in ch),
               {"ops": ops, "actors": [x.get("actor") for x in ch], "first": ch[0] if ch else None})
        if len(ch) >= 2:
            old = ch[0].get("oldRow", {})
            record(s, "history oldRow of the update holds the original empty string and null",
                   old.get("body") == "" and old.get("note") is None, {"oldRow.body": old.get("body"), "oldRow.note": old.get("note")})
    err, sc, text = c.call("changes_since", {"database": db, "table": table, "since": t0, "limit": 1000})
    ch = (sc or {}).get("changes", [])
    record(s, "changes_since the test start returns this run's changes", not err and len(ch) > 20,
           {"count": len(ch), "ops": sorted(set(x.get("op") for x in ch)), "error": text if err else None})
    deleted = ids.get("deleted")
    if deleted:
        before = None
        err, sc, text = c.call("record_history", {"database": db, "table": table, "key": {"id": deleted}})
        hist = (sc or {}).get("changes", [])
        if hist:
            before = hist[0]["at"]
            at = (datetime.datetime.fromisoformat(before.replace("Z", "+00:00")) - datetime.timedelta(milliseconds=1)).isoformat().replace("+00:00", "Z")
            err, sc, text = c.call("restore_record", {"database": db, "table": table, "key": {"id": deleted}, "at": at})
            err2, sc2, text2 = c.call("get_record", {"database": db, "table": table, "key": {"id": deleted}})
            row = dict(CASES)["emoji-only"]
            diffs, _ = compare(row, (sc2 or {}).get("record", {}), TYPES)
            record(s, "restore_record brings a deleted row back with identical values", not err and not err2 and not diffs,
                   {"restore": sc or text, "diffs": diffs})
    target = ids.get(("generic", "bool-true"))
    c.work_item = "projects-880-t1-undo-job"
    made = []
    for i in range(2):
        err, sc, text = c.call("insert_record", {"database": db, "table": table, "record": {"title": "undo job row %d" % i}})
        if not err:
            made.append(sc["record"]["id"])
    if target:
        c.call("update_record", {"database": db, "table": table, "key": {"id": target}, "changes": {"flag": False, "title": "changed by undo job"}})
    c.work_item = ""
    err, sc, text = c.call("changes_since", {"database": db, "table": table, "since": t0, "limit": 20})
    wi = [x for x in (sc or {}).get("changes", []) if x.get("actor", {}).get("workItem") == "projects-880-t1-undo-job"]
    record(s, "changes made under X-Agentdb-Work-Item carry that work item", len(wi) == 3, {"matching": len(wi), "actors": [x.get("actor") for x in wi]})
    err, sc, text = c.call("undo_changes", {"database": db, "work_item": "projects-880-t1-undo-job"})
    record(s, "undo_changes for the job's work item undoes all three changes together", not err and sc and sc.get("count") == 3, sc or text)
    gone = all(c.call("get_record", {"database": db, "table": table, "key": {"id": x}})[0] for x in made)
    err, sc, text = c.call("get_record", {"database": db, "table": table, "key": {"id": target}})
    rec = (sc or {}).get("record", {})
    record(s, "after undo: job's inserted rows gone, updated row back to original", gone and rec.get("flag") is True and rec.get("title") == "bool true",
           {"inserted_gone": gone, "target": rec})
    if cid_drop:
        err, sc, text = c.call("reverse_structure_change", {"id": cid_drop})
        record(s, "reverse_structure_change without coaching answers is refused", err and "refused" in text, text)
        err, sc, text, rnd, agreed = agree(c, "reverse_structure_change", {"id": cid_drop, **COACH})
        record(s, "reverse_structure_change confirm=false reviews the restore (agreed=%s)" % agreed, not err and sc and not sc.get("applied"), sc or text)
        err, sc, text = c.call("reverse_structure_change", {"id": cid_drop, **COACH, "confirm": True})
        record(s, "reverse_structure_change of drop_column applied", not err and sc and sc.get("applied"), sc or text)
        for rid, v in vals.items():
            err, sc, text = c.call("get_record", {"database": db, "table": table, "key": {"id": rid}})
            rec = (sc or {}).get("record", {})
            record(s, "after reverse id %s: detail value restored exactly (%r)" % (rid, v), "detail" in rec and rec.get("detail") == v, rec)
        err, sc, text = c.call("reverse_structure_change", {"id": cid_drop, **COACH, "confirm": True})
        record(s, "reversing the same change twice is refused", err, text)


def step7(c, db):
    s = 7
    err, sc, text = c.call("list_structure_changes", {"database": db, "limit": 100})
    ch = (sc or {}).get("changes", [])
    ok = not err and ch and all(x.get("actor", {}).get("username") and x.get("purpose") == COACH["purpose"] and
                                x.get("future_use") == COACH["future_use"] and x.get("other_identities") == COACH["other_identities"] for x in ch)
    record(s, "list_structure_changes shows caller and purpose/future_use/other_identities on every change", ok,
           [{"id": x.get("id"), "op": (x.get("operation") or {}).get("kind") or x.get("operation"), "actor": x.get("actor"),
             "purpose": x.get("purpose"), "reversed_by": x.get("reversed_by"), "reverses": x.get("reverses")} for x in ch])
    names = [x.get("actor", {}).get("username") for x in ch]
    record(s, "caller identity is specific (not the shared auth-off placeholder)", any(n and n != "unauthenticated" for n in names), {"usernames": sorted(set(n or "" for n in names))})


def cleanup(c, db, table):
    s = "cleanup"
    err, sc, text = c.call("list_tables", {"database": db})
    for t in (sc or {}).get("tables", []):
        structural(s, c, "drop_table", {"database": db, "table": t.get("table") or t.get("name")}, "drop_table")
    cid = structural(s, c, "drop_database", {"database": db}, "drop_database %s" % db)
    record(s, "drop_database %s" % db, bool(cid), cid)
    err, sc, text = c.call("list_tables", {"database": db})
    record(s, "no tables left in %s" % db, not err and sc and sc.get("tables") == [], sc or text)


def main():
    if not ENDPOINT:
        print("AGENTDB_ENDPOINT not set", file=sys.stderr)
        return 2
    db = "t2_fidelity_%d" % int(time.time())
    table = "items"
    t0 = datetime.datetime.now(datetime.timezone.utc).replace(microsecond=0).isoformat().replace("+00:00", "Z")
    log("start", database=db, table=table, t0=t0)
    c = Client()
    init = c.initialize()
    created = False
    try:
        names = step1(c, init)
        cid_db, cid_tbl, typed = step2(c, db, table)
        created = cid_db is not None
        cid_drop, vals = step3(c, db, table)
        m = c.rpc("tools/list", {}, quiet=True)
        typed = sorted(t["name"] for t in m.get("result", {}).get("tools", []) if t["name"].startswith(db + "__"))
        ids = step4(c, db, table, typed)
        step5(c, db, table, typed)
        step6(c, db, table, ids, cid_drop, vals, t0)
        step7(c, db)
    except Exception as e:
        record("fatal", "unexpected exception", False, repr(e))
    finally:
        if created:
            cleanup(c, db, table)
    by = {}
    for r in RESULTS:
        by.setdefault(str(r["step"]), [0, 0])[0 if r["pass"] else 1] += 1
    nm = [x for x in CALLS if not x["model"]]
    log("timing", calls=len(CALLS), model_calls=len(CALLS) - len(nm), non_model_over_2s=[x for x in nm if x["ms"] >= 2000],
        non_model_max_ms=max([x["ms"] for x in nm] or [0]), model_max_ms=max([x["ms"] for x in CALLS if x["model"]] or [0]))
    log("summary", per_step={k: {"pass": v[0], "fail": v[1]} for k, v in by.items()},
        failures=[{"step": r["step"], "check": r["check"]} for r in RESULTS if not r["pass"]])
    return 0


if __name__ == "__main__":
    sys.exit(main())
