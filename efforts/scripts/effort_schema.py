import argparse
import json
import re
import string
import sys

SECTION_ORDER = [
    "Title", "Assigned", "Parent", "Blockers", "Why", "What",
    "Indicators", "Gaps", "Where", "Facts", "Unknowns", "How", "Tags",
]
NONE_DEFAULT_SECTIONS = {"Parent", "Blockers", "Gaps", "Facts", "Unknowns", "How"}
NONE_SENTINEL_SECTIONS = {"Gaps", "Facts", "Unknowns", "How"}
ITEM_SECTIONS = ["Gaps", "Unknowns", "Facts", "Indicators", "How"]
CHECKBOX_SECTIONS = {"Gaps", "Unknowns", "How"}
ID_PREFIX = {"Gaps": "G", "Unknowns": "U", "How": "T", "Facts": "F", "Indicators": "I"}
STATUSES = [
    "Empty", "Planning", "Doing", "Blocked", "Review", "Paused", "Deferred",
    "Cancelled", "Done",
]
SCHEMA_SET = set(SECTION_ORDER)

MOVES = {
    "Empty": ("Planning", "Deferred"),
    "Planning": ("Doing", "Paused", "Cancelled", "Deferred"),
    "Doing": ("Blocked", "Review", "Paused", "Cancelled", "Deferred"),
    "Blocked": ("Doing", "Paused", "Cancelled", "Deferred"),
    "Review": ("Done", "Planning", "Paused", "Cancelled", "Deferred"),
    "Paused": ("Planning", "Cancelled", "Deferred"),
    "Deferred": ("Planning", "Doing", "Blocked", "Review", "Paused", "Cancelled", "Done"),
    "Done": ("Deferred",),
    "Cancelled": ("Deferred",),
}

EMDASH = "—"
TRAIL_SEP = " " + EMDASH + " "

STOPGAP_REQUIREMENTS = {
    "doing_indicators": (
        "Doing requires Indicators to be non-empty (stopgap, not a rule of the "
        "status machine); it is empty " + EMDASH + " fill it in step 1 Design."
    ),
    "review_how_closed": (
        "Review requires every How item to be closed or cancelled (stopgap, not a "
        "rule of the status machine); {open_how} still open " + EMDASH + " finish "
        "them in step 2 Deliver (or cancel what's no longer needed) before "
        "requesting review."
    ),
    "done_open_children": (
        "Done requires open_children == 0 (stopgap, not a rule of the status "
        "machine); {open_children} still open " + EMDASH + " carry each remaining "
        "child Effort through its own step 3 Review, or reparent it, first."
    ),
}

FENCE_OPEN_RE = re.compile(r'^ {0,3}(`{3,}|~{3,})')
FENCE_CLOSE_RE = re.compile(r'^ {0,3}(`{3,}|~{3,})\s*$')

CANONICAL_CHECKBOX_RE = re.compile(r'^- \[([ x~-])\] \[([A-Za-z])(\d+)\] (.*)$')
CANONICAL_PLAIN_RE = re.compile(r'^- \[([A-Za-z])(\d+)\] (.*)$')

MARK_STATE = {" ": "open", "x": "closed", "~": "cancelled", "-": "deferred"}

BACKTICK_RE = re.compile(r'`[^`]+`')
URL_RE = re.compile(r'[a-zA-Z][a-zA-Z0-9+.-]*://\S+')
ISSUE_REF_RE = re.compile(r'(?:[\w.-]+/[\w.-]+)?#\d+')
ARTEFACT_SUFFIXES = r'sh|py|json|yaml|yml|md|toml|ini|cfg|ts|js|go|rs|sql|txt|log|lock'
ARTEFACT_RE = re.compile(r'(?<![\w.-])[\w.-]+\.(?:' + ARTEFACT_SUFFIXES + r')(?![\w])')
PATH_RE = re.compile(
    r'(?<!\S)(?:[~.]?/)?[A-Za-z_][\w.-]*(?:/[\w.-]+){2,}(?!\S)'
    r'|(?<!\S)(?:[~.]?/)?[A-Za-z_][\w.-]*(?:/[\w.-]+)+\.(?:' + ARTEFACT_SUFFIXES + r')(?!\S)'
)


