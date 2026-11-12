#!/usr/bin/env python3
"""Pick the next meetings and email threads whose people gbrain has not met yet.

Runs every minute as the trigger for the people-scan cron job: it wakes Hermes only when gbrain has synced
a meeting or email thread whose people it has not met yet. It lists Dylan's synced meetings (past ones only) and email
threads from gbrain, drops the ones already handled, newsletters and automated mail, and prints a
batch for Hermes to turn into people pages. Handled slugs are kept on the gbrain page ops/people-scan
(Hermes cannot tag pages in the google source), marked when handed out so no item is
processed twice. With nothing to do it prints {"wakeAgent": false} so the job skips the model call.

Hermes only judges: its answer lists the real people as JSON after a PEOPLE-SCAN-RESULT line. Before
picking the next batch, this script reads those answers from the job's saved output and writes the
pages itself (a new page, or a timeline entry on an existing one), so page format never depends on
the model.
"""
import datetime
import json
import os
import re
import sys
import unicodedata
import urllib.request

URL = os.environ.get("GBRAIN_URL", "http://gbrain:3131/mcp")
TOKEN = os.environ.get("GBRAIN_REMOTE_TOKEN", "gbrain-mcp")
STATE = "ops/people-scan"
BATCH = int(os.environ.get("PEOPLE_SCAN_BATCH", "3"))
DAYS = 90
KEEP_DAYS = DAYS + 7
MAX_ATTENDEES = 10
MAX_EXAMINED = 60
BACKLOG_EVERY_MIN = 5
OUTPUT_DIR = os.path.join(os.environ.get("HERMES_HOME", "/opt/data"), "cron", "output")
MARKER = "PEOPLE-SCAN-RESULT"
DRY_RUN = os.environ.get("PEOPLE_SCAN_DRY_RUN") == "1"
SKIP_LABELS = {"SPAM", "TRASH", "CATEGORY_PROMOTIONS", "CATEGORY_UPDATES", "CATEGORY_SOCIAL", "CATEGORY_FORUMS"}
AUTOMATED = re.compile(r"(no-?reply|do-?not-?reply|notification|newsletter|mailer|bounce|updates?@|news@|info@|"
                       r"marketing|hello@|support@|team@|^em@|receipt|invoice|billing|statement|customerservice|^service@|"
                       r"^contact@|^office@|^welcome@|^register@|^notify@|^studio@|^help@|calendar-server|@resource\.calendar\.google\.com)", re.I)
SELF = re.compile(r"^(dylan[.a-z]*@(simplesalt\.company|simple-salt\.com|saltyonsecurity\.net|gmail\.com)"
                  r"|russ(ell)?[.a-z]*@(simplesalt\.company|simple-salt\.com)"
                  r"|admin@simplesalt\.company)$", re.I)


class Unreachable(Exception):
    pass


def call(tool, args):
    body = json.dumps({"jsonrpc": "2.0", "id": 1, "method": "tools/call",
                       "params": {"name": tool, "arguments": args}}).encode()
    req = urllib.request.Request(URL, data=body, headers={
        "Content-Type": "application/json", "Accept": "application/json, text/event-stream",
        "Authorization": "Bearer " + TOKEN})
    try:
        with urllib.request.urlopen(req, timeout=60) as r:
            raw = r.read().decode()
    except urllib.error.HTTPError as e:
        if e.code >= 500:
            raise Unreachable(f"gbrain answered HTTP {e.code} to {tool}") from None
        raise
    except (urllib.error.URLError, TimeoutError, ConnectionError) as e:
        raise Unreachable(f"gbrain unreachable on {tool}: {getattr(e, 'reason', e)}") from None
    for line in raw.splitlines():
        if line.startswith("data: "):
            msg = json.loads(line[6:])
            if "error" in msg:
                raise RuntimeError(f"{tool}: {msg['error']}")
            text = msg["result"]["content"][0]["text"]
            if msg["result"].get("isError"):
                raise RuntimeError(f"{tool}: {text[:300]}")
            try:
                return json.loads(text)
            except json.JSONDecodeError:
                return text
    raise RuntimeError(f"{tool}: no response")


def put_state(slug, content):
    args = {"slug": slug, "content": content}
    try:
        page = call("get_page", {"slug": slug})
    except RuntimeError as e:
        if "not_found" not in str(e):
            raise
        page = None
    if isinstance(page, dict):
        if page.get("revision"):
            args["expected_revision"] = page["revision"]
        timeline = (page.get("timeline") or "").strip()
        if timeline:
            args["content"] = content.rstrip("\n") + "\n\n" + timeline + "\n"
    call("put_page", args)


