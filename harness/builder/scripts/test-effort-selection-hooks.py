import json
import os
import shutil
import socket
import subprocess
import sys
import tempfile
import time
import unittest

SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, SCRIPT_DIR)
from fake_effort_mcp import FakeEffortServer  # noqa: E402

SESSION = "798b72f0-ff30-5d0f-a8c1-1f33d6db4fe0"
OTHER_SESSION = "11111111-2222-3333-4444-555555555555"
HOLDER = socket.gethostname() + "/" + SESSION
OTHER_HOLDER = socket.gethostname() + "/" + OTHER_SESSION
REF = "simplesalt/projects#630"
REF2 = "simplesalt/oci#42"
SELECT = "mcp__efforts__effort_select"
RELEASE = "mcp__efforts__effort_release"


class Base(unittest.TestCase):
    hook_name = ""

    def setUp(self):
        self.tmp = tempfile.mkdtemp(prefix="effort-selection-hooks-")
        self.addCleanup(shutil.rmtree, self.tmp, True)
        self.bin = os.path.join(self.tmp, "bin")
        self.home = os.path.join(self.tmp, "home")
        os.makedirs(self.bin)
        os.makedirs(self.home)
        for name in (self.hook_name, "effort_mcp_client.py"):
            shutil.copy2(os.path.join(SCRIPT_DIR, name), self.bin)
        self.hook = os.path.join(self.bin, self.hook_name)
        self.server = FakeEffortServer()
        self.server.start()
        self.addCleanup(self.server.stop)

    def register_path(self, session=SESSION):
        return os.path.join(self.home, ".claude", "selected-efforts.d", session)

    def register(self, refs, session=SESSION):
        path = self.register_path(session)
        os.makedirs(os.path.dirname(path), exist_ok=True)
        with open(path, "w") as handle:
            for ref in refs:
                handle.write(ref + "\n")

    def read_register(self, session=SESSION):
        try:
            with open(self.register_path(session)) as handle:
                return [line.strip() for line in handle if line.strip()]
        except OSError:
            return None

    def run_hook(self, payload, stdin=None):
        env = dict(os.environ)
        env["HOME"] = self.home
        env["EFFORT_MCP_URL"] = self.server.url
        env.pop("CLAUDE_CODE_SESSION_ID", None)
        data = stdin if stdin is not None else json.dumps(payload)
        result = subprocess.run(
            [self.hook], input=data, env=env, capture_output=True, text=True, timeout=30
        )
        self.assertEqual(result.returncode, 0, result.stderr)
        return result