def fence_open(line):
    m = FENCE_OPEN_RE.match(line)
    if not m:
        return None
    marker = m.group(1)
    return marker[0], len(marker)


def fence_close(line, char, length):
    m = FENCE_CLOSE_RE.match(line)
    if not m:
        return False
    marker = m.group(1)
    return marker[0] == char and len(marker) >= length


def raw_lines(body):
    parts = (body + "\n").split("\n")
    parts.pop()
    return parts


def strip_cr(line):
    if line.endswith("\r"):
        return line[:-1]
    return line


def extract_section(body, heading):
    in_section = False
    in_fence = False
    fence_char = ""
    fence_len = 0
    out_lines = []
    for raw in raw_lines(body):
        line = strip_cr(raw)
        if in_fence:
            if fence_close(line, fence_char, fence_len):
                in_fence = False
            if in_section:
                out_lines.append(line)
            continue
        opened = fence_open(line)
        if opened:
            fence_char, fence_len = opened
            in_fence = True
            if in_section:
                out_lines.append(line)
            continue
        if line == "### " + heading:
            in_section = True
            continue
        if line.startswith("### "):
            in_section = False
            continue
        if in_section:
            out_lines.append(line)
    while out_lines and out_lines[0] == "":
        out_lines.pop(0)
    while out_lines and out_lines[-1] == "":
        out_lines.pop()
    return "\n".join(out_lines)


def render(sections):
    def val(name):
        v = sections.get(name)
        return v if v else ""

    def withdefault(name):
        v = val(name)
        return v if v else "none"

    order = [
        ("Title", False), ("Assigned", False), ("Parent", True), ("Blockers", True),
        ("Why", False), ("What", False), ("Indicators", False), ("Gaps", True),
        ("Where", False), ("Facts", True), ("Unknowns", True), ("How", True),
        ("Tags", False),
    ]
    chunks = []
    for name, defaulted in order:
        text = withdefault(name) if defaulted else val(name)
        chunks.append("### " + name + "\n\n" + text + "\n\n")
    return "".join(chunks).rstrip("\n")


def split_trailing(rest):
    idx = rest.find(TRAIL_SEP)
    if idx == -1:
        return rest, ""
    return rest[:idx], rest[idx + 1:]


def parse_section_lines(section_name, text):
    if section_name in NONE_SENTINEL_SECTIONS and text.strip() == "none":
        return []
    if text == "":
        return []
    checkbox = section_name in CHECKBOX_SECTIONS
    prefix = ID_PREFIX.get(section_name)
    records = []
    for line in text.split("\n"):
        if line.strip() == "":
            records.append({"kind": "blank"})
            continue
        if line.startswith("- "):
            mark = num = rest = None
            m = CANONICAL_CHECKBOX_RE.match(line)
            if m and m.group(2) == prefix:
                mark, num, rest = m.group(1), m.group(3), m.group(4)
            else:
                m = CANONICAL_PLAIN_RE.match(line)
                if m and m.group(1) == prefix:
                    num, rest = m.group(2), m.group(3)
            if num is not None:
                if checkbox:
                    mark = mark if mark is not None else " "
                    state = MARK_STATE[mark]
                else:
                    mark, state = None, "open"
                item_text, trailing = split_trailing(rest)
                records.append({
                    "kind": "item", "id": int(num), "state": state,
                    "text": item_text, "trailing": trailing, "legacy": False,
                    "bullet": True, "mark": mark,
                })
                continue
            records.append({
                "kind": "item", "id": None, "state": "open",
                "text": line[2:], "trailing": "", "legacy": True,
                "bullet": True, "mark": None,
            })
            continue
        records.append({
            "kind": "item", "id": None, "state": "open",
            "text": line, "trailing": "", "legacy": True,
            "bullet": False, "mark": None,
        })
    return records


