import json
import os
import shutil
import socket
import subprocess
import sys
import tempfile
import time
import unittest

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from fake_effort_mcp import FakeEffortServer  # noqa: E402

SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))
HOOK_SOURCE = os.path.join(SCRIPT_DIR, "effort-session-title.sh")

SESSION = "798b72f0-ff30-5d0f-a8c1-1f33d6db4fe0"
OTHER_SESSION = "11111111-2222-3333-4444-555555555555"
HOST = socket.gethostname()
HOLDER = HOST + "/" + SESSION
OTHER_HOLDER = HOST + "/" + OTHER_SESSION

def effort(ref, title, status, in_use):
    return {
        "ref": ref,
        "title": title,
        "state": "open",
        "status": status,
        "assigned": "",
        "in_use": in_use,
        "parent": "",
        "blockers": [],
        "body": "",
        "open_children": 0,
        "open_blockers": 0,
        "item_id": "PVTI_x",
    }


class HookTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.mkdtemp(prefix="effort-session-title-")
        self.addCleanup(shutil.rmtree, self.tmp, True)
        self.bin = os.path.join(self.tmp, "bin")
        self.stub_dir = os.path.join(self.tmp, "stub")
        self.home = os.path.join(self.tmp, "home")
        for path in (self.bin, self.home):
            os.makedirs(path)
        self.hook = os.path.join(self.bin, "effort-session-title.sh")
        shutil.copy2(HOOK_SOURCE, self.hook)
        shutil.copy2(os.path.join(SCRIPT_DIR, "effort_mcp_client.py"), self.bin)
        self.server = FakeEffortServer()
        self.server.start()
        self.addCleanup(self.server.stop)

    def register(self, refs, session=SESSION):
        directory = os.path.join(self.home, ".claude", "selected-efforts.d")
        os.makedirs(directory, exist_ok=True)
        with open(os.path.join(directory, session), "w") as handle:
            for ref in refs:
                handle.write(ref + "\n")

    def stub_effort(self, data):
        self.server.efforts[data["ref"]] = data

    def calls(self):
        return [args["ref"] for _, args in self.server.calls]

    def run_hook(self, payload, env=None, stdin=None):
        full_env = dict(os.environ)
        full_env["HOME"] = self.home
        full_env["EFFORT_MCP_URL"] = self.server.url
        full_env.pop("EFFORT_SESSION_TITLE_TIMEOUT", None)
        env = dict(env or {})
        self.server.mode = env.pop("STUB_MODE", "ok")
        fail_ref = env.pop("STUB_FAIL_REF", None)
        self.server.fail_refs = {fail_ref} if fail_ref else set()
        full_env.update(env)
        data = stdin if stdin is not None else json.dumps(payload)
        return subprocess.run(
            [self.hook], input=data, env=full_env, capture_output=True, text=True, timeout=30
        )

    def payload(self, event="UserPromptSubmit", session=SESSION):
        return {"session_id": session, "hook_event_name": event}

    def assertNoOutput(self, result):
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(result.stdout, "")

    def assertTitle(self, result, event, title):
        self.assertEqual(result.returncode, 0, result.stderr)
        lines = result.stdout.splitlines()
        self.assertEqual(len(lines), 1, result.stdout)
        self.assertEqual(
            json.loads(lines[0]),
            {"hookSpecificOutput": {"hookEventName": event, "sessionTitle": title}},
        )


