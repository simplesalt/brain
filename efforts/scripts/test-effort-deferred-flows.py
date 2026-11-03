import copy
import json
import os
import re
import shlex
import shutil
import subprocess
import sys
import tempfile
import unittest

HERE = os.path.dirname(os.path.abspath(__file__))
SCRIPT_DIR = os.environ.get("EFFORT_SCRIPTS_DIR") or HERE
FAKE_GH = os.path.join(HERE, "testdata", "deferred", "fake_gh.py")

REPO = "simplesalt/projects"
DASH = "—"
HOST = subprocess.run(["hostname"], capture_output=True, text=True, check=True).stdout.strip()
SESSION = "798b72f0-ff30-5d0f-a8c1-1f33d6db4fe0"
OTHER_SESSION = "11111111-2222-3333-4444-555555555555"
HOLDER = HOST + "/" + SESSION
OTHER_HOLDER = HOST + "/" + OTHER_SESSION

SECTIONS = [
    "Title", "Assigned", "Parent", "Blockers", "Why", "What", "Indicators",
    "Gaps", "Where", "Facts", "Unknowns", "How", "Tags",
]
WRITE_OPS = {"create_issue", "patch_issue", "set_field", "add_blocked_by", "remove_blocked_by"}


def ref(number):
    return f"{REPO}#{number}"


def render_body(**overrides):
    values = {
        "Title": "An effort", "Assigned": "ssint-build-oci", "Parent": "none", "Blockers": "none",
        "Why": "why text", "What": "what text", "Indicators": "indicator text", "Gaps": "none",
        "Where": "where text", "Facts": "none", "Unknowns": "none", "How": "none", "Tags": "tag",
    }
    values.update(overrides)
    return "".join(f"### {name}\n\n{values[name]}\n\n" for name in SECTIONS).rstrip("\n")


def section(body, name):
    parts = re.split(r"^### (\w+)\n\n", body, flags=re.M)
    sections = dict(zip(parts[1::2], (text.strip("\n") for text in parts[2::2])))
    return sections[name]


def open_gaps(body):
    return [line for line in section(body, "Gaps").split("\n") if line.startswith("- [ ] [G")]


def how_marks(body):
    found = re.findall(r"^- \[(.)\] \[(T\d+)\]", section(body, "How"), flags=re.M)
    return {task: mark for mark, task in found}


def task(number, text, mark=" "):
    return f"- [{mark}] [T{number}] {text} {DASH} blockers: none; indicators: done"


def gap(number, text, mark=" "):
    return f"- [{mark}] [G{number}] {text}"


def parent_body(child, mark=" ", gaps="none"):
    how = "\n".join([task(1, f"Land the loader in {ref(child)}", mark), task(2, "Write the docs")])
    return render_body(Title="Parent", Indicators="the parent ships", Gaps=gaps, How=how)


class Result:
    def __init__(self, process):
        self.code = process.returncode
        self.out = process.stdout
        self.err = process.stderr

    @property
    def json(self):
        return json.loads(self.out)


class Board:
    def __init__(self, root):
        self.bin = os.path.join(root, "bin")
        self.home = os.path.join(root, "home")
        self.state_path = os.path.join(root, "state.json")
        self.log_path = os.path.join(root, "calls.jsonl")
        os.makedirs(self.bin)
        os.makedirs(self.home)
        wrapper = os.path.join(self.bin, "gh")
        with open(wrapper, "w") as handle:
            handle.write(f"#!/bin/sh\nexec {shlex.quote(sys.executable)} {shlex.quote(FAKE_GH)} \"$@\"\n")
        os.chmod(wrapper, 0o755)
        self.save({"next_number": 100, "issues": {}})
        open(self.log_path, "w").close()

    def load(self):
        with open(self.state_path) as handle:
            return json.load(handle)

    def save(self, state):
        with open(self.state_path, "w") as handle:
            json.dump(state, handle)

    def add(self, number, status, title=None, state="OPEN", parent=None, blocked_by=(), body=None,
            in_use="", priority=None, assigned="ssint-build-oci"):
        state_doc = self.load()
        state_doc["issues"][str(number)] = {
            "title": title or f"[Feature] Effort {number}",
            "state": state,
            "state_reason": None,
            "body": body if body is not None else render_body(),
            "parent": str(parent) if parent is not None else None,
            "blocked_by": [str(b) for b in blocked_by],
            "status": status,
            "priority": priority,
            "in_use": in_use,
            "assigned": assigned,
        }
        self.save(state_doc)

    def issue(self, number):
        return self.load()["issues"][str(number)]

    def calls(self, op=None):
        with open(self.log_path) as handle:
            entries = [json.loads(line) for line in handle if line.strip()]
        return [e for e in entries if op is None or e["op"] == op]

    def writes(self, since=0):
        return [e for e in self.calls()[since:] if e["op"] in WRITE_OPS]

    def field_writes(self, number, field):
        return [e for e in self.calls("set_field") if e["item"] == number and e["field"] == field]

    def run(self, script, *args, session=SESSION):
        env = dict(os.environ)
        env.update({
            "HOME": self.home,
            "PATH": self.bin + os.pathsep + os.environ.get("PATH", ""),
            "CLAUDE_CODE_SESSION_ID": session,
            "FAKE_GH_STATE": self.state_path,
            "FAKE_GH_LOG": self.log_path,
        })
        process = subprocess.run(
            [os.path.join(SCRIPT_DIR, script), *[str(a) for a in args]],
            capture_output=True, text=True, env=env, timeout=180,
        )
        return Result(process)