def serialize_record(section_name, record):
    if record["kind"] == "blank":
        return ""
    if record["legacy"]:
        if record["bullet"]:
            return "- " + record["text"]
        return record["text"]
    prefix = ID_PREFIX[section_name]
    idstr = f"[{prefix}{record['id']}]"
    text = record["text"]
    trailing = record.get("trailing") or ""
    suffix = f" {trailing}" if trailing else ""
    if section_name in CHECKBOX_SECTIONS:
        return f"- [{record['mark']}] {idstr} {text}{suffix}"
    return f"- {idstr} {text}{suffix}"


def render_section_from_records(section_name, records):
    return "\n".join(serialize_record(section_name, r) for r in records)


def next_id(records):
    ids = [r["id"] for r in records if r["kind"] == "item" and r["id"] is not None]
    return (max(ids) + 1) if ids else 1


def public_item(record):
    return {
        "id": record["id"],
        "state": record["state"],
        "text": record["text"],
        "trailing": record["trailing"],
        "legacy": record["legacy"],
    }


def parse(body):
    sections = {name: extract_section(body, name) for name in SECTION_ORDER}
    items = {}
    for name in ITEM_SECTIONS:
        records = parse_section_lines(name, sections[name])
        items[name] = [public_item(r) for r in records if r["kind"] == "item"]
    return {"sections": sections, "items": items}


def has_evidence(text):
    if BACKTICK_RE.search(text):
        return True
    if URL_RE.search(text):
        return True
    if ISSUE_REF_RE.search(text):
        return True
    if PATH_RE.search(text):
        return True
    if ARTEFACT_RE.search(text):
        return True
    lowered = text.lower()
    if "evidence:" in lowered:
        return True
    if "verified by" in lowered:
        return True
    return False


def section_filled(sections, name):
    text = (sections.get(name) or "").strip()
    if text == "":
        return False
    if name in NONE_DEFAULT_SECTIONS and text == "none":
        return False
    return True


def open_item_count(sections, name):
    records = parse_section_lines(name, sections.get(name) or "")
    return sum(1 for r in records if r["kind"] == "item" and r["state"] == "open")


def move_allowed(from_status, to_status):
    return from_status == to_status or to_status in MOVES.get(from_status, ())


def move_route(from_status, to_status):
    if from_status in ("Done", "Cancelled"):
        return (
            f"reopening a {from_status} Effort needs a person's direction; once given, "
            f"retry with --force and continue at step 1 Design."
        )
    if from_status == "Empty":
        return "set Planning first, once Why, What and Where are filled in step 1 Design."
    if to_status == "Planning":
        return "adding a Gap in step 1 Design moves it there automatically."
    if from_status == "Paused":
        return "resume through Planning, then continue at step 1 Design."
    if to_status == "Done":
        return "set Review first at step 3 Review."
    if to_status == "Review":
        return (
            "set Doing first in step 2 Deliver, finish the How, then set Review at "
            "step 3 Review."
        )
    if to_status == "Blocked":
        return (
            "Blocked follows Doing automatically once a blocker is recorded in step 2 "
            "Deliver."
        )
    if from_status == "Review" and to_status == "Doing":
        return "set Planning, then return to step 2 Deliver."
    return "follow the order of the procedure, from step 1 Design."


def move_violation(from_status, to_status):
    if move_allowed(from_status, to_status):
        return None
    listed = ", ".join(MOVES.get(from_status, ())) or "none"
    return (
        f"{from_status} -> {to_status} is not a move a session may request; from "
        f"{from_status} the moves are {listed} {EMDASH} "
        f"{move_route(from_status, to_status)}"
    )