def first_json(text, opener="["):
    start = text.find(opener)
    if start < 0:
        return None
    return json.JSONDecoder().raw_decode(text[start:])[0]


def answer_after(text, marker):
    if marker not in text or "SCRIPT-ERROR" in text:
        return None
    return first_json(text.split(marker, 1)[1])


def run(main, name):
    try:
        main()
    except Unreachable as e:
        print(f"{name}: {e}; the next run retries", file=sys.stderr)
        print(json.dumps({"wakeAgent": False}))


def all_pages(**filters):
    pages, offset = [], 0
    while True:
        chunk = call("list_pages", dict(filters, limit=100, offset=offset))
        if not isinstance(chunk, list) or not chunk:
            return pages
        pages += chunk
        offset += len(chunk)


def address(entry):
    m = re.search(r"<([^>]+)>", entry or "")
    return (m.group(1) if m else (entry or "")).strip().lower()


def people(addresses):
    out = []
    for a in addresses:
        email = address(a)
        if email and "@" in email and not SELF.match(email) and not AUTOMATED.search(email) and email not in out:
            out.append(email)
    return out


def slug_date(page):
    m = re.search(r"\d{4}-\d{2}-\d{2}", page["slug"])
    return m.group(0) if m else ""


def candidate(page, now, since):
    """("take", item), ("skip", None) for a page that will never qualify, or ("later", None) for a future meeting."""
    fm = (call("get_page", {"slug": page["slug"]}) or {}).get("frontmatter") or {}
    if page["type"] == "meeting":
        start = fm.get("start") or ""
        try:
            when = datetime.datetime.fromisoformat(start.replace("Z", "+00:00"))
        except ValueError:
            return "skip", None
        if when.tzinfo is None:
            when = when.replace(tzinfo=datetime.timezone.utc)
        attendees = fm.get("attendees") or []
        who = people(attendees)
        if when > now:
            return "later", None
        if when < since or not who or len(attendees) > MAX_ATTENDEES:
            return "skip", None
        return "take", {"slug": page["slug"], "kind": "meeting", "date": start[:10], "title": page.get("title"),
                        "people": who}
    labels = set(fm.get("labels") or [])
    if labels & SKIP_LABELS:
        return "skip", None
    date = (fm.get("date") or "")[:10]
    if not date or date < since.date().isoformat():
        return "skip", None
    who = people(fm.get("participants") or [])
    sender = address(fm.get("from") or "")
    if not who or (AUTOMATED.search(sender) and "SENT" not in labels):
        return "skip", None
    return "take", {"slug": page["slug"], "kind": "email", "date": date, "title": page.get("title"),
            "from": fm.get("from"), "people": who, "dylan_wrote": "SENT" in labels}


def load_state():
    try:
        page = call("get_page", {"slug": STATE})
    except RuntimeError:
        return set()
    body = (page.get("compiled_truth") or "") if isinstance(page, dict) else ""
    m = re.search(r"```json\n(.*?)\n```", body, re.S)
    return set(json.loads(m.group(1))) if m else set()


def keep_from():
    return (datetime.datetime.now(datetime.timezone.utc) - datetime.timedelta(days=KEEP_DAYS)).date().isoformat()


def entry_date(key):
    m = re.search(r"\d{4}-\d{2}-\d{2}", key)
    return m.group(0) if m else ""


def applied_marks(done):
    return {s[len("output:"):] for s in done if s.startswith("output:")}


def save_state(done):
    keep = keep_from()
    done = {s for s in done if not entry_date(s) or entry_date(s) >= keep}
    content = ("---\ntype: note\ntitle: People scan state\n---\n\n# People scan state\n\n"
               "Meetings and email threads the people-scan job has handled. Kept by cron-people-scan.py.\n\n"
               "```json\n" + json.dumps(sorted(done)) + "\n```\n")
    put_state(STATE, content)


def page_slug(name):
    return "people/" + (re.sub(r"[^a-z0-9]+", "-", name.lower()).strip("-") or "unknown")


def yaml_str(s):
    return json.dumps(s, ensure_ascii=False)