class FlowCase(unittest.TestCase):
    def new_board(self):
        root = tempfile.mkdtemp(prefix="effort-deferred-flows-")
        self.addCleanup(shutil.rmtree, root, True)
        board = Board(root)
        self.boards.append(board)
        return board

    def setUp(self):
        self.boards = []
        self.board = self.new_board()

    def tearDown(self):
        for board in self.boards:
            self.assertEqual(board.calls("unhandled"), [])

    def succeeded(self, result):
        self.assertEqual(result.code, 0, result.err)
        return result


class DeferChildTest(FlowCase):
    def seed(self, board, parent_status="Doing", child_status="Doing"):
        board.add(10, parent_status, title="[Feature] Parent", blocked_by=[11], in_use=OTHER_HOLDER,
                  body=parent_body(11))
        board.add(11, child_status, title="[Feature] Loader", parent=10, in_use=HOLDER,
                  body=render_body(Title="Loader", Parent=ref(10)))

    def test_deferring_a_child_rewrites_its_parent(self):
        self.seed(self.board)
        result = self.succeeded(self.board.run("update-effort.sh", 11, "--status", "Deferred"))
        sync = result.json["parent_sync"]
        self.assertEqual(
            (sync["parent"], sync["action"], sync["edge"], sync["gap_added"], sync["task"]),
            (ref(10), "defer", "removed", True, "T1"),
        )
        self.assertEqual((sync["parent_status_before"], sync["parent_status_after"]), ("Doing", "Planning"))
        self.assertEqual(self.board.issue(11)["status"], "Deferred")
        parent = self.board.issue(10)
        self.assertEqual(parent["blocked_by"], [])
        gaps = open_gaps(parent["body"])
        self.assertEqual(len(gaps), 1)
        self.assertIn(ref(11), gaps[0])
        self.assertIn("is deferred", gaps[0])
        self.assertEqual(how_marks(parent["body"]), {"T1": "-", "T2": " "})
        self.assertEqual(parent["status"], "Planning")
        self.assertEqual(parent["in_use"], OTHER_HOLDER)
        self.assertEqual(self.board.field_writes(10, "In Use"), [])
        self.assertEqual([e["blocker"] for e in self.board.calls("remove_blocked_by")], [11])

    def test_syncing_again_changes_nothing(self):
        self.seed(self.board)
        self.succeeded(self.board.run("update-effort.sh", 11, "--status", "Deferred"))
        before = self.board.load()
        mark = len(self.board.calls())
        result = self.succeeded(self.board.run("sync-deferred-parent.sh", 11))
        self.assertEqual(self.board.load(), before)
        self.assertEqual(self.board.writes(mark), [])
        sync = result.json
        self.assertEqual((sync["action"], sync["edge"], sync["gap_added"], sync["task"]), ("defer", "absent", False, None))

    def test_parent_status_after_deferral(self):
        expected = {
            "Blocked": "Planning",
            "Review": "Planning",
            "Planning": "Planning",
            "Paused": "Paused",
            "Deferred": "Deferred",
        }
        for before, after in expected.items():
            with self.subTest(parent=before):
                board = self.new_board()
                self.seed(board, parent_status=before, child_status="Deferred")
                result = self.succeeded(board.run("sync-deferred-parent.sh", 11))
                self.assertEqual(result.json["parent_status_after"], after)
                parent = board.issue(10)
                self.assertEqual(parent["status"], after)
                self.assertEqual(parent["blocked_by"], [])
                gaps = open_gaps(parent["body"])
                self.assertEqual(len(gaps), 1)
                self.assertIn(ref(11), gaps[0])
                self.assertEqual(how_marks(parent["body"]), {"T1": "-", "T2": " "})

    def test_a_parent_without_a_status_keeps_none(self):
        self.seed(self.board, parent_status="", child_status="Deferred")
        result = self.succeeded(self.board.run("sync-deferred-parent.sh", 11))
        self.assertEqual(result.json["parent_status_after"], None)
        parent = self.board.issue(10)
        self.assertEqual(parent["status"], "")
        self.assertEqual(self.board.field_writes(10, "Status"), [])
        self.assertEqual(len(open_gaps(parent["body"])), 1)
        self.assertEqual(how_marks(parent["body"]), {"T1": "-", "T2": " "})