class HeldEffort(HookTest):
    TITLE = "[Feature] Rename each coding session to its Effort's number, name and phase"
    EXPECTED = "#630 Rename each coding session to its Effort's number, name and phase · Doing"

    def setUp(self):
        super().setUp()
        self.register(["simplesalt/projects#630"])
        self.stub_effort(effort("simplesalt/projects#630", self.TITLE, "Doing", HOLDER))

    def test_user_prompt_submit(self):
        result = self.run_hook(self.payload("UserPromptSubmit"))
        self.assertTitle(result, "UserPromptSubmit", self.EXPECTED)
        self.assertEqual(self.calls(), ["simplesalt/projects#630"])

    def test_session_start(self):
        result = self.run_hook(self.payload("SessionStart"))
        self.assertTitle(result, "SessionStart", self.EXPECTED)

    def test_status_follows_the_board(self):
        self.stub_effort(effort("simplesalt/projects#630", self.TITLE, "Review", HOLDER))
        result = self.run_hook(self.payload())
        self.assertTitle(
            result,
            "UserPromptSubmit",
            "#630 Rename each coding session to its Effort's number, name and phase · Review",
        )

    def test_title_without_type_prefix_is_kept(self):
        self.stub_effort(
            effort("simplesalt/projects#630", "Plain name with [brackets] inside", "Doing", HOLDER)
        )
        result = self.run_hook(self.payload())
        self.assertTitle(
            result, "UserPromptSubmit", "#630 Plain name with [brackets] inside · Doing"
        )

    def test_only_one_leading_prefix_is_removed(self):
        self.stub_effort(
            effort("simplesalt/projects#630", "[Bug] [Urgent] Fix it", "Doing", HOLDER)
        )
        result = self.run_hook(self.payload())
        self.assertTitle(result, "UserPromptSubmit", "#630 [Urgent] Fix it · Doing")

    def test_number_comes_from_ref_in_any_repo(self):
        self.register(["simplesalt/oci#42"])
        self.stub_effort(effort("simplesalt/oci#42", "[Bug] Broken thing", "Planning", HOLDER))
        result = self.run_hook(self.payload())
        self.assertTitle(result, "UserPromptSubmit", "#42 Broken thing · Planning")

    def test_empty_status_emits_nothing(self):
        self.stub_effort(effort("simplesalt/projects#630", self.TITLE, "", HOLDER))
        self.assertNoOutput(self.run_hook(self.payload()))

    def test_blank_title_emits_nothing(self):
        self.stub_effort(effort("simplesalt/projects#630", "   ", "Doing", HOLDER))
        self.assertNoOutput(self.run_hook(self.payload()))


class NothingHeld(HookTest):
    def test_no_register_file(self):
        result = self.run_hook(self.payload())
        self.assertNoOutput(result)
        self.assertEqual(self.calls(), [])

    def test_empty_register_file(self):
        self.register([])
        result = self.run_hook(self.payload())
        self.assertNoOutput(result)
        self.assertEqual(self.calls(), [])

    def test_register_of_blank_and_malformed_lines(self):
        self.register(["", "   ", "not a ref", "--help", "simplesalt/projects#abc"])
        result = self.run_hook(self.payload())
        self.assertNoOutput(result)
        self.assertEqual(self.calls(), [])

    def test_other_sessions_register_is_ignored(self):
        self.register(["simplesalt/projects#630"], session=OTHER_SESSION)
        self.stub_effort(
            effort("simplesalt/projects#630", "[Feature] Mine", "Doing", HOLDER)
        )
        result = self.run_hook(self.payload())
        self.assertNoOutput(result)
        self.assertEqual(self.calls(), [])

    def test_shared_selected_efforts_file_is_not_read(self):
        shared = os.path.join(self.home, ".claude", "selected-efforts")
        os.makedirs(os.path.dirname(shared), exist_ok=True)
        with open(shared, "w") as handle:
            handle.write("simplesalt/projects#630\n")
        self.stub_effort(effort("simplesalt/projects#630", "[Feature] Mine", "Doing", HOLDER))
        result = self.run_hook(self.payload())
        self.assertNoOutput(result)
        self.assertEqual(self.calls(), [])


class OwnershipCheck(HookTest):
    def test_ref_held_by_another_session(self):
        self.register(["simplesalt/projects#630"])
        self.stub_effort(
            effort("simplesalt/projects#630", "[Feature] Theirs", "Doing", OTHER_HOLDER)
        )
        result = self.run_hook(self.payload())
        self.assertNoOutput(result)
        self.assertEqual(self.calls(), ["simplesalt/projects#630"])

    def test_ref_held_by_no_one(self):
        self.register(["simplesalt/projects#630"])
        self.stub_effort(effort("simplesalt/projects#630", "[Feature] Free", "Doing", ""))
        self.assertNoOutput(self.run_hook(self.payload()))

    def test_ref_held_on_another_host(self):
        self.register(["simplesalt/projects#630"])
        self.stub_effort(
            effort("simplesalt/projects#630", "[Feature] Far", "Doing", "other-pod/" + SESSION)
        )
        self.assertNoOutput(self.run_hook(self.payload()))

    def test_most_recent_ref_wins_when_both_held(self):
        self.register(["simplesalt/projects#100", "simplesalt/projects#200"])
        self.stub_effort(effort("simplesalt/projects#100", "[Bug] Older", "Doing", HOLDER))
        self.stub_effort(effort("simplesalt/projects#200", "[Bug] Newer", "Review", HOLDER))
        result = self.run_hook(self.payload())
        self.assertTitle(result, "UserPromptSubmit", "#200 Newer · Review")
        self.assertEqual(self.calls(), ["simplesalt/projects#200"])

    def test_older_ref_shown_when_most_recent_is_not_held(self):
        self.register(["simplesalt/projects#100", "simplesalt/projects#200"])
        self.stub_effort(effort("simplesalt/projects#100", "[Bug] Older", "Doing", HOLDER))
        self.stub_effort(
            effort("simplesalt/projects#200", "[Bug] Newer", "Doing", OTHER_HOLDER)
        )
        result = self.run_hook(self.payload())
        self.assertTitle(result, "UserPromptSubmit", "#100 Older · Doing")
        self.assertEqual(self.calls(), ["simplesalt/projects#200", "simplesalt/projects#100"])

    def test_newest_ref_failing_lookup_does_not_fall_back_to_older(self):
        self.register(["simplesalt/projects#100", "simplesalt/projects#200"])
        self.stub_effort(effort("simplesalt/projects#100", "[Bug] Older", "Doing", HOLDER))
        result = self.run_hook(self.payload(), env={"STUB_FAIL_REF": "simplesalt/projects#200"})
        self.assertNoOutput(result)


