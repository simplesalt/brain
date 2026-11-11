#!/usr/bin/env python3
"""Add each synced email thread and meeting to the timeline of the people in it.

Runs as a no-agent cron job, with no model involved. For every Google source in gbrain (found with
sources_list, never by a fixed id) it reads the email thread and meeting pages, and for each new or
changed one adds a timeline entry to every person page in the main source whose aliases or emails
exactly match a participant address. Dylan's own and automated addresses (the people-scan filters)
are skipped; so are addresses that match no person page or more than one. Each entry is dated by the
message or meeting, summarised by its subject or title, and sourced to the thread or meeting slug.
Done pages are kept on the gbrain page ops/timeline-sweep with the page's updated time, so a page is
reworked only when it changes, and entries whose source is already on the person's timeline are never
added again. Work per run is bounded. Prints one line per skipped address or added entry, nothing
when idle.
"""
import datetime
import os
import re
import sys
import json

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from importlib import import_module

scan = import_module("cron-people-scan")

STATE = "ops/timeline-sweep"
MAIN_SOURCE = "default"
MAX_PAGES = int(os.environ.get("TIMELINE_SWEEP_MAX_PAGES", "30"))
DRY_RUN = os.environ.get("TIMELINE_SWEEP_DRY_RUN") == "1"
DATE = re.compile(r"^\d{4}-\d{2}-\d{2}$")
SKIP_LABELS = scan.SKIP_LABELS


def google_sources():
    out = scan.call("sources_list", {})
    items = out.get("sources", []) if isinstance(out, dict) else out
    return [s["id"] for s in items if isinstance(s, dict) and s.get("kind") == "google" and s.get("id")]


def fm_list(value):
    if isinstance(value, str):
        return [value]
    return [v for v in (value or []) if isinstance(v, str)]


def person_index():
    """address -> list of person slugs, from aliases, emails and email in the main source."""
    index = {}
    for page in scan.all_pages(type="person", source_id=MAIN_SOURCE):
        fm = page.get("frontmatter")
        if fm is None:
            fm = (scan.call("get_page", {"slug": page["slug"]}) or {}).get("frontmatter") or {}
        for key in ("aliases", "emails", "email"):
            for a in fm_list(fm.get(key)):
                a = a.strip().lower()
                if "@" in a and page["slug"] not in index.setdefault(a, []):
                    index[a].append(page["slug"])
    return index


def load_state():
    try:
        page = scan.call("get_page", {"slug": STATE})
    except RuntimeError:
        return {}
    body = (page.get("compiled_truth") or "") if isinstance(page, dict) else ""
    m = re.search(r"```json\n(.*?)\n```", body, re.S)
    data = json.loads(m.group(1)) if m else {}
    return data if isinstance(data, dict) else {}


def save_state(done):
    content = ("---\ntype: note\ntitle: Timeline sweep state\n---\n\n# Timeline sweep state\n\n"
               "Emails and meetings the timeline-sweep job has added to people's timelines, with the page's "
               "updated time when it did. Kept by cron-timeline-sweep.py.\n\n"
               "```json\n" + json.dumps(done, sort_keys=True) + "\n```\n")
    scan.put_state(STATE, content)


def describe(page, now):
    """(date, summary, addresses) for a page worth recording, else None."""
    fm = (scan.call("get_page", {"slug": page["slug"]}) or {}).get("frontmatter") or {}
    title = (page.get("title") or fm.get("title") or fm.get("subject") or "").strip()
    if page.get("type") == "meeting":
        start = str(fm.get("start") or "")
        try:
            when = datetime.datetime.fromisoformat(start.replace("Z", "+00:00"))
        except ValueError:
            return None
        if when.tzinfo is None:
            when = when.replace(tzinfo=datetime.timezone.utc)
        if when > now:
            return None
        return start[:10], "Meeting: " + (title or "untitled"), fm_list(fm.get("attendees"))
    if set(fm_list(fm.get("labels"))) & SKIP_LABELS:
        return None
    date = str(fm.get("date") or "")[:10]
    addrs = fm_list(fm.get("participants")) + fm_list(fm.get("from"))
    return date, "Email: " + (title or "no subject"), addrs


def has_entry(slug, source, cache):
    if slug not in cache:
        entries = scan.call("get_timeline", {"slug": slug})
        if isinstance(entries, dict):
            entries = entries.get("entries") or entries.get("timeline") or []
        cache[slug] = {e.get("source") for e in entries or [] if isinstance(e, dict)}
    return source in cache[slug]


def main():
    now = datetime.datetime.now(datetime.timezone.utc)
    done = load_state()
    pages = []
    for sid in google_sources():
        for kind in ("email", "meeting"):
            pages += [dict(p, type=kind, _source=sid) for p in scan.all_pages(type=kind, source_id=sid)]
    seen = {}
    for p in pages:
        seen[p["slug"]] = seen.get(p["slug"], 0) + 1
    todo = [p for p in pages if done.get(p["slug"]) != str(p.get("updated_at") or "")]
    todo.sort(key=lambda p: str(p.get("updated_at") or ""), reverse=True)
    todo = todo[:MAX_PAGES]
    if not todo:
        return
    index = person_index()
    cache, lines, changed = {}, [], False
    for page in todo:
        slug = page["slug"]
        try:
            info = describe(page, now)
            if info is not None and DATE.match(info[0]):
                date, summary, addrs = info
                source = slug if seen[slug] == 1 else page["_source"] + ":" + slug
                targets = []
                for a in scan.people(addrs):
                    owners = index.get(a, [])
                    if len(owners) == 1:
                        if owners[0] not in targets:
                            targets.append(owners[0])
                    else:
                        lines.append(f"timeline-sweep: {a} in {slug}: " +
                                     ("no person page" if not owners else "matches " + " and ".join(owners)))
                for person in targets:
                    if has_entry(person, source, cache):
                        continue
                    if not DRY_RUN:
                        scan.call("add_timeline_entry", {"slug": person, "date": date, "summary": summary[:200],
                                                         "detail": "", "source": source})
                    cache[person].add(source)
                    lines.append(f"timeline-sweep: {person} <- {source} ({date})")
            done[slug] = str(page.get("updated_at") or "")
            changed = True
        except Exception as e:
            lines.append(f"timeline-sweep: could not process {slug}: {e}")
    if changed and not DRY_RUN:
        save_state(done)
    if lines:
        print("\n".join(lines))


if __name__ == "__main__":
    main()
