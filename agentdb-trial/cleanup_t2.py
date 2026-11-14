import sys
import time

sys.path.insert(0, "/test")
import mcp_test as t


def main():
    c = t.Client()
    for _ in range(24):
        try:
            init = c.initialize()
        except OSError as e:
            t.log("waiting_for_mcp", error=repr(e))
            init = None
        if "result" in (init or {}):
            break
        time.sleep(5)
    names = set(a for a in sys.argv[1:])
    err, sc, text = c.call("list_tables", {})
    for tb in (sc or {}).get("tables", []):
        if str(tb.get("database", "")).startswith("t2_"):
            names.add(tb["database"])
    for db in sorted(names):
        if not db.startswith("t2_"):
            continue
        err, sc, text = c.call("list_tables", {"database": db})
        for tb in (sc or {}).get("tables", []):
            t.structural("cleanup", c, "drop_table", {"database": db, "table": tb.get("table") or tb.get("name")}, "drop_table %s.%s" % (db, tb.get("table")))
        cid = t.structural("cleanup", c, "drop_database", {"database": db}, "drop_database %s" % db)
        t.record("cleanup", "drop_database %s" % db, bool(cid), cid)
    err, sc, text = c.call("list_tables", {})
    left = sorted(set(tb.get("database") for tb in (sc or {}).get("tables", [])))
    t.log("databases_with_tables_after_cleanup", databases=left)
    return 0


if __name__ == "__main__":
    sys.exit(main())