def gate(sections, from_status, to_status, open_children=0, blockers=0):
    fs = from_status or "Empty"
    violations = []
    refused = move_violation(fs, to_status)
    if refused:
        violations.append(refused)
    if to_status == "Planning":
        for name in ("Why", "What", "Where"):
            if not section_filled(sections, name):
                violations.append(
                    f"Planning requires {name} to be non-empty; it is empty "
                    f"{EMDASH} fill it in step 1 Design."
                )
        if fs == "Empty" and section_filled(sections, "How"):
            violations.append(
                f"Planning (from Empty) forbids How to be filled in advance; it has "
                f"content {EMDASH} clear it, the implementation plan belongs to step 2 "
                f"Deliver, only after step 1 Design settles Why/What/Where."
            )
    elif to_status == "Doing":
        gaps_open = open_item_count(sections, "Gaps")
        if gaps_open > 0:
            violations.append(
                f"Doing requires Gaps to have no open items; {gaps_open} are open "
                f"{EMDASH} resolve them in step 1 Design (/clarify with the user) "
                f"before delivering."
            )
        unk_open = open_item_count(sections, "Unknowns")
        if unk_open > 0:
            violations.append(
                f"Doing requires Unknowns to have no open items; {unk_open} are open "
                f"{EMDASH} resolve them in step 1 Design (/clarify with the user) "
                f"before delivering."
            )
        if not section_filled(sections, "Indicators"):
            violations.append(STOPGAP_REQUIREMENTS["doing_indicators"])
    elif to_status == "Blocked":
        if blockers <= 0:
            violations.append(
                f"Blocked requires blockers > 0; currently 0 {EMDASH} nothing in step 2 "
                f"Deliver blocks this Effort; record the blocking Effort with "
                f"project-depend.sh (blocked_by) first."
            )
    elif to_status == "Review":
        if blockers != 0:
            violations.append(
                f"Review requires blockers == 0; {blockers} recorded {EMDASH} finish each "
                f"blocking Effort in its own step 2 Deliver, or drop the "
                f"project-depend.sh blocked_by link, before requesting review."
            )
        if not section_filled(sections, "How"):
            violations.append(
                f"Review requires How to be non-empty; it is empty "
                f"{EMDASH} fill it in step 2 Deliver."
            )
        else:
            open_how = open_item_count(sections, "How")
            if open_how > 0:
                violations.append(
                    STOPGAP_REQUIREMENTS["review_how_closed"].format(open_how=open_how)
                )
    elif to_status == "Done":
        gaps_open = open_item_count(sections, "Gaps")
        if gaps_open > 0:
            violations.append(
                f"Done requires zero open Gaps; {gaps_open} are open {EMDASH} "
                f"resolve them in step 1 Design (/clarify with the user) first."
            )
        unk_open = open_item_count(sections, "Unknowns")
        if unk_open > 0:
            violations.append(
                f"Done requires zero open Unknowns; {unk_open} are open {EMDASH} "
                f"resolve them in step 1 Design (/clarify with the user) first."
            )
        if blockers != 0:
            violations.append(
                f"Done requires blockers == 0; {blockers} recorded {EMDASH} finish each "
                f"blocking Effort in its own step 2 Deliver, or drop the "
                f"project-depend.sh blocked_by link, first."
            )
        if open_children != 0:
            violations.append(
                STOPGAP_REQUIREMENTS["done_open_children"].format(open_children=open_children)
            )
        open_how = open_item_count(sections, "How")
        if open_how > 0:
            violations.append(
                f"Done requires every How item to be closed or cancelled; "
                f"{open_how} still open {EMDASH} finish them in step 2 Deliver (or "
                f"cancel what's no longer needed) first."
            )
    return violations


def derive_status(sections, current, blockers=0):
    cur = current or "Empty"
    gaps_open = open_item_count(sections, "Gaps")
    if cur in ("Doing", "Blocked", "Review") and gaps_open > 0:
        return "Planning"
    if cur == "Doing" and blockers > 0:
        return "Blocked"
    if cur == "Blocked" and blockers == 0:
        return "Doing"
    return cur


def one_line(text):
    flat = re.sub(r"\s*[\r\n]+\s*", " ", str(text)).strip()
    return re.sub(r"^\[[^\]\n]*\]\s+", "", flat)


def ref_token_re(ref):
    return re.compile(r"(?<![\w/])" + re.escape(ref) + r"(?!\d)")


def word_token_re(word):
    return re.compile(r"(?<!\w)" + re.escape(word) + r"(?!\w)", re.IGNORECASE)


