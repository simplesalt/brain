#!/usr/bin/env python3
"""Find duplicate person pages in gbrain and merge the exact ones.

Runs as a no-agent cron job. gbrain has no merge command, so this follows its documented merge protocol:
the more complete page survives; the duplicate's timeline entries, aliases, links, backlinks and raw
data move to it; then the duplicate is deleted.

Grouping: person pages in the main source that share any normalized identifier (email, LinkedIn URL,
LinkedIn id, phone) form a group. A group is exact when no two of its pages carry different LinkedIn URLs
(or LinkedIn ids); anything else is uncertain and goes to the pending-review list in the job's state page
(ops/person-dedupe), never merged.

DRY RUN IS THE DEFAULT. Set PERSON_DEDUPE_LIVE=1 to merge for real. A dry run writes nothing but the
state page, which records the plan (groups, survivor, what would move) for review.

Every merge step is idempotent and ordered so a crashed run resumes: the plan (duplicate -> survivor) is
saved in the state page before the first write, and each step skips what the survivor already has.

Dux Soup keeps a gbrain_slug per profile in its Postgres table. A deleted duplicate leaves that pointing
at a missing page, so the state page also records `redirects` (duplicate slug -> survivor slug); the
ingester must re-resolve a missing gbrain_slug before it is safe to turn live merges on.
"""
import datetime
import hashlib
import json
import os
import re
import sys
import uuid
from importlib import import_module

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
scan = import_module("cron-people-scan")

call = scan.call
all_pages = scan.all_pages

STATE = "ops/person-dedupe"
LIVE = os.environ.get("PERSON_DEDUPE_LIVE") == "1"
MAX_GROUP = int(os.environ.get("PERSON_DEDUPE_MAX_GROUP", "5"))
MAX_MERGES = int(os.environ.get("PERSON_DEDUPE_MAX_MERGES", "10"))
MERGED_KEEP_DAYS = 180
LIST_KEYS = ("emails", "phones", "aliases")
SECTION_SKIP = {"contact", "timeline"}
EMAIL_RE = re.compile(r"^[^\s:@]+@[^\s@]+\.[^\s@]+$")


# ---------------------------------------------------------------- identifiers

def as_list(v):
    if v is None or v == "":
        return []
    return list(v) if isinstance(v, (list, tuple)) else [v]


def norm_url(u):
    s = str(u or "").strip()
    if not re.search(r"linkedin\.com/", s, re.I):
        return None
    if not re.match(r"https?://", s, re.I):
        s = "https://" + s
    s = re.sub(r"^http://", "https://", s, flags=re.I)
    s = re.split(r"[?#]", s, 1)[0].lower().replace("://www.", "://").rstrip("/")
    return s


def norm_phone(p):
    s = str(p or "").strip()
    digits = re.sub(r"\D", "", s)
    if s.startswith("+"):
        out = "+" + digits
    elif len(digits) == 11 and digits.startswith("1"):
        out = "+" + digits
    else:
        return None
    return out if 8 <= len(out) - 1 <= 15 else None


def identifiers(fm):
    """The normalized identifiers a page's frontmatter holds, by kind."""
    aliases = [str(a).strip() for a in as_list(fm.get("aliases"))]
    emails = {e.strip().lower() for e in as_list(fm.get("emails")) + as_list(fm.get("email"))
              if EMAIL_RE.match(str(e).strip())}
    emails |= {a.lower() for a in aliases if EMAIL_RE.match(a)}
    urls = {u for u in (norm_url(x) for x in as_list(fm.get("linkedin")) + aliases) if u}
    lids = {str(x).strip() for x in as_list(fm.get("linkedin_id")) if str(x).strip()}
    lids |= {a.split(":", 1)[1].strip() for a in aliases if a.lower().startswith("linkedin-id:")}
    phones = {p for p in (norm_phone(x) for x in as_list(fm.get("phones"))) if p}
    return {"email": emails, "url": urls, "lid": lids, "phone": phones}