def norm_name(s):
    s = unicodedata.normalize("NFKD", s or "")
    return " ".join("".join(c for c in s if not unicodedata.combining(c)).casefold().split())


def as_list(v):
    return [str(x) for x in v] if isinstance(v, list) else ([str(v)] if v else [])


def emails_of(page):
    """The email addresses a page carries in aliases, emails or email, lowercased."""
    fm = (page or {}).get("frontmatter") or {}
    found = {a.strip().lower() for k in ("aliases", "emails", "email") for a in as_list(fm.get(k)) if "@" in a}
    return found


def page_title(page):
    # gbrain v0.60+ keeps the title on the page object, older pages in frontmatter.
    return page.get("title") or (page.get("frontmatter") or {}).get("title")


def read_page(slug):
    try:
        page = call("get_page", {"slug": slug, "include_content": True})
    except RuntimeError:
        return None
    return page if isinstance(page, dict) else None


def pages_with_email(email):
    """Person pages whose aliases, emails or email carry exactly this address: [(slug, page)]."""
    try:
        hits = call("search", {"query": email, "types": ["person"]})
    except RuntimeError:
        return []
    if isinstance(hits, dict):
        hits = hits.get("results") or []
    found = []
    for slug in dict.fromkeys(h.get("slug") for h in hits if isinstance(h, dict)):
        page = read_page(slug) if slug and slug.startswith("people/") else None
        if page and email in emails_of(page):
            found.append((slug, page))
    return found


def add_to_list(lines, key, value, create):
    item = yaml_str(value)
    for i, line in enumerate(lines):
        if not line.startswith(key + ":"):
            continue
        rest = line[len(key) + 1:].strip()
        if rest.startswith("["):
            inner = rest[1:-1].strip()
            lines[i] = f"{key}: [{inner + ', ' if inner else ''}{item}]"
        elif rest:
            lines[i:i + 1] = [f"{key}:", f"  - {rest}", f"  - {item}"]
        else:
            j = i + 1
            while j < len(lines) and re.match(r"\s*-\s", lines[j]):
                j += 1
            lines.insert(j, f"  - {item}")
        return
    if create:
        lines += [f"{key}:", f"  - {item}"]


def add_email(slug, email):
    """Add the email to the page's aliases (and emails, if it has that list), keeping the rest of the page."""
    for _ in range(3):
        page = read_page(slug)
        content = (page or {}).get("content")
        m = re.match(r"---\n(.*?)\n---(?=\n|$)", content or "", re.S)
        if not m:
            raise RuntimeError(f"{slug}: no frontmatter to add {email} to")
        if page.get("revision") is None:
            raise RuntimeError(f"{slug}: get_page returned no revision")
        lines = m.group(1).split("\n")
        add_to_list(lines, "aliases", email, True)
        add_to_list(lines, "emails", email, False)
        new = "---\n" + "\n".join(lines) + "\n---"
        if new == m.group(0):
            return
        try:
            call("edit_page", {"slug": slug, "expected_revision": page["revision"],
                               "edits": [{"old_text": m.group(0), "new_text": new}]})
            return
        except RuntimeError as e:
            if "revision_conflict" not in str(e):
                raise
    raise RuntimeError(f"{slug}: revision conflict adding {email}")


def new_slug(name, email, company):
    """A free slug for this person, or the slug of a page of the same name that carries no other identifier."""
    base = page_slug(name)
    suffix = page_slug(company or email.split("@", 1)[1])[len("people/"):]
    tries = [base, f"{base}-{suffix}"] + [f"{base}-{suffix}-{n}" for n in range(2, 10)]
    for slug in tries:
        page = read_page(slug)
        if page is None:
            return slug, False
        if not emails_of(page) and norm_name(page_title(page)) == norm_name(name):
            return slug, True
    raise RuntimeError(f"no free slug for {name}")