def find_pointer_task(records, child_ref):
    pattern = ref_token_re(child_ref)
    items = [r for r in records if r["kind"] == "item" and not r["legacy"]]
    for r in items:
        lead = (r.get("trailing") or "").lstrip(EMDASH).strip()
        if pattern.match(lead) or pattern.search(r["text"].rstrip()[-len(child_ref) - 1:]):
            return r
    for r in items:
        if pattern.search(r["text"]) or pattern.search(r.get("trailing") or ""):
            return r
    return None


def names_a_ref(text, refs):
    return any(ref_token_re(ref).search(text) for ref in refs)


def open_item_mentions(records, ref, word):
    ref_pattern = ref_token_re(ref)
    word_pattern = word_token_re(word)
    for r in records:
        if r["kind"] != "item" or r["state"] != "open":
            continue
        text = r["text"] + " " + (r.get("trailing") or "")
        if ref_pattern.search(text) and word_pattern.search(text):
            return True
    return False


def append_open_item(sections, section, text):
    records = parse_section_lines(section, sections.get(section) or "")
    records.append({
        "kind": "item", "id": next_id(records), "state": "open", "text": text,
        "trailing": "", "legacy": False, "bullet": True, "mark": " ",
    })
    out = dict(sections)
    out[section] = render_section_from_records(section, records)
    return out


def retag_pointer_task(sections, child_ref, from_mark, to_mark):
    records = parse_section_lines("How", sections.get("How") or "")
    target = find_pointer_task(records, child_ref)
    if target is None or target["mark"] != from_mark:
        return sections, None
    target["mark"] = to_mark
    target["state"] = MARK_STATE[to_mark]
    out = dict(sections)
    out["How"] = render_section_from_records("How", records)
    return out, f"T{target['id']}"


def defer_parent(body, child_ref, child_title, current, blockers=0):
    sections = parse(body)["sections"]
    gap_records = parse_section_lines("Gaps", sections.get("Gaps") or "")
    gap_added = not open_item_mentions(gap_records, child_ref, "deferred")
    if gap_added:
        sections = append_open_item(
            sections, "Gaps",
            f"{child_ref} ({one_line(child_title)}) is deferred: re-plan without it, "
            f"or pick it back up",
        )
    sections, task = retag_pointer_task(sections, child_ref, " ", "-")
    return {
        "body": render(sections),
        "status": derive_status(sections, current, blockers),
        "gap_added": gap_added,
        "task": task,
    }


def undefer_parent(body, child_ref):
    sections = parse(body)["sections"]
    sections, task = retag_pointer_task(sections, child_ref, "-", " ")
    return {"body": render(sections), "task": task}


def place_priority(child_priority, board_priorities):
    if child_priority is None:
        return None
    smaller = [p for p in board_priorities if p is not None and p < child_priority]
    if not smaller:
        return child_priority - 1
    mid = (child_priority + max(smaller)) / 2
    return int(mid) if mid == int(mid) else mid


def done_redirect(body, deferred_children, board_priorities):
    if not deferred_children:
        raise ValueError(
            f"done-redirect needs at least one Deferred child Effort; none given "
            f"{EMDASH} with nothing deferred there is nothing to redirect to, so "
            f"finish this Effort in step 3 Review (set Status=Done)."
        )
    prioritised = [c for c in deferred_children if c.get("priority") is not None]
    chosen = min(prioritised, key=lambda c: c["priority"]) if prioritised else deferred_children[0]
    ref = chosen["ref"]
    sections = parse(body)["sections"]
    gap_records = parse_section_lines("Gaps", sections.get("Gaps") or "")
    gap_added = not open_item_mentions(gap_records, ref, "next?")
    if gap_added:
        sections = append_open_item(
            sections, "Gaps",
            f"Should we work on {ref} ({one_line(chosen['title'])}) next? It is the most "
            f"urgent deferred child: yes picks it back up and adds it to this Effort's "
            f"What; no opens a conversation about what to do instead",
        )
    return {
        "body": render(sections),
        "status": "Planning",
        "priority": place_priority(chosen.get("priority"), board_priorities),
        "child": ref,
        "gap_added": gap_added,
    }


