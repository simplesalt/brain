import sys
import time
from decimal import Decimal

sys.path.insert(0, "/test")
import mcp_test as t

BIG = 9007199254740993
PRECISE = Decimal("12345678901234567890.123456789012345678")
TRAILING = Decimal("10.500")
IMAX = 9223372036854775807
IMIN = -9223372036854775808
JSON_BIG = 123456789012345678901234567890


def main():
    db = "t2_927_928_%d" % int(time.time())
    t.log("start", database=db)
    c = t.Client()
    init = c.initialize()
    t.log("server", serverInfo=init.get("result", {}).get("serverInfo"))
    t.structural("setup", c, "create_database", {"database": db}, "create_database")
    cols = [{"name": "title", "type": "text"}, {"name": "n_big", "type": "bigint", "nullable": True},
            {"name": "amount", "type": "numeric", "nullable": True}, {"name": "meta", "type": "jsonb", "nullable": True}]
    t.structural("setup", c, "create_table", {"database": db, "table": "w", "columns": cols, "guidance": {"purpose": "Rows that check large numbers and work-item capture in agentdb's general record tools.", "use": "Insert test rows, read them back, then drop the table.", "rules": []}}, "create_table")

    err, sc, text = c.call("insert_record", {"database": db, "table": "w", "record": {"title": "big", "n_big": BIG}})
    rid = (sc or {}).get("record", {}).get("id")
    err, sc, text = c.call("get_record", {"database": db, "table": "w", "key": {"id": rid}})
    got = (sc or {}).get("record", {}).get("n_big")
    t.record("927", "insert_record 9007199254740993 reads back exactly", got == BIG, {"read": got})

    for label, val in (("precise decimal", PRECISE), ("trailing zeros 10.500", TRAILING)):
        err, sc, text = c.call("insert_record", {"database": db, "table": "w", "record": {"title": label, "amount": val}})
        rid = (sc or {}).get("record", {}).get("id")
        err, sc, text = c.call("get_record", {"database": db, "table": "w", "key": {"id": rid}})
        got = (sc or {}).get("record", {}).get("amount")
        t.record("927", "insert_record %s reads back unchanged" % label, str(got) == str(val), {"read": str(got)})

    for label, val in (("bigint max", IMAX), ("bigint min", IMIN)):
        err, sc, text = c.call("insert_record", {"database": db, "table": "w", "record": {"title": label, "n_big": val}})
        t.record("927", "insert_record accepts %s" % label, not err and (sc or {}).get("record", {}).get("n_big") == val, sc or text)
        err, sc, text = c.call("find_records", {"database": db, "table": "w", "where": {"n_big": val}})
        recs = (sc or {}).get("records", [])
        t.record("927", "find_records finds %s" % label, len(recs) == 1 and recs[0].get("n_big") == val, recs or text)

    err, sc, text = c.call("insert_record", {"database": db, "table": "w", "record": {"title": "json big", "meta": {"n": JSON_BIG, "x": BIG}}})
    rid = (sc or {}).get("record", {}).get("id")
    err, sc, text = c.call("get_record", {"database": db, "table": "w", "key": {"id": rid}})
    meta = (sc or {}).get("record", {}).get("meta") or {}
    t.record("927", "large integers inside jsonb are not rounded", meta.get("n") == JSON_BIG and meta.get("x") == BIG, meta)

    b = t.Client()
    b.initialize()
    b.call("insert_record", {"database": db, "table": "w", "record": {"title": "before header"}})
    b.work_item = "wi-928-late"
    made = []
    for i in range(2):
        e, s, tx = b.call("insert_record", {"database": db, "table": "w", "record": {"title": "late header %d" % i}})
        made.append((s or {}).get("record", {}).get("id"))
    err, sc, text = c.call("changes_since", {"database": db, "table": "w", "since": "2000-01-01T00:00:00Z"})
    actors = {x["newRow"]["title"]: x["actor"] for x in (sc or {}).get("changes", []) if x.get("newRow") and x["op"] == "insert"}
    t.record("928", "header sent only on later write calls is recorded on those changes",
             all((actors.get("late header %d" % i) or {}).get("workItem") == "wi-928-late" for i in range(2)), actors)
    err, sc, text = c.call("undo_changes", {"database": db, "work_item": "wi-928-late"})
    t.record("928", "undo_changes for that work item undoes all of them", not err and sc and sc.get("count") == 2, sc or text)
    gone = all(c.call("get_record", {"database": db, "table": "w", "key": {"id": x}})[0] for x in made)
    t.record("928", "after undo the late-header rows are gone", gone, {"ids": made})

    err, sc, text = c.call("list_tables", {"database": db})
    for tb in (sc or {}).get("tables", []):
        t.structural("cleanup", c, "drop_table", {"database": db, "table": tb["table"]}, "drop_table")
    cid = t.structural("cleanup", c, "drop_database", {"database": db}, "drop_database %s" % db)
    t.record("cleanup", "drop_database %s" % db, bool(cid), cid)

    fails = [r for r in t.RESULTS if not r["pass"]]
    t.log("summary", total=len(t.RESULTS), failures=[{"step": r["step"], "check": r["check"]} for r in fails])
    return 0


if __name__ == "__main__":
    sys.exit(main())