def write_person(p):
    """Create the person's page, or add this meeting or thread to the page they already have.

    The page is found by the exact email first; the model's existing_slug is used only when the title
    matches the name. A page of the same name that carries another email is a different person.
    """
    name, email, item = (p.get("name") or "").strip(), (p.get("email") or "").strip().lower(), p.get("item") or ""
    if not name or "@" not in email or not item:
        return
    entry = {"date": p.get("date") or "", "summary": (p.get("summary") or "Met or emailed Dylan")[:200], "source": item}
    matches = pages_with_email(email)
    if len(matches) > 1:
        print(f"people-scan: {email} is on several person pages ({', '.join(s for s, _ in matches)}); skipped {name}",
              file=sys.stderr)
        return
    slug, needs_email = (matches[0][0], False) if matches else (None, True)
    if not slug and p.get("existing_slug"):
        page = read_page(p["existing_slug"])
        if page and norm_name(page_title(page)) == norm_name(name):
            slug = p["existing_slug"]
    if not slug:
        slug, exists = new_slug(name, email, p.get("company"))
        if not exists:
            fm = ["---", "type: person", f"title: {yaml_str(name)}"]
            if p.get("company"):
                fm.append(f"company: {yaml_str(p['company'])}")
            fm += ["emails:", f"  - {yaml_str(email)}", "aliases:", f"  - {yaml_str(email)}", "---"]
            about = (p.get("about") or "").strip()
            body = f"\n\n# {name}\n\n## About\n\n{about}\n\nSource: {p.get('title') or item}, {entry['date']}.\n"
            call("put_page", {"slug": slug, "content": "\n".join(fm) + body})
            if p.get("dylan_met_or_wrote"):
                call("add_tag", {"slug": slug, "tag": "needs-linkedin"})
            needs_email = False
    if needs_email:
        add_email(slug, email)
    # The timeline-sweep job may already have recorded this thread or meeting on the page.
    timeline = call("get_timeline", {"slug": slug})
    if isinstance(timeline, dict):
        timeline = timeline.get("entries") or timeline.get("timeline") or []
    if any(isinstance(e, dict) and e.get("source") == item for e in timeline or []):
        return
    call("add_timeline_entry", dict(entry, slug=slug))


def apply_results(state_files):
    """Write pages for every saved people-scan answer not applied yet; returns the files applied."""
    applied = []
    try:
        jobs = os.listdir(OUTPUT_DIR)
    except OSError:
        return applied
    for job in jobs:
        d = os.path.join(OUTPUT_DIR, job)
        for f in sorted(os.listdir(d)) if os.path.isdir(d) else []:
            key = f"{job}/{f}"
            if key in state_files or not f.endswith(".md"):
                continue
            if entry_date(f) and entry_date(f) < keep_from():
                continue
            text = open(os.path.join(d, f), encoding="utf-8", errors="replace").read()
            if MARKER not in text:
                continue
            try:
                people = answer_after(text, MARKER) or []
            except json.JSONDecodeError:
                people = []
            for p in people if isinstance(people, list) else []:
                try:
                    write_person(p)
                except Exception as e:
                    print(f"people-scan: could not write {p.get('name')}: {e}", file=sys.stderr)
            applied.append(key)
    return applied


def main():
    now = datetime.datetime.now(datetime.timezone.utc)
    since = now - datetime.timedelta(days=DAYS)
    done = load_state()
    applied = apply_results(applied_marks(done))
    done |= {"output:" + k for k in applied}
    pages = [p for p in all_pages(type="meeting", source_id="google") + all_pages(type="email", source_id="google")
             if p["slug"] not in done]
    today = now.date().isoformat()
    keep = keep_from()
    pages = [p for p in pages if slug_date(p) <= today and (not slug_date(p) or slug_date(p) >= keep)]
    pages.sort(key=slug_date, reverse=True)
    fresh = (now - datetime.timedelta(days=2)).date().isoformat()
    if not any(slug_date(p) >= fresh for p in pages) and now.minute % BACKLOG_EVERY_MIN:
        # Only older backlog left: work it every few minutes, so it does not use up the chat model's rate limit.
        if applied and not DRY_RUN:
            save_state(done)
        print(json.dumps({"wakeAgent": False}))
        return
    batch, skipped, examined = [], 0, 0
    for page in pages:
        if len(batch) >= BATCH or examined >= MAX_EXAMINED:
            break
        examined += 1
        verdict, item = candidate(page, now, since)
        if verdict == "take":
            batch.append(item)
            done.add(page["slug"])
        elif verdict == "skip":
            done.add(page["slug"])
            skipped += 1
    if (batch or skipped or applied) and not DRY_RUN:
        save_state(done)
    if not batch:
        print(json.dumps({"wakeAgent": False}))
        return
    print(f"{len(batch)} items to process; {skipped} skipped as automated or out of range; "
          f"{len(pages) - examined} not yet examined.")
    print(json.dumps(batch, indent=1))


if __name__ == "__main__":
    run(main, "people-scan")
