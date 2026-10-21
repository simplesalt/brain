#!/usr/bin/env python3
"""Replay real Hermes requests against Hindsight recall and print what each memory setting would inject.

Each case names a saved Hermes chat (Hindsight document id) and the turn to test. The script rebuilds
the query the plugin would send for that turn under each variant and writes every recalled memory, with
its scores, to a JSONL file for scoring. It only reads from Hindsight; nothing is saved or changed.

usage: hermes-memory-eval.py <out.jsonl> [--variants lagged,current,floor]
env:   HINDSIGHT_API_URL (default http://hindsight.ssint-main-ai:8888), HERMES_BANK (default hermes)
"""
import json
import os
import re
import sys
import urllib.request

API = os.environ.get("HINDSIGHT_API_URL", "http://hindsight.ssint-main-ai:8888")
BANK = os.environ.get("HERMES_BANK", "hermes")

CASES = [
    ("brian-intro", "api-c4b8830069466bac", 0),
    ("caption-previous-look", "api-a519a481591aa6d2", 1),
    ("caption-descript", "api-a519a481591aa6d2", 2),
    ("caption-template", "api-a519a481591aa6d2", 5),
    ("conference-contacts", "api-6f854d75a69f38fc", 0),
    ("conference-cloudflare", "api-6f854d75a69f38fc", 1),
    ("conference-notion-task", "api-6f854d75a69f38fc", 2),
    ("video-hooks", "api-61a9a078dbcf47ad", 1),
    ("founder-vendors", "api-635f37871862ac2f", 2),
    ("russ-email-check", "api-c21948372476ea82", 1),
    ("clayton-cancel", "api-00cc147f752fc3d5", 0),
    ("priority-field", "api-d97212922fe3b1a6", 1),
]

BASE = {"max_tokens": 250, "budget": "low", "types": ["observation"], "max_input_chars": 300}
VARIANTS = {
    "lagged": dict(BASE, query_from="previous", floor=None),
    "current": dict(BASE, query_from="current", floor=None),
    "floor": dict(BASE, query_from="current", floor={"reranker": 0.01}),
    "world": dict(BASE, query_from="current", floor=None, types=["world"]),
    "world-mid": dict(BASE, query_from="current", floor=None, types=["world", "observation"], budget="mid"),
}


def call(method, path, body=None):
    req = urllib.request.Request(API + path, method=method, headers={"Content-Type": "application/json"},
                                 data=json.dumps(body).encode() if body is not None else None)
    with urllib.request.urlopen(req, timeout=60) as r:
        return json.load(r)


def user_turns(doc_id):
    doc = call("GET", f"/v1/default/banks/{BANK}/documents/{doc_id}")
    data = json.loads(doc["original_text"])
    turns = []

    def walk(x):
        if isinstance(x, list):
            for y in x:
                walk(y)
        elif isinstance(x, dict) and x.get("role") == "user":
            text = re.sub(r"<memory-context>.*?(</memory-context>|$)", "", x["content"], flags=re.S)
            turns.append((x.get("timestamp", ""), re.sub(r"^User: ", "", text.strip())))

    walk(data)
    return turns


def learned_at(r):
    return r.get("mentioned_at") or r.get("occurred_start") or ""


def recall(query, v, before):
    """Recall as the plugin would, keeping only memories that existed before the replayed turn."""
    body = {"query": query[: v["max_input_chars"]], "max_tokens": v["max_tokens"], "budget": v["budget"],
            "types": v["types"]}
    results = call("POST", f"/v1/default/banks/{BANK}/memories/recall", body).get("results", [])
    results = [r for r in results if learned_at(r) and learned_at(r)[:19] < before[:19]]
    if v["floor"]:
        results = [r for r in results
                   if all((r.get("scores") or {}).get(k, -1) >= f for k, f in v["floor"].items())]
    return results


def main():
    out = sys.argv[1]
    names = sys.argv[3].split(",") if len(sys.argv) > 3 and sys.argv[2] == "--variants" else list(VARIANTS)
    with open(out, "w") as f:
        for case, doc_id, turn in CASES:
            turns = user_turns(doc_id)
            sent_at, current = turns[turn]
            previous = turns[turn - 1][1] if turn > 0 else None
            for name in names:
                v = VARIANTS[name]
                query = previous if v["query_from"] == "previous" else current
                results = recall(query, v, sent_at) if query else []
                f.write(json.dumps({
                    "case": case, "variant": name, "sent_at": sent_at, "message": current, "query": query,
                    "memories": [{"text": r["text"], "learned_at": learned_at(r), "scores": r.get("scores")}
                                 for r in results],
                }) + "\n")
                print(f"{case:24} {name:8} {len(results):3} memories")


if __name__ == "__main__":
    main()