class UndeferChildTest(FlowCase):
    def seed(self):
        gaps = gap(1, f"{ref(11)} (Loader) is deferred: re-plan without it, or pick it back up")
        self.board.add(10, "Planning", title="[Feature] Parent", in_use=OTHER_HOLDER, body=parent_body(11, "-", gaps))
        self.board.add(11, "Deferred", title="[Feature] Loader", parent=10, in_use=HOLDER,
                       body=render_body(Title="Loader", Parent=ref(10)))

    def test_undeferring_a_child_restores_the_edge_and_the_task(self):
        self.seed()
        result = self.succeeded(self.board.run("update-effort.sh", 11, "--status", "Planning"))
        sync = result.json["parent_sync"]
        self.assertEqual((sync["action"], sync["edge"], sync["task"]), ("undefer", "added", "T1"))
        self.assertEqual(self.board.issue(11)["status"], "Planning")
        parent = self.board.issue(10)
        self.assertEqual(parent["blocked_by"], ["11"])
        self.assertEqual(how_marks(parent["body"]), {"T1": " ", "T2": " "})
        self.assertEqual(parent["status"], "Planning")
        self.assertEqual([(e["issue"], e["blocker"]) for e in self.board.calls("add_blocked_by")], [(10, 11)])

    def test_cancelling_a_deferred_child_leaves_the_parent_alone(self):
        self.seed()
        parent_before = copy.deepcopy(self.board.issue(10))
        mark = len(self.board.calls())
        result = self.succeeded(self.board.run("update-effort.sh", 11, "--status", "Cancelled"))
        self.assertNotIn("parent_sync", result.json)
        child = self.board.issue(11)
        self.assertEqual((child["status"], child["state"]), ("Cancelled", "CLOSED"))
        self.assertEqual(self.board.issue(10), parent_before)
        written = {(e["op"], e.get("number", e.get("item"))) for e in self.board.writes(mark)}
        self.assertEqual(written, {("patch_issue", 11), ("set_field", 11)})


class DoneRedirectTest(FlowCase):
    def seed(self, child_priorities):
        how = "\n".join([task(1, "Do the work", "x"), task(2, "Check the work", "x")])
        self.board.add(30, "Review", title="[Feature] Parent", in_use=HOLDER, priority=5,
                       body=render_body(Title="Parent", Indicators="the parent ships", How=how))
        for number, priority in zip((31, 32), child_priorities):
            self.board.add(number, "Deferred", title=f"[Feature] Deferred child {number}", parent=30, priority=priority,
                           body=render_body(Title=f"Child {number}", Parent=ref(30)))
        for number, priority in zip((40, 41, 42), (1, 3, 9)):
            self.board.add(number, "Planning", title=f"[Feature] Other {number}", priority=priority)

    def test_done_with_deferred_children_becomes_a_question_about_the_most_urgent(self):
        self.seed((6, 8))
        result = self.succeeded(self.board.run("update-effort.sh", 30, "--status", "Done"))
        out = result.json
        self.assertEqual(out["status_derived"], "Done -> Planning")
        self.assertEqual(out["deferred_redirect"]["child"], ref(31))
        parent = self.board.issue(30)
        self.assertEqual(parent["state"], "OPEN")
        self.assertEqual(parent["status"], "Planning")
        gaps = open_gaps(parent["body"])
        self.assertEqual(len(gaps), 1)
        self.assertRegex(gaps[0], rf"Should we work on {re.escape(ref(31))} \(.*\) next\?")
        self.assertNotIn(ref(32), gaps[0])
        self.assertGreater(parent["priority"], 3)
        self.assertLess(parent["priority"], 6)
        self.assertEqual(out["deferred_redirect"]["priority"], parent["priority"])
        self.assertEqual([e["value"] for e in self.board.field_writes(30, "Priority")], [parent["priority"]])
        self.assertFalse([e for e in self.board.calls("patch_issue") if e["fields"].get("state") == "closed"])

    def test_without_child_priorities_no_priority_is_written(self):
        self.seed((None, None))
        result = self.succeeded(self.board.run("update-effort.sh", 30, "--status", "Done"))
        out = result.json
        self.assertEqual(out["status_derived"], "Done -> Planning")
        self.assertEqual(out["deferred_redirect"]["priority"], None)
        parent = self.board.issue(30)
        self.assertEqual((parent["state"], parent["status"], parent["priority"]), ("OPEN", "Planning", 5))
        self.assertEqual(self.board.field_writes(30, "Priority"), [])
        self.assertEqual(len(open_gaps(parent["body"])), 1)
        self.assertIn("Should we work on", open_gaps(parent["body"])[0])