def keys_of(ids):
    return {(k, v) for k, vs in ids.items() for v in vs}


# ------------------------------------------------------------------- gbrain

def get_full(slug):
    try:
        return call("get_page", {"slug": slug, "include_content": True})
    except RuntimeError as e:
        if "not_found" in str(e):
            return None
        raise


def rid(*parts):
    return str(uuid.uuid5(uuid.NAMESPACE_URL, "person-dedupe|" + "|".join(str(p) for p in parts)))


def tl_key(e):
    return ((e.get("date") or "")[:10], (e.get("summary") or "").strip())


def link_key(l):
    return (l.get("link_type") or "", l.get("context") or "")


def timeline_of(slug):
    r = call("get_timeline", {"slug": slug})
    return r if isinstance(r, list) else []


def links_of(tool, slug):
    r = call(tool, {"slug": slug})
    return r if isinstance(r, list) else []


def load_page(slug):
    p = get_full(slug)
    if not p:
        return None
    return {"slug": slug, "page": p, "fm": p.get("frontmatter") or {},
            "timeline": timeline_of(slug), "backlinks": links_of("get_backlinks", slug),
            "links": links_of("get_links", slug)}


def score(pg):
    created = pg["page"].get("created_at") or ""
    # more complete first; on a tie the older page; then slug for stability
    return (-len(pg["timeline"]), -len((pg["page"].get("compiled_truth") or "").strip()),
            -(len(pg["links"]) + len(pg["backlinks"])), created, pg["slug"])


# ----------------------------------------------------------------- grouping

def group_pages(pages):
    """pages: list of {slug, ids}. Returns groups (lists of slugs, 2+ pages) with the shared keys."""
    parent = {p["slug"]: p["slug"] for p in pages}

    def find(x):
        while parent[x] != x:
            parent[x] = parent[parent[x]]
            x = parent[x]
        return x

    owners = {}
    for p in pages:
        for k in keys_of(p["ids"]):
            owners.setdefault(k, []).append(p["slug"])
    for slugs in owners.values():
        for s in slugs[1:]:
            parent[find(s)] = find(slugs[0])
    groups = {}
    for p in pages:
        groups.setdefault(find(p["slug"]), []).append(p["slug"])
    out = []
    for slugs in groups.values():
        if len(slugs) < 2:
            continue
        shared = sorted(f"{k[0]}:{k[1]}" for k, o in owners.items() if sum(1 for s in o if s in slugs) > 1)
        out.append({"slugs": sorted(slugs), "shared": shared})
    return sorted(out, key=lambda g: g["slugs"])


def classify(group, ids_by_slug):
    """("exact", None) or ("uncertain", reason)."""
    slugs = group["slugs"]
    if len(slugs) > MAX_GROUP:
        return "uncertain", f"group of {len(slugs)} pages is larger than {MAX_GROUP}"
    for kind, label in (("url", "different LinkedIn URLs"), ("lid", "different LinkedIn ids")):
        for i, a in enumerate(slugs):
            for b in slugs[i + 1:]:
                x, y = ids_by_slug[a][kind], ids_by_slug[b][kind]
                if x and y and not (x & y):
                    return "uncertain", f"{a} and {b} carry {label}"
    return "exact", None


def pick_survivor(pgs):
    ordered = sorted(pgs, key=score)
    return ordered[0], ordered[1:]


# -------------------------------------------------------------------- merge

def split_sections(text):
    """[(heading, full_text)] for each '## ' section of a page body."""
    out, cur, head = [], [], None
    for line in (text or "").split("\n"):
        if line.startswith("## "):
            if head is not None:
                out.append((head, "\n".join(cur).rstrip()))
            head, cur = line[3:].strip(), [line]
        elif head is not None:
            cur.append(line)
    if head is not None:
        out.append((head, "\n".join(cur).rstrip()))
    return out