class RecordHook(Base):
    hook_name = "record-effort-selection.sh"

    def payload(self, tool, ref=REF, session=HOLDER, response="default", sid=SESSION):
        if response == "default":
            response = {"content": [{"type": "text", "text": json.dumps({"ref": ref})}]}
        return {
            "session_id": sid,
            "hook_event_name": "PostToolUse",
            "tool_name": tool,
            "tool_input": {"ref": ref, "session": session},
            "tool_response": response,
        }

    def test_select_then_release(self):
        self.run_hook(self.payload(SELECT))
        self.assertEqual(self.read_register(), [REF])
        self.run_hook(self.payload(SELECT, ref=REF2))
        self.assertEqual(self.read_register(), [REF, REF2])
        self.run_hook(self.payload(RELEASE))
        self.assertEqual(self.read_register(), [REF2])
        self.run_hook(self.payload(RELEASE, ref=REF2))
        self.assertIsNone(self.read_register())

    def test_no_duplicates_and_reselect_moves_to_end(self):
        self.register([REF, REF2])
        self.run_hook(self.payload(SELECT))
        self.assertEqual(self.read_register(), [REF2, REF])
        self.run_hook(self.payload(SELECT))
        self.assertEqual(self.read_register(), [REF2, REF])

    def test_response_shapes(self):
        text = json.dumps({"ref": REF})
        for response in (
            text,
            [{"type": "text", "text": text}],
            {"content": [{"type": "text", "text": text}]},
            {"ref": REF},
        ):
            with self.subTest(response=response):
                self.run_hook(self.payload(SELECT, response=response))
                self.assertEqual(self.read_register(), [REF])
                os.remove(self.register_path())

    def test_ref_in_response_preferred(self):
        response = [{"type": "text", "text": json.dumps({"ref": REF2})}]
        self.run_hook(self.payload(SELECT, ref="#630", response=response))
        self.assertEqual(self.read_register(), [REF2])

    def test_falls_back_to_input_ref(self):
        self.run_hook(self.payload(SELECT, response=[{"type": "text", "text": "{}"}]))
        self.assertEqual(self.read_register(), [REF])

    def test_error_responses_are_skipped(self):
        for response in (
            {"isError": True, "content": [{"type": "text", "text": json.dumps({"ref": REF})}]},
            "not json: boom",
            [],
            None,
            42,
        ):
            with self.subTest(response=response):
                self.run_hook(self.payload(SELECT, response=response))
                self.assertIsNone(self.read_register())

    def test_failed_release_keeps_ref(self):
        self.register([REF])
        self.run_hook(self.payload(RELEASE, response="error: nope"))
        self.assertEqual(self.read_register(), [REF])

    def test_other_label_is_ignored(self):
        self.run_hook(self.payload(SELECT, session=OTHER_HOLDER))
        self.assertIsNone(self.read_register())
        self.register([REF])
        self.run_hook(self.payload(RELEASE, session=OTHER_HOLDER))
        self.assertEqual(self.read_register(), [REF])

    def test_unrelated_tool_is_ignored(self):
        self.run_hook(self.payload("mcp__efforts__effort_get"))
        self.assertIsNone(self.read_register())

    def test_fail_open(self):
        for stdin in ("not json", "", "[]"):
            self.run_hook(None, stdin=stdin)
        bad = self.payload(SELECT, sid="../escape", session=socket.gethostname() + "/../escape")
        self.run_hook(bad)
        self.run_hook(self.payload(SELECT, ref="--help"))
        self.assertIsNone(self.read_register())


class ReleaseHook(Base):
    hook_name = "release-selected-effort.sh"

    def payload(self, sid=SESSION):
        return {"session_id": sid, "hook_event_name": "SessionEnd"}

    def releases(self):
        return [args for name, args in self.server.calls if name == "effort_release"]

    def test_releases_each_ref_and_removes_register(self):
        self.register([REF, "", REF2])
        self.run_hook(self.payload())
        self.assertEqual(
            self.releases(),
            [{"ref": REF, "session": HOLDER}, {"ref": REF2, "session": HOLDER}],
        )
        self.assertIsNone(self.read_register())

    def test_other_sessions_register_untouched(self):
        self.register([REF], session=OTHER_SESSION)
        self.run_hook(self.payload())
        self.assertEqual(self.server.calls, [])
        self.assertEqual(self.read_register(OTHER_SESSION), [REF])

    def test_no_register_makes_no_calls(self):
        self.run_hook(self.payload())
        self.assertEqual(self.server.calls, [])

    def test_failed_release_still_removes_register_and_continues(self):
        self.register([REF, REF2])
        self.server.fail_refs = {REF}
        self.run_hook(self.payload())
        self.assertEqual(len(self.releases()), 2)
        self.assertIsNone(self.read_register())

    def test_server_down(self):
        self.register([REF])
        self.server.stop()
        self.run_hook(self.payload())
        self.assertIsNone(self.read_register())

    def test_hanging_server_is_bounded(self):
        self.register([REF])
        self.server.mode = "sleep"
        started = time.monotonic()
        self.run_hook(self.payload())
        self.assertLess(time.monotonic() - started, 15)
        self.assertIsNone(self.read_register())

    def test_bad_stdin(self):
        self.register([REF])
        self.run_hook(None, stdin="not json")
        self.assertEqual(self.read_register(), [REF])

    def test_escaping_session_id(self):
        self.run_hook(self.payload(sid="../escape"))
        self.assertEqual(self.server.calls, [])


if __name__ == "__main__":
    unittest.main()