class AddTaskTest(FlowCase):
    def test_a_task_naming_a_deferred_child_is_stored_as_deferred(self):
        self.board.add(50, "Doing", title="[Feature] Parent", in_use=HOLDER, blocked_by=[52],
                       body=render_body(Title="Parent", Indicators="the parent ships"))
        self.board.add(51, "Deferred", parent=50)
        self.board.add(52, "Planning", parent=50)
        self.succeeded(self.board.run(
            "update-effort.sh", 50, "--add-task", f"Deliver {ref(51)}", "--task-indicators", "child delivered"))
        self.succeeded(self.board.run(
            "update-effort.sh", 50, "--add-task", f"Deliver {ref(52)}", "--task-indicators", "child delivered"))
        parent = self.board.issue(50)
        self.assertEqual(how_marks(parent["body"]), {"T1": "-", "T2": " "})
        self.assertIn(ref(51), section(parent["body"], "How").split("\n")[0])
        self.assertEqual(parent["status"], "Doing")


class DependTest(FlowCase):
    def seed(self):
        self.board.add(60, "Doing", title="[Feature] Parent", in_use=OTHER_HOLDER)
        self.board.add(61, "Deferred", parent=60)
        self.board.add(62, "Planning", parent=60)
        self.board.add(63, "Deferred")

    def test_a_parent_cannot_be_blocked_by_its_deferred_child(self):
        self.seed()
        result = self.board.run("project-depend.sh", "add", ref(60), "--blocked-by", ref(61))
        self.assertEqual(result.code, 1)
        self.assertIn("cannot be recorded as blocked by its own child", result.err)
        self.assertEqual(self.board.calls("add_blocked_by"), [])
        self.assertEqual(self.board.issue(60)["blocked_by"], [])

    def test_a_parent_may_be_blocked_by_a_child_that_is_not_deferred(self):
        self.seed()
        self.succeeded(self.board.run("project-depend.sh", "add", ref(60), "--blocked-by", ref(62)))
        self.assertEqual(self.board.issue(60)["blocked_by"], ["62"])
        self.assertEqual([(e["issue"], e["blocker"]) for e in self.board.calls("add_blocked_by")], [(60, 62)])

    def test_a_deferred_effort_that_is_not_a_child_may_block(self):
        self.seed()
        self.succeeded(self.board.run("project-depend.sh", "add", ref(60), "--blocked-by", ref(63)))
        self.assertEqual(self.board.issue(60)["blocked_by"], ["63"])
        self.assertEqual([(e["issue"], e["blocker"]) for e in self.board.calls("add_blocked_by")], [(60, 63)])


class CreateEffortTest(FlowCase):
    ARGS = [
        "--title", "Loader", "--type", "Feature", "--assigned", "ssint-build-oci",
        "--why", "the loader is missing", "--what", "a loader exists",
        "--indicators", "the loader loads", "--where", "no loader today", "--tags", "loader",
    ]

    def seed(self):
        self.board.add(80, "Doing", title="[Feature] Parent", in_use=OTHER_HOLDER)

    def create(self, *extra):
        return self.board.run("create-effort.sh", *self.ARGS, "--parent", ref(80), *extra)

    def test_a_deferred_child_records_no_blocker_edge(self):
        self.seed()
        result = self.succeeded(self.create("--status", "Deferred"))
        self.assertEqual(result.err, "")
        out = result.json
        self.assertEqual((out["ref"], out["parent"]), (ref(100), ref(80)))
        child = self.board.issue(100)
        self.assertEqual((child["status"], child["parent"]), ("Deferred", "80"))
        self.assertEqual(self.board.issue(80)["blocked_by"], [])
        self.assertEqual(self.board.calls("add_blocked_by"), [])

    def test_a_default_child_records_the_blocker_edge(self):
        self.seed()
        out = self.succeeded(self.create()).json
        self.assertEqual(out["ref"], ref(100))
        child = self.board.issue(100)
        self.assertEqual((child["status"], child["parent"]), ("Planning", "80"))
        self.assertEqual(self.board.issue(80)["blocked_by"], ["100"])
        self.assertEqual([(e["issue"], e["blocker"]) for e in self.board.calls("add_blocked_by")], [(80, 100)])

    def test_doing_is_not_a_creation_status(self):
        self.seed()
        result = self.create("--status", "Doing")
        self.assertEqual(result.code, 1)
        self.assertIn("--status must be one of: Planning Deferred", result.err)
        self.assertEqual(self.board.writes(), [])