def merged_frontmatter(sv, dp):
    """Survivor frontmatter plus the duplicate's: lists unioned, empty scalars filled."""
    fm = dict(sv)
    changes = []
    for key in LIST_KEYS:
        have = as_list(sv.get(key))
        norm = {str(x).strip().lower() for x in have}
        add = []
        for x in as_list(dp.get(key)):
            if str(x).strip().lower() not in norm:
                norm.add(str(x).strip().lower())
                add.append(x)
        if add:
            fm[key] = have + add
            changes.append(f"{key}+{len(add)}")
    owned = as_list(sv.get("duxsoup_fields"))
    dp_owned = {str(x) for x in as_list(dp.get("duxsoup_fields"))}
    for key, val in dp.items():
        if key in LIST_KEYS or key == "duxsoup_fields":
            continue
        if sv.get(key) in (None, "", [], {}) and val not in (None, "", [], {}):
            fm[key] = val
            changes.append(f"fill {key}")
            if key in dp_owned and key not in [str(x) for x in owned]:
                owned = owned + [key]
    if owned != as_list(sv.get("duxsoup_fields")):
        fm["duxsoup_fields"] = owned
    return fm, changes


def unique_tail(text, end, start):
    """The shortest run of whole lines ending at `end` (not before `start`) that occurs once in text."""
    a = end
    while a > start:
        nl = text.rfind("\n", start, a - 1)
        a = nl + 1 if nl >= 0 else start
        if text.count(text[a:end]) == 1:
            return a
    return None


def merge_edits(sv_page, fm, extra_sections):
    content = sv_page.get("content") or ""
    m = re.match(r"---\n.*?\n---\n", content, re.S)
    if not m:
        raise RuntimeError(f"{sv_page.get('slug')}: no frontmatter to merge into")
    edits = []
    view = content
    if fm != (sv_page.get("frontmatter") or {}):
        head = "---\n" + "\n".join(f"{k}: {json.dumps(v, ensure_ascii=False)}" for k, v in fm.items()) + "\n---\n"
        edits.append({"old_text": m.group(0), "new_text": head})
        view = head + content[m.end():]
    if extra_sections:
        start = len(view) - len(content) + m.end()
        body = view[start:]
        truth = (sv_page.get("compiled_truth") or "").rstrip()
        at = body.find(truth) + len(truth) if truth and truth in body else -1
        if at < 0:
            i = body.find("\n## Timeline")
            at = i if i >= 0 else len(body.rstrip())
        end = start + len(body[:at].rstrip("\n"))
        a = unique_tail(view, end, start) if end > start else None
        if a is None:
            raise RuntimeError(f"{sv_page.get('slug')}: no unique line to add sections after")
        edits.append({"old_text": view[a:end], "new_text": view[a:end] + "\n\n" + "\n\n".join(extra_sections)})
    return edits


def plan_merge(sv, dp):
    """What would move from duplicate dp to survivor sv; pure function of the two loaded pages."""
    have_tl = {tl_key(e) for e in sv["timeline"]}
    tl = [e for e in dp["timeline"] if tl_key(e) not in have_tl]
    fm, fm_changes = merged_frontmatter(sv["fm"], dp["fm"])
    sv_heads = {h.lower() for h, _ in split_sections(sv["page"].get("compiled_truth"))}
    sections = [t for h, t in split_sections(dp["page"].get("compiled_truth"))
                if h.lower() not in sv_heads and h.lower() not in SECTION_SKIP and "duxsoup:contact" not in t]
    sv_in = {(l["from_slug"],) + link_key(l) for l in sv["backlinks"]}
    back = [l for l in dp["backlinks"]
            if l["from_slug"] not in (sv["slug"], dp["slug"]) and (l["from_slug"],) + link_key(l) not in sv_in]
    sv_out = {(l["to_slug"],) + link_key(l) for l in sv["links"]}
    out = [l for l in dp["links"]
           if l["to_slug"] not in (sv["slug"], dp["slug"]) and (l["to_slug"],) + link_key(l) not in sv_out]
    return {"timeline": tl, "frontmatter": fm, "frontmatter_changes": fm_changes, "sections": sections,
            "backlinks": back, "links": out}