def blocker_edge_refusal(blocked_ref, blocker_status, blocker_parent):
    if blocker_status == "Deferred" and blocker_parent == blocked_ref:
        return (
            f"{blocked_ref} cannot be recorded as blocked by its own child: the child "
            f"is Deferred, and a Deferred Effort never holds up its parent {EMDASH} take "
            f"the child out of Deferred first (step 1 Design: set its status to "
            f"Planning), then record the blocker."
        )
    return None


def normalize_lines_account(text):
    text = text.replace("\r\n", "\n").replace("\r", "\n")
    return [l.rstrip() for l in text.split("\n")]


def segment_account(lines):
    heading = None
    in_fence = False
    fence_char = None
    fence_len = 0
    entries = []
    last_open_idx = None
    last_open_heading = None
    for idx, line in enumerate(lines):
        if in_fence:
            entries.append({"line": line, "heading_line": False, "owner": heading, "poisoned": False})
            if fence_close(line, fence_char, fence_len):
                in_fence = False
            continue
        opened = fence_open(line)
        if opened:
            fence_char, fence_len = opened
            in_fence = True
            last_open_idx = idx
            last_open_heading = heading
            entries.append({"line": line, "heading_line": False, "owner": heading, "poisoned": False})
            continue
        if line.startswith("### "):
            heading = line[4:].strip()
            entries.append({"line": line, "heading_line": True, "owner": heading, "poisoned": False})
            continue
        entries.append({"line": line, "heading_line": False, "owner": heading, "poisoned": False})
    if in_fence and last_open_idx is not None:
        for i in range(last_open_idx, len(entries)):
            entries[i]["heading_line"] = False
            entries[i]["owner"] = last_open_heading
            entries[i]["poisoned"] = True
    return entries


def account_excerpt(line):
    text = line.strip()
    if len(text) > 72:
        text = text[:72] + "..."
    return text


def account(body, isset_names):
    entries = segment_account(normalize_lines_account(body))
    isset = set(isset_names)
    problems = []
    swallowed_names = []
    for e in entries:
        line = e["line"]
        if line.strip() == "":
            continue
        if e["poisoned"] and line.startswith("### "):
            name = line[4:].strip()
            if name in SCHEMA_SET:
                if name not in swallowed_names:
                    swallowed_names.append(name)
                continue
        if e["heading_line"]:
            name = line[4:].strip()
            if name in SCHEMA_SET:
                continue
            problems.append(("a heading not in the Effort schema", line))
            continue
        owner = e["owner"]
        if owner is None:
            problems.append(("a preamble line before the first '### ' heading", line))
            continue
        if owner not in SCHEMA_SET:
            problems.append((f"content under the non-schema heading '### {owner}'", line))
            continue
        if e["poisoned"] and owner not in isset:
            problems.append((f"content under '### {owner}' left unparseable by an unclosed code fence", line))
            continue
    messages = []
    if swallowed_names or problems:
        if swallowed_names:
            joined = ", ".join(f"### {n}" for n in swallowed_names)
            messages.append(
                f"an unclosed code fence has swallowed one or more later sections "
                f"({joined}), so the PATCH would drop them - close the fence in the "
                f"record's body by hand first"
            )
        if problems:
            messages.append(
                "the fetched body has content the Effort schema parser cannot place, "
                "so the PATCH would drop it:"
            )
            shown = problems[:2]
            for kind, line in shown:
                messages.append(f"  - {kind}: {account_excerpt(line)}")
            remaining = len(problems) - len(shown)
            if remaining > 0:
                messages.append(f"  ...and {remaining} more unaccounted line(s)")
    return messages


def read_file(path):
    with open(path, "r", newline="") as f:
        return f.read()


def cmd_parse(args):
    body = read_file(args.body_file)
    print(json.dumps(parse(body)))


def cmd_render(args):
    with open(args.json_file, "r") as f:
        data = json.load(f)
    sys.stdout.write(render(data.get("sections", {})))


