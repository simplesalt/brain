import sys
import time

sys.path.insert(0, "/test")
import mcp_test as t

COLS = [{"name": "title", "type": "text"}, {"name": "n_big", "type": "bigint", "nullable": True},
        {"name": "amount", "type": "numeric", "nullable": True}, {"name": "meta", "type": "jsonb", "nullable": True}]
TYPES = {c["name"]: c["type"] for c in COLS}
GUIDANCE = {"purpose": "Rows that check large numbers and JSON keep their exact values in agentdb.",
            "use": "Insert test rows, read them back, then drop the table.", "rules": []}


def long_session(c, n):
    lat = []
    fails = 0
    for i in range(n):
        a = time.time()
        err, sc, text = c.call("list_tables", {"database": "probe"} if i % 2 else {})
        lat.append(time.time() - a)
        fails += 1 if err else 0
    chunks = [lat[i:i + 50] for i in range(0, len(lat), 50)]
    stats = [{"calls": len(x), "max_s": round(max(x), 3), "avg_s": round(sum(x) / len(x), 3)} for x in chunks]
    t.record("950", "%d calls in one session all answered, latency stays flat" % n,
             fails == 0 and max(s["avg_s"] for s in stats) < 3 * max(stats[0]["avg_s"], 0.05), {"errors": fails, "per_50": stats})


def t951(c, i):
    db = "t2_951_%d_%d" % (int(time.time()), i)
    t.structural("951", c, "create_database", {"database": db}, "create_database %s" % db)
    args = dict(database=db, table="w", columns=COLS, guidance=GUIDANCE, **t.COACH)
    err, sc, text, rnd, agreed = t.agree(c, "create_table", args)
    rv = (sc or {}).get("review") or {}
    rules = ((rv.get("proposed_guidance") or {}).get("rules")) or []
    bad = [r for r in rules if r.get("kind") == "max_bytes" and TYPES.get(r.get("column")) != "text"]
    t.record("951", "try %d: reviewer agrees on the bigint/numeric/jsonb table (rounds %d)" % (i, rnd), agreed and not err, {"rounds": rnd, "rules": rules, "error": text if err else None})
    t.record("951", "try %d: no max_bytes rule on a non-text column" % i, not bad, {"bad": bad})
    if agreed:
        cid = t.structural("951", c, "create_table", {"database": db, "table": "w", "columns": COLS, "guidance": rv.get("proposed_guidance") or GUIDANCE}, "create_table try %d" % i)
        if cid:
            t.structural("951", c, "drop_table", {"database": db, "table": "w"}, "drop_table try %d" % i)
    t.structural("951", c, "drop_database", {"database": db}, "drop_database %s" % db)


def main():
    c = t.Client()
    c.initialize()
    long_session(c, int(sys.argv[1]) if len(sys.argv) > 1 else 200)
    for i in range(1, 5):
        t951(c, i)
    fails = [r for r in t.RESULTS if not r["pass"]]
    t.log("summary", total=len(t.RESULTS), failures=[{"step": r["step"], "check": r["check"]} for r in fails])
    return 0


if __name__ == "__main__":
    sys.exit(main())