def raw_to_copy(sv_slug, dp_slug):
    have = {r.get("source") for r in as_list(call("get_raw_data", {"slug": sv_slug}))}
    rows = as_list(call("get_raw_data", {"slug": dp_slug}))
    return [r for r in rows if isinstance(r, dict) and r.get("source") not in have]


def link_args(l, frm, to):
    a = {"from": frm, "to": to}
    if l.get("link_type"):
        a["link_type"] = l["link_type"]
    if l.get("context"):
        a["context"] = l["context"]
    for k in ("valid_from", "valid_until"):
        if l.get(k):
            a[k] = str(l[k])[:10]
    return a


def execute_merge(sv_slug, dp_slug):
    """Merge dp_slug into sv_slug. Idempotent; safe to run again after a crash at any step."""
    sv, dp = load_page(sv_slug), load_page(dp_slug)
    if not sv:
        raise RuntimeError(f"survivor {sv_slug} is missing; leaving {dp_slug} alone")
    if not dp:
        return "already merged"
    plan = plan_merge(sv, dp)
    for r in raw_to_copy(sv_slug, dp_slug):
        call("put_raw_data", {"slug": sv_slug, "source": r["source"], "data": r.get("data") or {}})
    for e in plan["timeline"]:
        args = {"slug": sv_slug, "date": (e.get("date") or "")[:10], "summary": e.get("summary") or ""}
        for k in ("detail", "source"):
            if e.get(k):
                args[k] = e[k]
        call("add_timeline_entry", args)
    for l in plan["backlinks"]:
        call("add_link", link_args(l, l["from_slug"], sv_slug))
    for l in plan["links"]:
        call("add_link", link_args(l, sv_slug, l["to_slug"]))
    sv = get_full(sv_slug)  # revision moved with the timeline writes
    fm, _ = merged_frontmatter(sv.get("frontmatter") or {}, dp["fm"])
    sv_heads = {h.lower() for h, _ in split_sections(sv.get("compiled_truth"))}
    extra = [t for t in plan["sections"] if split_sections(t)[0][0].lower() not in sv_heads]
    edits = merge_edits(sv, fm, extra)
    if edits:
        call("edit_page", {"slug": sv_slug, "expected_revision": sv["revision"], "edits": edits,
                           "request_id": rid("edit", sv_slug, sv["revision"],
                                             hashlib.sha1(json.dumps(edits).encode()).hexdigest())})
    dp = get_full(dp_slug)
    if dp:
        call("delete_page", {"slug": dp_slug, "expected_revision": dp["revision"],
                             "request_id": rid("delete", dp_slug, dp["revision"])})
    return "merged"


# -------------------------------------------------------------------- state

def load_state():
    empty = {"pending_review": [], "plan": [], "inflight": {}, "merged": [], "redirects": {}}
    try:
        page = call("get_page", {"slug": STATE})
    except RuntimeError:
        return empty
    body = scan.state_body(page, STATE)
    m = re.search(r"```json\n(.*?)\n```", body, re.S)
    return dict(empty, **json.loads(m.group(1))) if m else empty


def save_state(state):
    content = ("---\ntype: note\ntitle: Person dedupe state\n---\n\n# Person dedupe state\n\n"
               "Duplicate person pages: the merge plan, pending review and merge history. Kept by "
               "cron-person-dedupe.py.\n\n```json\n" + json.dumps(state, indent=1, sort_keys=True) + "\n```\n")
    scan.check_cap(STATE, content)
    args = {"slug": STATE, "content": content}
    try:
        cur = call("get_page", {"slug": STATE, "include_content": True})
        scan.state_body(cur, STATE)
        if cur.get("revision"):
            args["expected_revision"] = cur["revision"]
    except RuntimeError:
        pass
    scan.put_state_page(args)


# --------------------------------------------------------------------- main