def cmd_account(args):
    body = read_file(args.body_file)
    isset_names = args.isset.split() if args.isset else []
    messages = account(body, isset_names)
    if messages:
        for m in messages:
            print(m)
        sys.exit(1)


def cmd_gate(args):
    body = read_file(args.body_file)
    sections = parse(body)["sections"]
    violations = gate(sections, args.from_, args.to, args.open_children, args.blockers)
    if violations:
        for v in violations:
            print(v)
        sys.exit(1)


def cmd_derive_status(args):
    body = read_file(args.body_file)
    sections = parse(body)["sections"]
    print(derive_status(sections, args.current, args.blockers))


def cmd_item_add(args):
    body = read_file(args.body_file)
    sections = parse(body)["sections"]
    section = args.section
    if section not in ITEM_SECTIONS:
        print(
            f"error: --section must be one of: {' '.join(ITEM_SECTIONS)} (got '{section}')",
            file=sys.stderr,
        )
        sys.exit(2)
    if section == "How":
        current_status = getattr(args, "current_status", None)
        if current_status and current_status != "Doing":
            print(
                f"error: How requires the Effort's status to be Doing before a task "
                f"can be added; it is {current_status} {EMDASH} move the Effort to "
                f"step 2 Deliver (--status Doing) first.",
                file=sys.stderr,
            )
            sys.exit(1)
    if section == "Facts":
        if not args.evidence or not has_evidence(args.evidence):
            print(
                "error: Facts items require --evidence that satisfies has_evidence",
                file=sys.stderr,
            )
            sys.exit(1)
        trailing = args.trailing if args.trailing is not None else f"{EMDASH} evidence: {args.evidence}"
    else:
        trailing = args.trailing if args.trailing is not None else ""
    records = parse_section_lines(section, sections.get(section) or "")
    new_id = next_id(records)
    mark = " " if section in CHECKBOX_SECTIONS else None
    if section == "How" and names_a_ref(args.text, args.deferred_child or []):
        mark = "-"
    records.append({
        "kind": "item", "id": new_id, "state": MARK_STATE.get(mark, "open"), "text": args.text,
        "trailing": trailing, "legacy": False, "bullet": True, "mark": mark,
    })
    sections[section] = render_section_from_records(section, records)
    sys.stdout.write(render(sections))


def cmd_item_close(args):
    body = read_file(args.body_file)
    sections = parse(body)["sections"]
    section = args.section
    if section not in CHECKBOX_SECTIONS:
        print(
            f"error: --section must be one of: {' '.join(sorted(CHECKBOX_SECTIONS))} (got '{section}')",
            file=sys.stderr,
        )
        sys.exit(1)
    records = parse_section_lines(section, sections.get(section) or "")
    target = None
    for r in records:
        if r["kind"] == "item" and r["id"] == args.id:
            target = r
            break
    if target is None:
        print(f"error: no item with id {args.id} in section {section}", file=sys.stderr)
        sys.exit(1)
    word = "resolved" if args.state == "x" else "cancelled"
    existing = (target.get("trailing") or "").strip()
    if existing.startswith(EMDASH):
        existing = existing[len(EMDASH):].strip()
    parts = [p for p in (existing, f"{word}: {args.note}" if args.note else "") if p]
    target["mark"] = args.state
    target["state"] = "closed" if args.state == "x" else "cancelled"
    target["trailing"] = f"{EMDASH} {'; '.join(parts)}" if parts else ""
    sections[section] = render_section_from_records(section, records)
    sys.stdout.write(render(sections))


def cmd_has_evidence(args):
    sys.exit(0 if has_evidence(args.text) else 1)


def read_json_file(path):
    with open(path, "r") as f:
        return json.load(f)


def cmd_defer_parent(args):
    body = read_file(args.body_file)
    result = defer_parent(body, args.child, args.child_title, args.current, args.blockers)
    print(json.dumps(result))


def cmd_undefer_parent(args):
    body = read_file(args.body_file)
    print(json.dumps(undefer_parent(body, args.child)))