class FailOpen(HookTest):
    def setUp(self):
        super().setUp()
        self.register(["simplesalt/projects#630"])
        self.stub_effort(
            effort("simplesalt/projects#630", "[Feature] Something", "Doing", HOLDER)
        )

    def test_stub_exits_non_zero(self):
        result = self.run_hook(self.payload(), env={"STUB_MODE": "fail"})
        self.assertNoOutput(result)
        self.assertEqual(self.calls(), ["simplesalt/projects#630"])

    def test_stub_prints_garbage(self):
        result = self.run_hook(self.payload(), env={"STUB_MODE": "garbage"})
        self.assertNoOutput(result)
        self.assertEqual(self.calls(), ["simplesalt/projects#630"])

    def test_stub_prints_a_json_list(self):
        self.server.efforts["simplesalt/projects#630"] = [1, 2, 3]
        self.assertNoOutput(self.run_hook(self.payload()))

    def test_stub_prints_wrong_field_types(self):
        self.server.efforts["simplesalt/projects#630"] = {
            "ref": 5, "title": 7, "status": ["Doing"], "in_use": HOLDER}
        self.assertNoOutput(self.run_hook(self.payload()))

    def test_stub_sleeps_past_the_timeout(self):
        started = time.monotonic()
        result = self.run_hook(
            self.payload(),
            env={"STUB_MODE": "sleep", "EFFORT_SESSION_TITLE_TIMEOUT": "0.5"},
        )
        elapsed = time.monotonic() - started
        self.assertNoOutput(result)
        self.assertLess(elapsed, 10)

    def test_unusable_timeout_setting_falls_back_to_default(self):
        for value in ("", "abc", "-3", "0", "nan", "inf"):
            with self.subTest(value=value):
                result = self.run_hook(
                    self.payload(), env={"EFFORT_SESSION_TITLE_TIMEOUT": value}
                )
                self.assertTitle(result, "UserPromptSubmit", "#630 Something · Doing")

    def test_server_unreachable(self):
        self.server.stop()
        self.assertNoOutput(self.run_hook(self.payload()))

    def test_server_http_error(self):
        result = self.run_hook(self.payload(), env={"STUB_MODE": "http500"})
        self.assertNoOutput(result)

    def test_missing_client_module(self):
        os.remove(os.path.join(self.bin, "effort_mcp_client.py"))
        self.assertNoOutput(self.run_hook(self.payload()))

    def test_missing_session_id(self):
        result = self.run_hook({"hook_event_name": "UserPromptSubmit"})
        self.assertNoOutput(result)
        self.assertEqual(self.calls(), [])

    def test_missing_hook_event_name(self):
        result = self.run_hook({"session_id": SESSION})
        self.assertNoOutput(result)
        self.assertEqual(self.calls(), [])

    def test_session_id_that_escapes_the_register_directory(self):
        with open(os.path.join(self.home, ".claude", "escape"), "w") as handle:
            handle.write("simplesalt/projects#630\n")
        result = self.run_hook(self.payload(session="../escape"))
        self.assertNoOutput(result)
        self.assertEqual(self.calls(), [])

    def test_unparseable_stdin(self):
        self.assertNoOutput(self.run_hook(None, stdin="not json"))
        self.assertNoOutput(self.run_hook(None, stdin=""))
        self.assertNoOutput(self.run_hook(None, stdin="[]"))
        self.assertEqual(self.calls(), [])


if __name__ == "__main__":
    unittest.main()