def describe(plan):
    return {"timeline_entries": len(plan["timeline"]), "frontmatter": plan["frontmatter_changes"],
            "sections": [split_sections(t)[0][0] for t in plan["sections"]],
            "backlinks_repointed": len(plan["backlinks"]), "links_copied": len(plan["links"])}


def run(live=None):
    live = LIVE if live is None else live
    state = load_state()
    log = []
    # Finish merges a crashed run left half done, with the survivor it already chose.
    for dp_slug, sv_slug in list(state["inflight"].items()):
        if not live:
            continue
        try:
            execute_merge(sv_slug, dp_slug)
            state["merged"].append({"duplicate": dp_slug, "survivor": sv_slug, "at": now()})
            state["redirects"][dp_slug] = sv_slug
            state["inflight"].pop(dp_slug)
            save_state(state)
            log.append(f"resumed merge of {dp_slug} into {sv_slug}")
        except Exception as e:
            print(f"person-dedupe: could not resume {dp_slug}: {e}", file=sys.stderr)

    pages = []
    for p in all_pages(type="person"):
        full = get_full(p["slug"])
        if full:
            pages.append({"slug": p["slug"], "ids": identifiers(full.get("frontmatter") or {})})
    ids_by_slug = {p["slug"]: p["ids"] for p in pages}
    pending, plans, merges = [], [], 0
    seen = {r["key"]: r for r in state["pending_review"]}
    for g in group_pages(pages):
        verdict, reason = classify(g, ids_by_slug)
        if verdict == "uncertain":
            key = "|".join(g["slugs"])
            pending.append({"key": key, "slugs": g["slugs"], "shared": g["shared"], "reason": reason,
                            "first_seen": seen.get(key, {}).get("first_seen", now())})
            continue
        loaded = [load_page(s) for s in g["slugs"]]
        sv, dups = pick_survivor([p for p in loaded if p])
        for dp in dups:
            chosen = state["inflight"].get(dp["slug"])
            if chosen and chosen != sv["slug"] and any(p["slug"] == chosen for p in loaded if p):
                sv = next(p for p in loaded if p and p["slug"] == chosen)
            plans.append({"survivor": sv["slug"], "duplicate": dp["slug"], "shared": g["shared"],
                          "moves": describe(plan_merge(sv, dp))})
            if not live or merges >= MAX_MERGES:
                continue
            state["inflight"][dp["slug"]] = sv["slug"]
            save_state(dict(state, pending_review=pending))
            try:
                execute_merge(sv["slug"], dp["slug"])
            except Exception as e:
                print(f"person-dedupe: merge of {dp['slug']} into {sv['slug']} failed: {e}", file=sys.stderr)
                continue
            merges += 1
            state["inflight"].pop(dp["slug"], None)
            state["merged"].append({"duplicate": dp["slug"], "survivor": sv["slug"], "at": now()})
            state["redirects"][dp["slug"]] = sv["slug"]
            log.append(f"merged {dp['slug']} into {sv['slug']}")
    keep = (datetime.datetime.now(datetime.timezone.utc) - datetime.timedelta(days=MERGED_KEEP_DAYS)).strftime("%Y-%m-%d")
    state["merged"] = [e for e in state["merged"] if (e.get("at") or "")[:10] >= keep]
    state["pending_review"] = pending
    state["plan"] = plans
    state["mode"] = "live" if live else "dry-run"
    state["last_run"] = now()
    save_state(state)
    if not live and plans:
        log.append(f"dry run: {len(plans)} exact duplicate(s) would be merged")
    if pending:
        log.append(f"{len(pending)} uncertain group(s) waiting for review")
    return log, state


def now():
    return datetime.datetime.now(datetime.timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def main():
    log, _ = run()
    if log:
        print("\n".join(log))


if __name__ == "__main__":
    try:
        main()
    except scan.StateError as e:
        print(f"person-dedupe: FAILED {e}", file=sys.stderr)
        sys.exit(1)