class ReviewRefusalTest(FlowCase):
    def test_review_is_refused_while_the_deferred_child_gap_is_open(self):
        gaps = gap(1, f"{ref(71)} (Loader) is deferred: re-plan without it, or pick it back up")
        body = parent_body(71, "-", gaps)
        self.board.add(70, "Planning", title="[Feature] Parent", in_use=HOLDER, body=body)
        self.board.add(71, "Deferred", parent=70)
        result = self.board.run("update-effort.sh", 70, "--status", "Review")
        self.assertEqual(result.code, 1)
        self.assertIn("Planning -> Review is not a move a session may request", result.err)
        parent = self.board.issue(70)
        self.assertEqual(parent["status"], "Planning")
        self.assertEqual(parent["body"], body)
        self.assertEqual(len(open_gaps(parent["body"])), 1)
        self.assertEqual(self.board.writes(), [])


class ListingTest(FlowCase):
    def seed(self):
        self.board.add(90, "Deferred", title="[Feature] Backlog one")
        self.board.add(91, "Deferred", title="[Feature] Backlog two")
        self.board.add(92, "Paused", title="[Feature] Paused one")
        self.board.add(93, "Planning", title="[Feature] Planned one")
        self.board.add(94, "Doing", title="[Feature] Unheld one")
        self.board.add(95, "Doing", title="[Feature] Held one", in_use=OTHER_HOLDER)

    def test_stalled_listing_excludes_deferred_and_counts_them(self):
        self.seed()
        result = self.succeeded(self.board.run("find-stalled-efforts.sh"))
        listed = {e["ref"]: e["condition"] for e in result.json}
        self.assertEqual(listed, {ref(92): "paused", ref(94): "unheld-doing"})
        self.assertIn("deferred: 2 (not stalled, excluded from this output; list with --condition deferred)", result.err)

    def test_stalled_listing_without_deferred_efforts_prints_no_note(self):
        self.board.add(92, "Paused", title="[Feature] Paused one")
        result = self.succeeded(self.board.run("find-stalled-efforts.sh"))
        self.assertEqual([e["ref"] for e in result.json], [ref(92)])
        self.assertEqual(result.err, "")

    def test_deferred_condition_lists_them(self):
        self.seed()
        result = self.succeeded(self.board.run("find-stalled-efforts.sh", "--condition", "deferred"))
        self.assertEqual([(e["ref"], e["condition"], e["status"]) for e in result.json],
                         [(ref(90), "deferred", "Deferred"), (ref(91), "deferred", "Deferred")])

    def test_a_deferred_blocker_root_is_classified_as_deferred(self):
        self.board.add(97, "Blocked", title="[Feature] Waiting", blocked_by=[98])
        self.board.add(98, "Deferred", title="[Feature] Backlog")
        result = self.succeeded(self.board.run("explain-blocked-effort.sh", ref(97)))
        report = result.json
        self.assertEqual(len(report), 1)
        self.assertEqual(report[0]["classification"], "blocked")
        roots = report[0]["roots"]
        self.assertEqual([(r["ref"], r["classification"]) for r in roots], [(ref(98), "root-deferred")])

    def test_a_held_deferred_root_is_still_deferred(self):
        self.board.add(97, "Blocked", title="[Feature] Waiting", blocked_by=[98])
        self.board.add(98, "Deferred", title="[Feature] Backlog", in_use=OTHER_HOLDER)
        result = self.succeeded(self.board.run("explain-blocked-effort.sh", ref(97)))
        roots = result.json[0]["roots"]
        self.assertEqual([(r["ref"], r["classification"]) for r in roots], [(ref(98), "root-deferred")])


if __name__ == "__main__":
    unittest.main()
