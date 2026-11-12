import datetime
import sys
import time
from decimal import Decimal

sys.path.insert(0, "/test")
import mcp_test as t

OLD_DB = "t1_fidelity_1794506834"


def now():
    return datetime.datetime.now(datetime.timezone.utc).isoformat().replace("+00:00", "Z")


def drop_db(c, db, step):
    err, sc, text = c.call("list_tables", {"database": db})
    for tb in (sc or {}).get("tables", []):
        e, s2, tx = c.call("drop_table", {"database": db, "table": tb["table"], **t.COACH, "confirm": True})
        t.record(step, "drop_table %s.%s" % (db, tb["table"]), not e and s2 and s2.get("applied"), s2 or tx)
    err, sc, text = c.call("drop_database", {"database": db, **t.COACH, "confirm": True})
    t.record(step, "drop_database %s" % db, not err and sc and sc.get("applied"), sc or text)
    err, sc, text = c.call("list_structure_changes", {"database": db, "limit": 3})
    t.record(step, "drop of %s recorded with purpose" % db, not err and sc and sc["changes"] and sc["changes"][0]["purpose"] == t.COACH["purpose"],
             [{"id": x["id"], "op": x["operation"].get("kind"), "actor": x["actor"]} for x in (sc or {}).get("changes", [])])
    err, sc, text = c.call("list_tables", {})
    left = [x for x in (sc or {}).get("tables", []) if x["database"] == db]
    t.record(step, "no tables left in %s" % db, not err and not left, [x["database"] + "." + x["table"] for x in (sc or {}).get("tables", [])])


def main():
    db = "t1_followup_%d" % int(time.time())
    t.log("start", database=db, old_database=OLD_DB)
    c = t.Client()
    init = c.initialize()
    r = init.get("result", {})
    t.log("guidance", instructions=r.get("instructions"), capabilities=r.get("capabilities"), serverInfo=r.get("serverInfo"))
    t.record(1, "initialize returns server instructions", bool(r.get("instructions")), {"instructions": r.get("instructions")})

    drop_db(c, OLD_DB, "cleanup-run1")

    t.structural(2, c, "create_database", {"database": db}, "create_database")
    cols = [{"name": "title", "type": "text"}, {"name": "n_big", "type": "bigint", "nullable": True},
            {"name": "amount", "type": "numeric", "nullable": True}]
    t.structural(2, c, "create_table", {"database": db, "table": "w", "columns": cols}, "create_table")
    err, sc, text = c.call("create_table", {"database": db, "table": "w2", "columns": cols, "purpose": t.COACH["purpose"], "confirm": True})
    t.record(2, "create_table with only purpose and confirm=true is refused (schema requires the coaching answers)", err, text)

    err, sc, text = c.call("rename_column", {"database": db, "table": "w", "from": "id", "to": "pk", **t.COACH, "confirm": True})
    t.record(3, "rename of the primary key column with confirm=true is refused", err, sc or text)

    err, sc, text = c.call("insert_record", {"database": db, "table": "w", "record": {"title": "generic", "n_big": 1}})
    gid = sc["record"]["id"]
    err, sc, text = c.call("update_record", {"database": db, "table": "w", "key": {"id": gid},
                                             "changes": {"n_big": 9007199254740993, "amount": Decimal("0.12345678901234567890123")}})
    err, sc2, text2 = c.call("get_record", {"database": db, "table": "w", "key": {"id": gid}})
    rec = (sc2 or {}).get("record", {})
    t.record(4, "generic update_record keeps bigint 2^53+1 and a 23-digit decimal exactly",
             rec.get("n_big") == 9007199254740993 and Decimal(str(rec.get("amount"))) == Decimal("0.12345678901234567890123"), rec)
    err, sc, text = c.call("%s__w__write" % db, {"operation": "insert", "record": {"title": "typed max", "n_big": 9223372036854775807}})
    err, sc, text = c.call("%s__w__find" % db, {"where": {"n_big": 9223372036854775807}})
    recs = (sc or {}).get("records", [])
    t.record(5, "typed find where n_big = 2^63-1 finds the row", len(recs) == 1, recs or text)

    a = t.Client()
    a.work_item = "wi-from-initialize"
    a.initialize()
    a.call("insert_record", {"database": db, "table": "w", "record": {"title": "session A row"}})
    b = t.Client()
    b.initialize()
    b.work_item = "wi-on-tool-call-only"
    b.call("insert_record", {"database": db, "table": "w", "record": {"title": "session B row"}})
    err, sc, text = c.call("changes_since", {"database": db, "table": "w", "since": "2000-01-01T00:00:00Z"})
    actors = {x["newRow"]["title"]: x["actor"] for x in (sc or {}).get("changes", []) if x.get("newRow") and x["op"] == "insert"}
    t.record(6, "work item header sent on initialize and every call is recorded", actors.get("session A row", {}).get("workItem") == "wi-from-initialize", actors)
    t.record(6, "work item header sent only on tool calls (after initialize) is recorded", actors.get("session B row", {}).get("workItem") == "wi-on-tool-call-only", actors)
    err, sc, text = c.call("undo_changes", {"database": db, "work_item": "wi-from-initialize"})
    t.record(6, "undo_changes by work item from initialize", not err and sc and sc.get("count") == 1, sc or text)

    time.sleep(2)
    since = now()
    time.sleep(1)
    made = []
    for i in range(2):
        e, s, tx = c.call("insert_record", {"database": db, "table": "w", "record": {"title": "undo batch %d" % i}})
        made.append(s["record"]["id"])
    c.call("update_record", {"database": db, "table": "w", "key": {"id": gid}, "changes": {"title": "changed in batch"}})
    err, sc, text = c.call("undo_changes", {"database": db, "username": "unauthenticated", "since": since})
    t.record(6, "undo_changes by caller since a time undoes the batch of 3 together", not err and sc and sc.get("count") == 3, {"since": since, "result": sc or text})
    gone = all(c.call("get_record", {"database": db, "table": "w", "key": {"id": x}})[0] for x in made)
    err, sc, text = c.call("get_record", {"database": db, "table": "w", "key": {"id": gid}})
    t.record(6, "after undo: batch inserts gone and the updated row is back", gone and (sc or {}).get("record", {}).get("title") == "generic",
             {"inserts_gone": gone, "row": (sc or {}).get("record")})

    drop_db(c, db, "cleanup-run2")
    err, sc, text = c.call("list_tables", {"database": "probe"})
    t.record("cleanup", "probe.notes untouched", bool((sc or {}).get("tables")), [x["table"] for x in (sc or {}).get("tables", [])])

    fails = [r for r in t.RESULTS if not r["pass"]]
    t.log("summary", total=len(t.RESULTS), failures=[{"step": r["step"], "check": r["check"]} for r in fails])
    return 0


if __name__ == "__main__":
    sys.exit(main())