def cmd_done_redirect(args):
    body = read_file(args.body_file)
    children = read_json_file(args.children_file)
    priorities = read_json_file(args.priorities_file)
    try:
        result = done_redirect(body, children, priorities)
    except ValueError as e:
        print(f"error: {e}", file=sys.stderr)
        sys.exit(1)
    print(json.dumps(result))


def cmd_may_add_blocker(args):
    message = blocker_edge_refusal(args.blocked, args.blocker_status, args.blocker_parent)
    if message:
        print(message)
        sys.exit(1)


def item_id_number(value):
    text = str(value).strip().lstrip("[").rstrip("]")
    digits = text.lstrip(string.ascii_letters)
    if not digits.isdigit():
        raise argparse.ArgumentTypeError(
            f"'{value}' is not an item id; expected a number or a prefixed id such as G3, U2, F1, I1 or T4"
        )
    return int(digits)


def build_parser():
    parser = argparse.ArgumentParser(prog="effort_schema.py")
    sub = parser.add_subparsers(dest="command", required=True)

    p = sub.add_parser("parse")
    p.add_argument("--body-file", required=True)
    p.set_defaults(func=cmd_parse)

    p = sub.add_parser("render")
    p.add_argument("--json-file", required=True)
    p.set_defaults(func=cmd_render)

    p = sub.add_parser("account")
    p.add_argument("--body-file", required=True)
    p.add_argument("--isset", default="")
    p.set_defaults(func=cmd_account)

    p = sub.add_parser("gate")
    p.add_argument("--body-file", required=True)
    p.add_argument("--from", dest="from_", required=True)
    p.add_argument("--to", required=True)
    p.add_argument("--open-children", type=int, default=0)
    p.add_argument("--blockers", type=int, default=0)
    p.set_defaults(func=cmd_gate)

    p = sub.add_parser("derive-status")
    p.add_argument("--body-file", required=True)
    p.add_argument("--current", required=True)
    p.add_argument("--blockers", type=int, default=0)
    p.set_defaults(func=cmd_derive_status)

    p = sub.add_parser("item-add")
    p.add_argument("--body-file", required=True)
    p.add_argument("--section", required=True)
    p.add_argument("--text", required=True)
    p.add_argument("--evidence")
    p.add_argument("--trailing")
    p.add_argument("--current-status")
    p.add_argument("--deferred-child", action="append")
    p.set_defaults(func=cmd_item_add)

    p = sub.add_parser("item-close")
    p.add_argument("--body-file", required=True)
    p.add_argument("--section", required=True)
    p.add_argument("--id", type=item_id_number, required=True)
    p.add_argument("--state", required=True, choices=["x", "~"])
    p.add_argument("--note")
    p.set_defaults(func=cmd_item_close)

    p = sub.add_parser("has-evidence")
    p.add_argument("--text", required=True)
    p.set_defaults(func=cmd_has_evidence)

    p = sub.add_parser("defer-parent")
    p.add_argument("--body-file", required=True)
    p.add_argument("--child", required=True)
    p.add_argument("--child-title", required=True)
    p.add_argument("--current", required=True)
    p.add_argument("--blockers", type=int, default=0)
    p.set_defaults(func=cmd_defer_parent)

    p = sub.add_parser("undefer-parent")
    p.add_argument("--body-file", required=True)
    p.add_argument("--child", required=True)
    p.set_defaults(func=cmd_undefer_parent)

    p = sub.add_parser("done-redirect")
    p.add_argument("--body-file", required=True)
    p.add_argument("--children-file", required=True)
    p.add_argument("--priorities-file", required=True)
    p.set_defaults(func=cmd_done_redirect)

    p = sub.add_parser("may-add-blocker")
    p.add_argument("--blocked", required=True)
    p.add_argument("--blocker-status", required=True)
    p.add_argument("--blocker-parent", default="")
    p.set_defaults(func=cmd_may_add_blocker)

    return parser


def main():
    parser = build_parser()
    args = parser.parse_args()
    args.func(args)


if __name__ == "__main__":
    main()
