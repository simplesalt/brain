import argparse
import json
import os
import subprocess
import sys
import tempfile
import unittest

SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, SCRIPT_DIR)

import effort_schema as es

SECTION_ORDER = es.SECTION_ORDER


def bash_effort_body(values):
    script = (
        'source "$1"/lib-project.sh 2>/dev/null || true\n'
        'source "$1"/lib-effort.sh\n'
        'shift\n'
        'effort_body "$@"\n'
    )
    args = ["bash", "-c", script, "bash", SCRIPT_DIR] + list(values)
    result = subprocess.run(args, capture_output=True, text=True, check=True)
    return result.stdout.rstrip("\n")


def bash_effort_body_field(body, heading):
    script = (
        'source "$1"/lib-project.sh 2>/dev/null || true\n'
        'source "$1"/lib-effort.sh\n'
        'effort_body_field "$2" "$3"\n'
    )
    args = ["bash", "-c", script, "bash", SCRIPT_DIR, body, heading]
    result = subprocess.run(args, capture_output=True, text=True, check=True)
    return result.stdout


def run_cli(args, input_files=None):
    cmd = [sys.executable, os.path.join(SCRIPT_DIR, "effort_schema.py")] + args
    result = subprocess.run(cmd, capture_output=True, text=True)
    return result.returncode, result.stdout, result.stderr


FENCED_BODY = (
    "### Title\n\nFenced\n\n### Assigned\n\nDylan\n\n### Parent\n\nnone\n\n"
    "### Blockers\n\nnone\n\n### Why\n\nSome code:\n\n```\n### not a heading\n"
    "still inside\n```\n\nafter fence\n\n### What\n\nwhat text\n\n"
    "### Indicators\n\nind\n\n### Gaps\n\nnone\n\n### Where\n\nwhere\n\n"
    "### Facts\n\nnone\n\n### Unknowns\n\nnone\n\n### How\n\nnone\n\n"
    "### Tags\n\ntags\n"
)

CRLF_BODY = (
    "### Title\r\n\r\nT\r\n\r\n### Assigned\r\n\r\nA\r\n\r\n### Parent\r\n\r\nnone\r\n\r\n"
    "### Blockers\r\n\r\nnone\r\n\r\n### Why\r\n\r\nwhy text\r\nwith crlf\r\n\r\n"
    "### What\r\n\r\nwhat text\r\n\r\n### Indicators\r\n\r\nind\r\n\r\n"
    "### Gaps\r\n\r\nnone\r\n\r\n### Where\r\n\r\nwhere\r\n\r\n### Facts\r\n\r\nnone\r\n\r\n"
    "### Unknowns\r\n\r\nnone\r\n\r\n### How\r\n\r\nnone\r\n\r\n### Tags\r\n\r\ntags\r\n"
)

LEGACY_BODY = (
    "### Title\n\nLegacy record\n\n### Assigned\n\nDylan\n\n### Parent\n\nnone\n\n"
    "### Blockers\n\nnone\n\n### Why\n\nBecause the old records predate the item "
    "grammar.\n\n### What\n\nProve legacy prose survives untouched.\n\n"
    "### Indicators\n\nA legacy body round-trips unchanged.\n\n"
    "### Gaps\n\n**Open**\n\nnone\n\n**Resolved 2026-09-05**\n\n"
    "- Something noted without an id.\n- Another freeform bullet.\n\n"
    "### Where\n\nIn the board history.\n\n"
    "### Facts\n\nA long paragraph of unfootnoted prose that just asserts things "
    "without any bracketed id at all.\n\nA second paragraph, separated by a blank "
    "line.\n\n### Unknowns\n\nnone\n\n### How\n\nnone\n\n### Tags\n\nlegacy\n"
)


class RenderMatchesBash(unittest.TestCase):
    def check(self, values):
        expected = bash_effort_body(values)
        sections = dict(zip(SECTION_ORDER, values))
        actual = es.render(sections)
        self.assertEqual(actual, expected)

    def test_realistic_record(self):
        self.check([
            "effort-schema: one module for the Effort grammar",
            "ssint-build-oci",
            "simplesalt/projects#410",
            "",
            "Nothing validates an Effort record, so records drift.",
            "A single Python module is the only implementation of the grammar.",
            "Only one implementation exists and every memory function calls it.",
            "- [ ] [G1] Whether the tense check is enforced or advisory.",
            "somewhere in the repo",
            "- [F1] The bash and Python copies can disagree — evidence: `lib-effort.sh`",
            "- [ ] [U1] Whether existing bodies need migrating.",
            "- [ ] [T1] Write the module — blockers: none; indicators: tests pass",
            "wwwhw,schema",
        ])

    def test_all_empty(self):
        self.check([""] * 13)

    def test_none_defaults(self):
        self.check(["T", "A", "", "", "why", "what", "ind", "", "where", "", "", "", "tags"])

    def test_multiline_and_blank_padding(self):
        self.check([
            "T", "A", "p", "b",
            "\n\nwhy with leading and trailing blank\n\n",
            "what\nmultiline\nvalue",
            "ind", "gap1\ngap2", "where", "fact1", "unk1", "how1", "t a g s",
        ])


class ExtractSectionMatchesBash(unittest.TestCase):
    def test_fenced_headings_are_content(self):
        for heading in SECTION_ORDER:
            expected = bash_effort_body_field(FENCED_BODY, heading)
            actual = es.extract_section(FENCED_BODY, heading)
            self.assertEqual(actual, expected, heading)

    def test_why_keeps_fenced_fake_heading(self):
        why = es.extract_section(FENCED_BODY, "Why")
        self.assertIn("### not a heading", why)
        self.assertIn("still inside", why)
        self.assertIn("after fence", why)

    def test_crlf_stripped(self):
        for heading in SECTION_ORDER:
            expected = bash_effort_body_field(CRLF_BODY, heading)
            actual = es.extract_section(CRLF_BODY, heading)
            self.assertEqual(actual, expected, heading)
        why = es.extract_section(CRLF_BODY, "Why")
        self.assertNotIn("\r", why)
        self.assertEqual(why, "why text\nwith crlf")


class LegacyRoundTrip(unittest.TestCase):
    def test_legacy_body_round_trips_unchanged(self):
        sections = es.parse(LEGACY_BODY)["sections"]
        rendered = es.render(sections)
        self.assertEqual(rendered, LEGACY_BODY.rstrip("\n"))

    def test_legacy_items_have_none_id(self):
        items = es.parse(LEGACY_BODY)["items"]
        for item in items["Gaps"]:
            self.assertTrue(item["legacy"])
            self.assertIsNone(item["id"])
            self.assertEqual(item["state"], "open")
        for item in items["Facts"]:
            self.assertTrue(item["legacy"])
            self.assertIsNone(item["id"])


class IdAllocation(unittest.TestCase):
    def test_starts_at_one(self):
        records = es.parse_section_lines("Gaps", "")
        self.assertEqual(es.next_id(records), 1)

    def test_increments_from_max(self):
        text = "- [ ] [G1] first\n- [ ] [G5] second\n- [ ] [G3] third"
        records = es.parse_section_lines("Gaps", text)
        self.assertEqual(es.next_id(records), 6)

    def test_ignores_legacy_ids(self):
        text = "- freeform legacy line\n- [ ] [G2] canonical"
        records = es.parse_section_lines("Gaps", text)
        self.assertEqual(es.next_id(records), 3)

    def test_none_sentinel_is_empty(self):
        records = es.parse_section_lines("Gaps", "none")
        self.assertEqual(records, [])
        self.assertEqual(es.next_id(records), 1)


def sections_with(overrides):
    base = {name: "" for name in SECTION_ORDER}
    base.update(overrides)
    return base


class GateRules(unittest.TestCase):
    def test_empty_to_planning_requires_why_what_where(self):
        v = es.gate(sections_with({}), "Empty", "Planning")
        self.assertEqual(len(v), 3)

    def test_empty_to_planning_forbids_how(self):
        v = es.gate(sections_with({
            "Why": "w", "What": "w", "Where": "w", "How": "- [ ] [T1] plan it",
        }), "Empty", "Planning")
        self.assertEqual(len(v), 1)
        self.assertIn("How", v[0])

    def test_regress_to_planning_allows_how(self):
        v = es.gate(sections_with({
            "Why": "w", "What": "w", "Where": "w", "How": "- [ ] [T1] plan it",
        }), "Review", "Planning")
        self.assertEqual(v, [])

    def test_regress_to_planning_still_requires_www(self):
        v = es.gate(sections_with({"How": "- [ ] [T1] plan it"}), "Review", "Planning")
        self.assertEqual(len(v), 3)

    def test_doing_requires_no_open_gaps_or_unknowns(self):
        v = es.gate(sections_with({
            "Gaps": "- [ ] [G1] open gap",
            "Unknowns": "- [ ] [U1] open unknown",
            "Indicators": "ind",
            "How": "- [ ] [T1] how",
        }), "Planning", "Doing")
        self.assertEqual(len(v), 2)

    def test_doing_requires_indicators_only(self):
        v = es.gate(sections_with({}), "Planning", "Doing")
        self.assertEqual(len(v), 1)
        self.assertIn("Indicators", v[0])

    def test_doing_passes_when_satisfied(self):
        v = es.gate(sections_with({
            "Indicators": "ind", "How": "- [ ] [T1] how",
        }), "Planning", "Doing")
        self.assertEqual(v, [])

    def test_doing_allows_empty_how(self):
        v = es.gate(sections_with({"Indicators": "ind"}), "Planning", "Doing")
        self.assertEqual(v, [])

    def test_doing_allows_closed_gaps(self):
        v = es.gate(sections_with({
            "Gaps": "- [x] [G1] closed — resolved: done",
            "Indicators": "ind", "How": "- [ ] [T1] how",
        }), "Planning", "Doing")
        self.assertEqual(v, [])

    def test_blocked_requires_blockers(self):
        v = es.gate(sections_with({}), "Doing", "Blocked", blockers=0)
        self.assertEqual(len(v), 1)
        v = es.gate(sections_with({}), "Doing", "Blocked", blockers=1)
        self.assertEqual(v, [])

    def test_review_requires_blockers_zero(self):
        v = es.gate(sections_with({"How": "- [x] [T1] done — resolved: y"}), "Doing", "Review", blockers=1)
        self.assertEqual(len(v), 1)

    def test_review_requires_how_nonempty(self):
        v = es.gate(sections_with({}), "Doing", "Review")
        self.assertEqual(len(v), 1)

    def test_review_requires_all_how_closed(self):
        v = es.gate(sections_with({
            "How": "- [x] [T1] one — resolved: y\n- [ ] [T2] two",
        }), "Doing", "Review")
        self.assertEqual(len(v), 1)

    def test_review_passes_when_satisfied(self):
        v = es.gate(sections_with({
            "How": "- [x] [T1] one — resolved: y\n- [~] [T2] two — cancelled: n",
        }), "Doing", "Review")
        self.assertEqual(v, [])

    def test_done_requires_everything(self):
        v = es.gate(sections_with({
            "Gaps": "- [ ] [G1] open",
            "Unknowns": "- [ ] [U1] open",
            "How": "- [ ] [T1] open",
        }), "Review", "Done", open_children=2, blockers=1)
        self.assertEqual(len(v), 5)

    def test_done_passes_when_satisfied(self):
        v = es.gate(sections_with({
            "How": "- [x] [T1] one — resolved: y",
        }), "Review", "Done", open_children=0, blockers=0)
        self.assertEqual(v, [])

    def test_paused_and_cancelled_have_no_requirements(self):
        self.assertEqual(es.gate(sections_with({}), "Doing", "Paused"), [])
        self.assertEqual(es.gate(sections_with({}), "Doing", "Cancelled"), [])

    def test_violation_names_field_and_step(self):
        v = es.gate(sections_with({}), "Empty", "Planning")
        for msg in v:
            self.assertTrue(any(f in msg for f in ("Why", "What", "Where")))
            self.assertIn("step 1 Design", msg)


class DeriveStatusRatchets(unittest.TestCase):
    def test_doing_with_open_gap_regresses_to_planning(self):
        s = sections_with({"Gaps": "- [ ] [G1] open"})
        self.assertEqual(es.derive_status(s, "Doing"), "Planning")

    def test_blocked_with_open_unknown_does_not_regress(self):
        s = sections_with({"Unknowns": "- [ ] [U1] open"})
        self.assertEqual(es.derive_status(s, "Blocked", blockers=1), "Blocked")

    def test_doing_with_open_unknown_does_not_regress(self):
        s = sections_with({"Unknowns": "- [ ] [U1] open"})
        self.assertEqual(es.derive_status(s, "Doing"), "Doing")

    def test_gap_ratchet_fires_even_with_open_unknown_too(self):
        s = sections_with({
            "Gaps": "- [ ] [G1] open", "Unknowns": "- [ ] [U1] open",
        })
        self.assertEqual(es.derive_status(s, "Doing"), "Planning")

    def test_review_with_open_gap_regresses_to_planning(self):
        s = sections_with({"Gaps": "- [ ] [G1] open"})
        self.assertEqual(es.derive_status(s, "Review"), "Planning")

    def test_gaps_regress_takes_precedence_over_blocker_ratchet(self):
        s = sections_with({"Gaps": "- [ ] [G1] open"})
        self.assertEqual(es.derive_status(s, "Doing", blockers=1), "Planning")

    def test_doing_with_blockers_ratchets_to_blocked(self):
        s = sections_with({})
        self.assertEqual(es.derive_status(s, "Doing", blockers=1), "Blocked")

    def test_blocked_with_no_blockers_ratchets_to_doing(self):
        s = sections_with({})
        self.assertEqual(es.derive_status(s, "Blocked", blockers=0), "Doing")

    def test_otherwise_unchanged(self):
        s = sections_with({})
        self.assertEqual(es.derive_status(s, "Planning"), "Planning")
        self.assertEqual(es.derive_status(s, "Paused"), "Paused")

    def test_missing_status_treated_as_empty(self):
        s = sections_with({})
        self.assertEqual(es.derive_status(s, None), "Empty")
        self.assertEqual(es.derive_status(s, ""), "Empty")


class HasEvidence(unittest.TestCase):
    def test_backtick(self):
        self.assertTrue(es.has_evidence("see `foo.py:12`"))

    def test_url(self):
        self.assertTrue(es.has_evidence("see https://example.com/thing"))

    def test_path_token(self):
        self.assertTrue(es.has_evidence("check builder-sb/policy/scripts/lib-effort.sh"))

    def test_issue_ref_owner_repo(self):
        self.assertTrue(es.has_evidence("per simplesalt/projects#530"))

    def test_issue_ref_bare_hash(self):
        self.assertTrue(es.has_evidence("see #530 for details"))

    def test_literal_evidence_word(self):
        self.assertTrue(es.has_evidence("evidence: manual test run"))

    def test_verified_by(self):
        self.assertTrue(es.has_evidence("verified by Dylan on 2026-09-21"))

    def test_bare_claim_fails(self):
        self.assertFalse(es.has_evidence("this is definitely true, trust me"))

    def test_empty_fails(self):
        self.assertFalse(es.has_evidence(""))


class AccountFailureModes(unittest.TestCase):
    def test_clean_body_has_no_problems(self):
        self.assertEqual(es.account(LEGACY_BODY, []), [])

    def test_unclosed_fence_swallows_later_sections(self):
        body = "### Why\n\n```\nunclosed\n\n### What\n\nswallowed\n\n### Where\n\nalso swallowed\n"
        msgs = es.account(body, [])
        self.assertTrue(any("unclosed code fence" in m for m in msgs))
        self.assertTrue(any("What" in m for m in msgs))

    def test_non_schema_heading(self):
        body = "### Why\n\nsome text\n\n### Bogus\n\nother text\n"
        msgs = es.account(body, [])
        self.assertTrue(any("heading not in the Effort schema" in m for m in msgs))

    def test_preamble_before_first_heading(self):
        body = "stray preamble line\n\n### Why\n\nsome text\n"
        msgs = es.account(body, [])
        self.assertTrue(any("preamble line before the first" in m for m in msgs))

    def test_content_under_non_schema_heading(self):
        body = "### Why\n\ntext\n\n### Bogus\n\nstray content\n"
        msgs = es.account(body, [])
        self.assertTrue(any("non-schema heading" in m for m in msgs))

    def test_unparseable_fence_content_not_isset(self):
        body = "### Why\n\n```\nunclosed forever\n"
        msgs = es.account(body, [])
        self.assertTrue(any("unclosed code fence" in m for m in msgs) or any("left unparseable" in m for m in msgs))

    def test_isset_excuses_poisoned_section(self):
        body = "### Why\n\n```\nunclosed forever\n"
        msgs = es.account(body, ["Why"])
        self.assertEqual(msgs, [])


class CliSmoke(unittest.TestCase):
    def write(self, tmpdir, name, content):
        path = os.path.join(tmpdir, name)
        with open(path, "w") as f:
            f.write(content)
        return path

    def test_parse_render_gate_account_derive_status_via_cli(self):
        import tempfile
        with tempfile.TemporaryDirectory() as tmp:
            body_path = self.write(tmp, "body.md", LEGACY_BODY)
            rc, out, err = run_cli(["parse", "--body-file", body_path])
            self.assertEqual(rc, 0, err)
            data = json.loads(out)
            self.assertIn("sections", data)
            self.assertIn("items", data)

            json_path = self.write(tmp, "in.json", json.dumps({"sections": data["sections"]}))
            rc, out, err = run_cli(["render", "--json-file", json_path])
            self.assertEqual(rc, 0, err)
            self.assertEqual(out, LEGACY_BODY.rstrip("\n"))

            rc, out, err = run_cli(["account", "--body-file", body_path])
            self.assertEqual(rc, 0, err)

            rc, out, err = run_cli(["gate", "--body-file", body_path, "--from", "Empty", "--to", "Planning"])
            self.assertEqual(rc, 0, err)

            rc, out, err = run_cli(["derive-status", "--body-file", body_path, "--current", "Doing"])
            self.assertEqual(rc, 0, err)
            self.assertEqual(out.strip(), "Planning")

    def test_item_add_and_item_close_via_cli(self):
        import tempfile
        with tempfile.TemporaryDirectory() as tmp:
            body_path = self.write(tmp, "body.md", LEGACY_BODY)
            rc, out, err = run_cli([
                "item-add", "--body-file", body_path, "--section", "How", "--text", "do the thing",
            ])
            self.assertEqual(rc, 0, err)
            self.assertIn("[T1] do the thing", out)

            new_body_path = self.write(tmp, "body2.md", out)
            rc, out2, err = run_cli([
                "item-close", "--body-file", new_body_path, "--section", "How",
                "--id", "1", "--state", "x", "--note", "shipped",
            ])
            self.assertEqual(rc, 0, err)
            self.assertIn("[x] [T1] do the thing", out2)
            self.assertIn("resolved: shipped", out2)

    def test_item_add_facts_requires_evidence(self):
        import tempfile
        with tempfile.TemporaryDirectory() as tmp:
            body_path = self.write(tmp, "body.md", LEGACY_BODY)
            rc, out, err = run_cli([
                "item-add", "--body-file", body_path, "--section", "Facts", "--text", "a bare claim",
            ])
            self.assertEqual(rc, 1)
            rc, out, err = run_cli([
                "item-add", "--body-file", body_path, "--section", "Facts", "--text", "a claim",
                "--evidence", "see `foo.py`",
            ])
            self.assertEqual(rc, 0, err)
            self.assertIn("[F1] a claim", out)

    def test_has_evidence_cli_exit_codes(self):
        rc, out, err = run_cli(["has-evidence", "--text", "see `foo.py`"])
        self.assertEqual(rc, 0)
        rc, out, err = run_cli(["has-evidence", "--text", "bare claim"])
        self.assertEqual(rc, 1)

    def test_item_close_unknown_id_exits_one(self):
        import tempfile
        with tempfile.TemporaryDirectory() as tmp:
            body_path = self.write(tmp, "body.md", LEGACY_BODY)
            rc, out, err = run_cli([
                "item-close", "--body-file", body_path, "--section", "How",
                "--id", "99", "--state", "x",
            ])
            self.assertEqual(rc, 1)

    def test_item_close_non_checkbox_section_exits_one(self):
        import tempfile
        with tempfile.TemporaryDirectory() as tmp:
            body_path = self.write(tmp, "body.md", LEGACY_BODY)
            rc, out, err = run_cli([
                "item-close", "--body-file", body_path, "--section", "Facts",
                "--id", "1", "--state", "x",
            ])
            self.assertEqual(rc, 1)


TASK_BODY = (
    "### Title\n\nTasks\n\n### Assigned\n\nDylan\n\n### Parent\n\nnone\n\n"
    "### Blockers\n\nnone\n\n### Why\n\nwhy\n\n### What\n\nwhat\n\n"
    "### Indicators\n\nind\n\n### Gaps\n\nnone\n\n### Where\n\nwhere\n\n"
    "### Facts\n\nnone\n\n### Unknowns\n\nnone\n\n### How\n\n"
    "- [ ] [T1] build the thing " + es.EMDASH + " blockers: none; indicators: it builds\n"
    "- [ ] [T2] test the thing " + es.EMDASH + " blockers: T1; indicators: it passes\n\n"
    "### Tags\n\ntags\n"
)


class ClosingAnItemKeepsItsMetadata(unittest.TestCase):
    def close(self, ident, extra=None):
        with tempfile.TemporaryDirectory() as d:
            path = os.path.join(d, "body.md")
            with open(path, "w") as f:
                f.write(TASK_BODY)
            args = ["item-close", "--body-file", path, "--section", "How",
                    "--id", ident, "--state", "x"]
            if extra:
                args += extra
            rc, out, err = run_cli(args)
            self.assertEqual(rc, 0, err)
            return out

    def test_trailing_metadata_survives_closing(self):
        out = self.close("T1")
        how = es.parse(out)["sections"]["How"]
        self.assertIn("blockers: none; indicators: it builds", how)
        self.assertTrue(how.startswith("- [x] [T1]"))

    def test_note_is_appended_beside_the_metadata(self):
        out = self.close("T1", ["--note", "landed on main"])
        how = es.parse(out)["sections"]["How"]
        self.assertIn("blockers: none; indicators: it builds", how)
        self.assertIn("resolved: landed on main", how)

    def test_sibling_item_is_untouched(self):
        out = self.close("T1")
        how = es.parse(out)["sections"]["How"]
        self.assertIn(
            "- [ ] [T2] test the thing " + es.EMDASH + " blockers: T1; indicators: it passes",
            how,
        )

    def test_every_other_section_is_byte_identical(self):
        out = self.close("T1")
        before = es.parse(TASK_BODY)["sections"]
        after = es.parse(out)["sections"]
        for name in before:
            if name != "How":
                self.assertEqual(before[name], after[name], name)


class ItemIdsAcceptTheirRenderedForm(unittest.TestCase):
    def test_prefixed_and_bracketed_and_bare_all_resolve(self):
        for ident in ("T1", "t1", "[T1]", "1"):
            self.assertEqual(es.item_id_number(ident), 1)

    def test_a_non_id_is_rejected(self):
        for bad in ("", "T", "abc", "T1x"):
            with self.assertRaises(argparse.ArgumentTypeError):
                es.item_id_number(bad)

    def test_cli_accepts_the_rendered_id(self):
        with tempfile.TemporaryDirectory() as d:
            path = os.path.join(d, "body.md")
            with open(path, "w") as f:
                f.write(TASK_BODY)
            rc, out, err = run_cli([
                "item-close", "--body-file", path, "--section", "How",
                "--id", "T2", "--state", "~", "--note", "not needed",
            ])
            self.assertEqual(rc, 0, err)
            self.assertIn("- [~] [T2]", es.parse(out)["sections"]["How"])


class EvidenceRejectsAnIncidentalSlash(unittest.TestCase):
    def test_a_bare_claim_is_not_evidence(self):
        for text in (
            "this works 9/10 times, trust me",
            "and/or mostly right",
            "I am confident this is fine",
            "on 9/22 probably",
            "it should be fine either/or",
            "roughly 3/4 of the time",
        ):
            self.assertFalse(es.has_evidence(text), text)

    def test_something_you_can_point_at_is_evidence(self):
        for text in (
            "builder-sb/policy/scripts/get-effort.sh",
            "scripts/get-effort.sh",
            "create-effort.sh --how with --dry-run exits 1",
            "`git log --oneline`",
            "https://github.com/simplesalt/projects/issues/530",
            "projects#530",
            "verified by a delegate worker",
            "evidence: the run output",
        ):
            self.assertTrue(es.has_evidence(text), text)


class EveryDenialNamesAProcedureStep(unittest.TestCase):
    def test_no_violation_leaves_the_reader_without_a_next_step(self):
        filled = sections_with({
            "Why": "w", "What": "w", "Where": "w",
            "Indicators": "i", "How": "- [x] [T1] done",
        })
        cases = [
            (sections_with({}), "Empty", "Planning", {}),
            (sections_with({"Why": "w", "What": "w", "Where": "w",
                            "How": "- [ ] [T1] plan"}), "Empty", "Planning", {}),
            (sections_with({"Gaps": "- [ ] [G1] open",
                            "Unknowns": "- [ ] [U1] open"}), "Planning", "Doing", {}),
            (filled, "Doing", "Blocked", {}),
            (filled, "Doing", "Review", {"blockers": 2}),
            (filled, "Review", "Done", {"blockers": 2}),
            (filled, "Review", "Done", {"open_children": 3}),
            (sections_with({"Gaps": "- [ ] [G1] open", "How": "- [ ] [T1] open"}),
             "Review", "Done", {}),
            (filled, "Doing", "Done", {}),
            (filled, "Planning", "Done", {}),
            (filled, "Review", "Doing", {}),
            (filled, "Doing", "Planning", {}),
            (filled, "Planning", "Review", {}),
            (filled, "Planning", "Blocked", {}),
            (filled, "Empty", "Doing", {}),
            (filled, "Paused", "Doing", {}),
            (filled, "Done", "Doing", {}),
            (filled, "Cancelled", "Planning", {}),
        ]
        seen = 0
        for sections, frm, to, kw in cases:
            for violation in es.gate(sections, frm, to, **kw):
                seen += 1
                self.assertRegex(violation, r"step \d", violation)
        self.assertGreater(seen, 18)

    def test_every_refused_move_names_a_step(self):
        refused = 0
        for frm in es.STATUSES:
            for to in es.STATUSES:
                message = es.move_violation(frm, to)
                if message is None:
                    continue
                refused += 1
                self.assertRegex(message, r"step \d", message)
        self.assertGreater(refused, 0)

    def test_blocker_edge_refusal_names_a_step(self):
        message = es.blocker_edge_refusal(PARENT_REF, "Deferred", PARENT_REF)
        self.assertRegex(message, r"step \d")

    def test_done_redirect_without_children_names_a_step(self):
        with self.assertRaises(ValueError) as caught:
            es.done_redirect(body_with({}), [], [])
        self.assertRegex(str(caught.exception), r"step \d")


class HowTaskAdditionRequiresDoing(unittest.TestCase):
    def write(self, tmpdir, name, content):
        path = os.path.join(tmpdir, name)
        with open(path, "w") as f:
            f.write(content)
        return path

    def test_add_how_item_refused_when_status_not_doing(self):
        with tempfile.TemporaryDirectory() as tmp:
            body_path = self.write(tmp, "body.md", LEGACY_BODY)
            rc, out, err = run_cli([
                "item-add", "--body-file", body_path, "--section", "How",
                "--text", "do the thing", "--current-status", "Planning",
            ])
            self.assertEqual(rc, 1)
            self.assertIn("Doing", err)

    def test_add_how_item_succeeds_when_status_doing(self):
        with tempfile.TemporaryDirectory() as tmp:
            body_path = self.write(tmp, "body.md", LEGACY_BODY)
            rc, out, err = run_cli([
                "item-add", "--body-file", body_path, "--section", "How",
                "--text", "do the thing", "--current-status", "Doing",
            ])
            self.assertEqual(rc, 0, err)
            self.assertIn("[T1] do the thing", out)

    def test_add_how_item_unaffected_when_status_not_supplied(self):
        with tempfile.TemporaryDirectory() as tmp:
            body_path = self.write(tmp, "body.md", LEGACY_BODY)
            rc, out, err = run_cli([
                "item-add", "--body-file", body_path, "--section", "How",
                "--text", "do the thing",
            ])
            self.assertEqual(rc, 0, err)
            self.assertIn("[T1] do the thing", out)

    def test_add_other_section_ignores_current_status(self):
        with tempfile.TemporaryDirectory() as tmp:
            body_path = self.write(tmp, "body.md", LEGACY_BODY)
            rc, out, err = run_cli([
                "item-add", "--body-file", body_path, "--section", "Unknowns",
                "--text", "something unclear", "--current-status", "Planning",
            ])
            self.assertEqual(rc, 0, err)
            self.assertIn("[U1] something unclear", out)

    def test_close_how_item_still_works_from_non_doing_status(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = self.write(tmp, "body.md", TASK_BODY)
            rc, out, err = run_cli([
                "item-close", "--body-file", path, "--section", "How",
                "--id", "T1", "--state", "x",
            ])
            self.assertEqual(rc, 0, err)
            self.assertIn("[x] [T1]", es.parse(out)["sections"]["How"])


class OneGrammarInTheRepo(unittest.TestCase):
    def test_no_shell_script_reimplements_the_heading_grammar(self):
        offenders = []
        for name in os.listdir(SCRIPT_DIR):
            if not name.endswith(".sh"):
                continue
            with open(os.path.join(SCRIPT_DIR, name)) as f:
                text = f.read()
            if "`{3,}" in text or "identity_sections" in text:
                offenders.append(name)
        self.assertEqual(offenders, [])


PARENT_REF = "simplesalt/projects#700"
CHILD_REF = "simplesalt/projects#710"
OTHER_REF = "simplesalt/projects#711"
CHILD_TITLE = "Session scripts carry out Deferred"
DEFER_GAP = (
    "simplesalt/projects#710 (Session scripts carry out Deferred) is deferred: "
    "re-plan without it, or pick it back up"
)


def body_with(overrides):
    base = {
        "Title": "Parent", "Assigned": "Dylan", "Why": "why", "What": "what",
        "Indicators": "ind", "Where": "where", "Tags": "tags",
    }
    base.update(overrides)
    return es.render(sections_with(base))


def how_with(*lines):
    return "\n".join(lines)


def pointer_line(mark, ident, ref=CHILD_REF):
    return (
        f"- [{mark}] [T{ident}] Session scripts carry out Deferred " + es.EMDASH +
        f" {ref} " + es.EMDASH + " blockers: none; indicators: it works"
    )


def write_file(tmpdir, name, content):
    path = os.path.join(tmpdir, name)
    with open(path, "w") as f:
        f.write(content)
    return path


def other_sections_identical(test, before_body, after_body, changed):
    before = es.parse(before_body)["sections"]
    after = es.parse(after_body)["sections"]
    for name in before:
        if name not in changed:
            test.assertEqual(before[name], after[name], name)


class DeferredStatus(unittest.TestCase):
    def test_deferred_follows_paused_in_statuses(self):
        self.assertIn("Deferred", es.STATUSES)
        self.assertEqual(es.STATUSES.index("Deferred"), es.STATUSES.index("Paused") + 1)

    def test_gate_imposes_nothing_on_a_move_to_deferred(self):
        loaded = sections_with({
            "Gaps": "- [ ] [G1] open", "Unknowns": "- [ ] [U1] open",
            "How": "- [ ] [T1] open",
        })
        for frm in ("Empty", "Planning", "Doing", "Blocked", "Review", "Paused"):
            self.assertEqual(es.gate(sections_with({}), frm, "Deferred"), [], frm)
            self.assertEqual(
                es.gate(loaded, frm, "Deferred", open_children=2, blockers=3), [], frm
            )

    def test_derive_status_leaves_deferred_deferred(self):
        gapped = sections_with({"Gaps": "- [ ] [G1] open"})
        self.assertEqual(es.derive_status(sections_with({}), "Deferred"), "Deferred")
        self.assertEqual(es.derive_status(gapped, "Deferred"), "Deferred")
        self.assertEqual(es.derive_status(gapped, "Deferred", blockers=2), "Deferred")

    def test_derive_status_cli_leaves_deferred_deferred(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = write_file(tmp, "body.md", body_with({"Gaps": "- [ ] [G1] open"}))
            rc, out, err = run_cli([
                "derive-status", "--body-file", path, "--current", "Deferred",
            ])
            self.assertEqual(rc, 0, err)
            self.assertEqual(out.strip(), "Deferred")

    def test_gate_cli_allows_a_move_to_deferred(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = write_file(tmp, "body.md", body_with({"Gaps": "- [ ] [G1] open"}))
            rc, out, err = run_cli([
                "gate", "--body-file", path, "--from", "Doing", "--to", "Deferred",
            ])
            self.assertEqual(rc, 0, err)
            self.assertEqual(out, "")


ALLOWED_MOVES = [
    ("Empty", "Planning"),
    ("Planning", "Doing"),
    ("Doing", "Blocked"),
    ("Blocked", "Doing"),
    ("Doing", "Review"),
    ("Review", "Done"),
    ("Review", "Planning"),
    ("Planning", "Paused"),
    ("Doing", "Paused"),
    ("Review", "Paused"),
    ("Blocked", "Paused"),
    ("Paused", "Planning"),
    ("Planning", "Cancelled"),
    ("Doing", "Cancelled"),
    ("Review", "Cancelled"),
    ("Paused", "Cancelled"),
    ("Blocked", "Cancelled"),
]
ALLOWED_MOVES += [(s, "Deferred") for s in es.STATUSES if s != "Deferred"]
ALLOWED_MOVES += [("Deferred", s) for s in es.STATUSES if s not in ("Empty", "Deferred")]
ALLOWED_MOVES += [(s, s) for s in es.STATUSES]

REFUSED_MOVES = [
    ("Doing", "Done"),
    ("Planning", "Done"),
    ("Blocked", "Done"),
    ("Paused", "Done"),
    ("Empty", "Done"),
    ("Review", "Doing"),
    ("Planning", "Review"),
    ("Blocked", "Review"),
    ("Paused", "Review"),
    ("Doing", "Planning"),
    ("Blocked", "Planning"),
    ("Planning", "Blocked"),
    ("Review", "Blocked"),
    ("Paused", "Doing"),
    ("Empty", "Doing"),
    ("Empty", "Paused"),
    ("Empty", "Cancelled"),
] + [
    (frm, to)
    for frm in ("Done", "Cancelled")
    for to in es.STATUSES
    if to not in (frm, "Deferred")
]


def move_refusals(frm, to, **kw):
    return [
        m for m in es.gate(sections_with({}), frm, to, **kw) if "is not a move" in m
    ]


class RequestedMoves(unittest.TestCase):
    def test_every_allowed_move_passes_the_table(self):
        for frm, to in ALLOWED_MOVES:
            self.assertTrue(es.move_allowed(frm, to), (frm, to))
            self.assertIsNone(es.move_violation(frm, to), (frm, to))
            self.assertEqual(move_refusals(frm, to), [], (frm, to))

    def test_every_notable_move_outside_the_table_is_refused(self):
        for frm, to in REFUSED_MOVES:
            self.assertFalse(es.move_allowed(frm, to), (frm, to))
            self.assertEqual(len(move_refusals(frm, to)), 1, (frm, to))

    def test_the_table_allows_nothing_beyond_the_listed_moves(self):
        listed = set(ALLOWED_MOVES)
        for frm in es.STATUSES:
            for to in es.STATUSES:
                self.assertEqual(es.move_allowed(frm, to), (frm, to) in listed, (frm, to))

    def test_requesting_the_current_status_again_is_allowed(self):
        for status in es.STATUSES:
            self.assertEqual(move_refusals(status, status), [], status)

    def test_deferred_moves_to_and_from_any_status(self):
        for status in es.STATUSES:
            self.assertEqual(move_refusals(status, "Deferred"), [], status)
            if status != "Empty":
                self.assertEqual(move_refusals("Deferred", status), [], status)

    def test_an_unset_status_is_treated_as_empty(self):
        self.assertEqual(len(es.gate(sections_with({}), None, "Doing")), 2)
        self.assertEqual(move_refusals("", "Planning"), [])
        self.assertEqual(len(move_refusals("", "Doing")), 1)

    def test_an_unknown_current_status_has_no_moves(self):
        self.assertEqual(len(move_refusals("Elsewhere", "Planning")), 1)
        self.assertIn("the moves are none", es.move_violation("Elsewhere", "Planning"))
        self.assertEqual(move_refusals("Elsewhere", "Elsewhere"), [])

    def test_a_refusal_names_the_move_and_lists_the_moves_from_the_status(self):
        message = es.move_violation("Doing", "Done")
        self.assertIn("Doing -> Done", message)
        self.assertIn("Blocked, Review, Paused, Cancelled, Deferred", message)

    def test_doing_to_done_says_to_set_review_first(self):
        message = es.move_violation("Doing", "Done")
        self.assertIn("set Review first at step 3 Review", message)

    def test_doing_to_planning_says_to_add_a_gap(self):
        message = es.move_violation("Doing", "Planning")
        self.assertIn("adding a Gap in step 1 Design", message)

    def test_review_to_doing_says_to_set_planning(self):
        message = es.move_violation("Review", "Doing")
        self.assertIn("set Planning", message)
        self.assertIn("step 2 Deliver", message)

    def test_reopening_a_finished_effort_needs_a_person(self):
        for status in ("Done", "Cancelled"):
            message = es.move_violation(status, "Doing")
            self.assertIn("a person's direction", message)
            self.assertIn("--force", message)
            self.assertIn("step 1 Design", message)

    def test_a_refusal_comes_with_the_precondition_checks(self):
        v = es.gate(sections_with({"Gaps": "- [ ] [G1] open"}), "Doing", "Done")
        self.assertEqual(len(v), 2)
        self.assertIn("is not a move", v[0])
        self.assertIn("zero open Gaps", v[1])

    def test_gate_cli_refuses_a_move_outside_the_table(self):
        body = body_with({"How": "- [x] [T1] done"})
        with tempfile.TemporaryDirectory() as tmp:
            path = write_file(tmp, "body.md", body)
            for frm, to in (("Doing", "Done"), ("Review", "Doing"), ("Done", "Doing")):
                rc, out, err = run_cli([
                    "gate", "--body-file", path, "--from", frm, "--to", to,
                ])
                self.assertEqual(rc, 1, (frm, to, err))
                self.assertIn(f"{frm} -> {to} is not a move", out)

    def test_gate_cli_allows_a_move_in_the_table(self):
        body = body_with({"How": "- [x] [T1] done"})
        with tempfile.TemporaryDirectory() as tmp:
            path = write_file(tmp, "body.md", body)
            for frm, to in (
                ("Review", "Done"), ("Planning", "Deferred"), ("Deferred", "Doing"),
                ("Doing", "Doing"),
            ):
                rc, out, err = run_cli([
                    "gate", "--body-file", path, "--from", frm, "--to", to,
                ])
                self.assertEqual(rc, 0, (frm, to, out, err))
                self.assertEqual(out, "")


class DeferredHowMark(unittest.TestCase):
    HOW = how_with(
        pointer_line("-", 1),
        pointer_line(" ", 2, OTHER_REF),
        pointer_line("x", 3, "simplesalt/projects#712"),
    )

    def test_mark_state_maps_the_dash_to_deferred(self):
        self.assertEqual(es.MARK_STATE["-"], "deferred")

    def test_deferred_task_parses_with_its_state_and_metadata(self):
        records = es.parse_section_lines("How", self.HOW)
        self.assertEqual(records[0]["state"], "deferred")
        self.assertEqual(records[0]["id"], 1)
        self.assertFalse(records[0]["legacy"])
        self.assertEqual(records[0]["mark"], "-")
        self.assertIn(CHILD_REF, records[0]["trailing"])
        items = es.parse(body_with({"How": self.HOW}))["items"]["How"]
        self.assertEqual([i["state"] for i in items], ["deferred", "open", "closed"])

    def test_round_trips_byte_identically(self):
        body = body_with({"How": self.HOW})
        self.assertEqual(es.render(es.parse(body)["sections"]), body)
        records = es.parse_section_lines("How", self.HOW)
        self.assertEqual(es.render_section_from_records("How", records), self.HOW)

    def test_open_item_count_ignores_deferred(self):
        only_deferred = {"How": pointer_line("-", 1)}
        self.assertEqual(es.open_item_count(only_deferred, "How"), 0)
        self.assertEqual(es.open_item_count({"How": self.HOW}, "How"), 1)

    def test_a_deferred_task_does_not_stop_review(self):
        sections = sections_with({
            "How": how_with(pointer_line("x", 1), pointer_line("-", 2, OTHER_REF)),
        })
        self.assertEqual(es.gate(sections, "Doing", "Review"), [])

    def test_a_deferred_task_alone_satisfies_review_how_nonempty(self):
        sections = sections_with({"How": pointer_line("-", 1)})
        self.assertEqual(es.gate(sections, "Doing", "Review"), [])

    def test_a_deferred_task_does_not_stop_done(self):
        sections = sections_with({
            "How": how_with(pointer_line("x", 1), pointer_line("-", 2, OTHER_REF)),
        })
        self.assertEqual(es.gate(sections, "Review", "Done"), [])

    def test_an_open_task_still_stops_review_beside_a_deferred_one(self):
        sections = sections_with({
            "How": how_with(pointer_line("-", 1), pointer_line(" ", 2, OTHER_REF)),
        })
        self.assertEqual(len(es.gate(sections, "Doing", "Review")), 1)

    def test_gate_cli_passes_review_with_a_deferred_task(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = write_file(tmp, "body.md", body_with({"How": self.HOW.replace("[ ]", "[x]")}))
            rc, out, err = run_cli([
                "gate", "--body-file", path, "--from", "Doing", "--to", "Review",
            ])
            self.assertEqual(rc, 0, out + err)

    def test_item_add_after_a_deferred_task_allocates_the_next_id(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = write_file(tmp, "body.md", body_with({"How": self.HOW}))
            rc, out, err = run_cli([
                "item-add", "--body-file", path, "--section", "How", "--text", "next",
            ])
            self.assertEqual(rc, 0, err)
            how = es.parse(out)["sections"]["How"]
            self.assertTrue(how.startswith(self.HOW))
            self.assertTrue(how.endswith("- [ ] [T4] next"))


class GapTitlesDropTheTypePrefix(unittest.TestCase):
    def test_the_issue_type_prefix_is_dropped(self):
        self.assertEqual(es.one_line("[Feature] deferred-status: backlog"), "deferred-status: backlog")
        self.assertEqual(es.one_line("plain [x] title"), "plain [x] title")
        self.assertEqual(es.one_line("[Bug] two\nlines"), "two lines")


class FindPointerTask(unittest.TestCase):
    def records(self, *lines):
        return es.parse_section_lines("How", how_with(*lines))

    def test_matches_the_ref_in_the_trailing(self):
        records = self.records(
            "- [ ] [T1] unrelated " + es.EMDASH + " blockers: none",
            pointer_line(" ", 2),
        )
        self.assertEqual(es.find_pointer_task(records, CHILD_REF)["id"], 2)

    def test_matches_the_ref_in_the_text(self):
        records = self.records(f"- [ ] [T1] Carry out {CHILD_REF} when it lands")
        self.assertEqual(es.find_pointer_task(records, CHILD_REF)["id"], 1)

    def test_returns_none_when_nothing_names_the_child(self):
        records = self.records(pointer_line(" ", 1, OTHER_REF), "- [ ] [T2] plain")
        self.assertIsNone(es.find_pointer_task(records, CHILD_REF))
        self.assertIsNone(es.find_pointer_task([], CHILD_REF))

    def test_returns_the_first_match(self):
        records = self.records(
            pointer_line("x", 1), pointer_line(" ", 2), pointer_line(" ", 3, OTHER_REF),
        )
        self.assertEqual(es.find_pointer_task(records, CHILD_REF)["id"], 1)

    def test_a_longer_number_is_not_a_match(self):
        records = self.records(pointer_line(" ", 1, "simplesalt/projects#7100"))
        self.assertIsNone(es.find_pointer_task(records, CHILD_REF))
        records = self.records(pointer_line(" ", 1, CHILD_REF))
        self.assertIsNone(es.find_pointer_task(records, "simplesalt/projects#71"))
        self.assertIsNone(es.find_pointer_task(records, "simplesalt/projects#7"))

    def test_a_ref_preceded_by_a_word_character_or_slash_is_not_a_match(self):
        for ref in ("xsimplesalt/projects#710", "other/simplesalt/projects#710"):
            records = self.records(pointer_line(" ", 1, ref))
            self.assertIsNone(es.find_pointer_task(records, CHILD_REF), ref)

    def test_a_bare_issue_number_is_not_a_match(self):
        records = self.records(pointer_line(" ", 1, "#710"))
        self.assertIsNone(es.find_pointer_task(records, CHILD_REF))

    def test_punctuation_around_the_ref_still_matches(self):
        for wrapped in (f"({CHILD_REF})", f"{CHILD_REF},", f"{CHILD_REF}.", f"[{CHILD_REF}]"):
            records = self.records(pointer_line(" ", 1, wrapped))
            self.assertEqual(es.find_pointer_task(records, CHILD_REF)["id"], 1, wrapped)

    def test_the_ref_at_the_very_end_of_the_text_matches(self):
        records = self.records(f"- [ ] [T1] Carry out {CHILD_REF}")
        self.assertEqual(es.find_pointer_task(records, CHILD_REF)["id"], 1)

    def test_legacy_lines_are_skipped(self):
        records = es.parse_section_lines(
            "How", f"- freeform note about {CHILD_REF}\nfree prose {CHILD_REF}\n" + pointer_line(" ", 4),
        )
        self.assertEqual(es.find_pointer_task(records, CHILD_REF)["id"], 4)
        legacy_only = es.parse_section_lines("How", f"- freeform note about {CHILD_REF}")
        self.assertIsNone(es.find_pointer_task(legacy_only, CHILD_REF))

    def test_blank_records_are_skipped(self):
        records = self.records(pointer_line("-", 1), "", pointer_line(" ", 2, OTHER_REF))
        self.assertEqual(es.find_pointer_task(records, CHILD_REF)["id"], 1)
        self.assertEqual(es.find_pointer_task(records, OTHER_REF)["id"], 2)

    def test_a_task_in_any_state_is_found(self):
        for mark in (" ", "x", "~", "-"):
            records = self.records(pointer_line(mark, 1))
            self.assertEqual(es.find_pointer_task(records, CHILD_REF)["id"], 1, mark)

    def test_a_task_led_by_the_ref_wins_over_an_earlier_mention(self):
        records = self.records(
            f"- [ ] [T1] Analysis: check {CHILD_REF} behaves " + es.EMDASH + " blockers: T2",
            pointer_line(" ", 2),
        )
        self.assertEqual(es.find_pointer_task(records, CHILD_REF)["id"], 2)


class DeferParent(unittest.TestCase):
    def parent(self, how=None, gaps=None):
        overrides = {"How": how if how is not None else how_with(
            pointer_line(" ", 1, OTHER_REF), pointer_line(" ", 2), "- [ ] [T3] unrelated",
        )}
        if gaps is not None:
            overrides["Gaps"] = gaps
        return body_with(overrides)

    def defer(self, body, current="Doing", blockers=0):
        return es.defer_parent(body, CHILD_REF, CHILD_TITLE, current, blockers)

    def test_result_has_exactly_the_documented_keys(self):
        result = self.defer(self.parent())
        self.assertEqual(set(result), {"body", "status", "gap_added", "task"})

    def test_adds_the_exact_gap_text_as_an_open_gap(self):
        result = self.defer(self.parent())
        self.assertTrue(result["gap_added"])
        gaps = es.parse(result["body"])["items"]["Gaps"]
        self.assertEqual(len(gaps), 1)
        self.assertEqual(gaps[0]["text"], DEFER_GAP)
        self.assertEqual(gaps[0]["state"], "open")
        self.assertEqual(gaps[0]["id"], 1)
        self.assertEqual(es.parse(result["body"])["sections"]["Gaps"], f"- [ ] [G1] {DEFER_GAP}")

    def test_gap_id_continues_after_existing_gaps(self):
        gaps = "- [x] [G1] old — resolved: done\n- [ ] [G4] other question"
        result = self.defer(self.parent(gaps=gaps))
        section = es.parse(result["body"])["sections"]["Gaps"]
        self.assertEqual(section, gaps + f"\n- [ ] [G5] {DEFER_GAP}")

    def test_marks_the_pointer_task_deferred(self):
        result = self.defer(self.parent())
        self.assertEqual(result["task"], "T2")
        how = es.parse(result["body"])["sections"]["How"]
        self.assertEqual(how, how_with(
            pointer_line(" ", 1, OTHER_REF), pointer_line("-", 2), "- [ ] [T3] unrelated",
        ))

    def test_untouched_sections_stay_byte_identical(self):
        body = self.parent()
        result = self.defer(body)
        other_sections_identical(self, body, result["body"], {"Gaps", "How"})

    def test_a_task_naming_another_child_is_left_alone(self):
        result = self.defer(self.parent(how=pointer_line(" ", 1, OTHER_REF)))
        self.assertIsNone(result["task"])
        self.assertEqual(
            es.parse(result["body"])["sections"]["How"], pointer_line(" ", 1, OTHER_REF)
        )

    def test_status_per_parent_status(self):
        cases = [
            ("Doing", 0, "Planning"),
            ("Doing", 2, "Planning"),
            ("Blocked", 1, "Planning"),
            ("Review", 0, "Planning"),
            ("Planning", 0, "Planning"),
            ("Paused", 0, "Paused"),
            ("Deferred", 0, "Deferred"),
        ]
        for current, blockers, expected in cases:
            result = self.defer(self.parent(), current, blockers)
            self.assertEqual(result["status"], expected, current)

    def test_status_when_a_gap_about_the_child_is_already_open(self):
        body = self.parent(gaps=f"- [ ] [G1] {DEFER_GAP}")
        for current, expected in (("Doing", "Planning"), ("Blocked", "Planning"),
                                  ("Review", "Planning"), ("Paused", "Paused")):
            self.assertEqual(self.defer(body, current, 1)["status"], expected, current)

    def test_no_duplicate_gap_when_one_is_already_open(self):
        gaps = f"- [ ] [G1] {DEFER_GAP}"
        body = self.parent(gaps=gaps)
        result = self.defer(body)
        self.assertFalse(result["gap_added"])
        self.assertEqual(es.parse(result["body"])["sections"]["Gaps"], gaps)
        self.assertEqual(result["task"], "T2")

    def test_an_open_gap_with_its_own_wording_also_counts(self):
        gaps = f"- [ ] [G1] Is {CHILD_REF} Deferred for good?"
        result = self.defer(self.parent(gaps=gaps))
        self.assertFalse(result["gap_added"])
        self.assertEqual(es.parse(result["body"])["sections"]["Gaps"], gaps)

    def test_a_closed_gap_does_not_suppress_a_new_one(self):
        gaps = f"- [x] [G1] {DEFER_GAP} " + es.EMDASH + " resolved: re-planned"
        result = self.defer(self.parent(gaps=gaps))
        self.assertTrue(result["gap_added"])
        self.assertEqual(len(es.parse(result["body"])["items"]["Gaps"]), 2)

    def test_an_open_gap_naming_another_child_does_not_suppress(self):
        gaps = f"- [ ] [G1] {OTHER_REF} is deferred: re-plan"
        result = self.defer(self.parent(gaps=gaps))
        self.assertTrue(result["gap_added"])

    def test_an_open_gap_with_a_longer_number_does_not_suppress(self):
        gaps = "- [ ] [G1] simplesalt/projects#7100 is deferred: re-plan"
        result = self.defer(self.parent(gaps=gaps))
        self.assertTrue(result["gap_added"])

    def test_an_open_gap_naming_the_child_without_deferred_does_not_suppress(self):
        gaps = f"- [ ] [G1] Which order for {CHILD_REF}?"
        result = self.defer(self.parent(gaps=gaps))
        self.assertTrue(result["gap_added"])

    def test_done_cancelled_and_deferred_pointer_tasks_are_left_alone(self):
        for mark in ("x", "~", "-"):
            how = pointer_line(mark, 1)
            result = self.defer(self.parent(how=how))
            self.assertIsNone(result["task"], mark)
            self.assertEqual(es.parse(result["body"])["sections"]["How"], how, mark)
            self.assertTrue(result["gap_added"], mark)

    def test_no_pointer_task_is_fine(self):
        for how in ("", "- [ ] [T1] unrelated"):
            result = self.defer(self.parent(how=how))
            self.assertIsNone(result["task"])
            self.assertTrue(result["gap_added"])
            self.assertEqual(
                es.parse(result["body"])["sections"]["How"], how if how else "none"
            )

    def test_deferring_twice_changes_nothing_the_second_time(self):
        first = self.defer(self.parent())
        second = self.defer(first["body"])
        self.assertFalse(second["gap_added"])
        self.assertIsNone(second["task"])
        self.assertEqual(second["body"], first["body"])

    def test_a_multi_line_title_stays_on_one_line(self):
        result = es.defer_parent(self.parent(), CHILD_REF, "two\nlines", "Doing", 0)
        self.assertEqual(len(es.parse(result["body"])["items"]["Gaps"]), 1)
        self.assertIn(f"{CHILD_REF} (two lines) is deferred", result["body"])

    def test_cli_prints_the_result_as_json(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = write_file(tmp, "body.md", self.parent())
            rc, out, err = run_cli([
                "defer-parent", "--body-file", path, "--child", CHILD_REF,
                "--child-title", CHILD_TITLE, "--current", "Doing", "--blockers", "1",
            ])
            self.assertEqual(rc, 0, err)
            result = json.loads(out)
            self.assertEqual(set(result), {"body", "status", "gap_added", "task"})
            self.assertEqual(result["status"], "Planning")
            self.assertTrue(result["gap_added"])
            self.assertEqual(result["task"], "T2")
            self.assertEqual(result, self.defer(self.parent(), "Doing", 1))

    def test_cli_blockers_defaults_to_zero(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = write_file(tmp, "body.md", self.parent())
            rc, out, err = run_cli([
                "defer-parent", "--body-file", path, "--child", CHILD_REF,
                "--child-title", CHILD_TITLE, "--current", "Paused",
            ])
            self.assertEqual(rc, 0, err)
            self.assertEqual(json.loads(out)["status"], "Paused")


class UndeferParent(unittest.TestCase):
    def parent(self, how):
        return body_with({"How": how})

    def test_a_deferred_pointer_task_goes_back_to_open(self):
        how = how_with(pointer_line(" ", 1, OTHER_REF), pointer_line("-", 2), "- [x] [T3] done")
        body = self.parent(how)
        result = es.undefer_parent(body, CHILD_REF)
        self.assertEqual(set(result), {"body", "task"})
        self.assertEqual(result["task"], "T2")
        self.assertEqual(
            es.parse(result["body"])["sections"]["How"],
            how_with(pointer_line(" ", 1, OTHER_REF), pointer_line(" ", 2), "- [x] [T3] done"),
        )
        other_sections_identical(self, body, result["body"], {"How"})

    def test_nothing_else_changes_in_the_body(self):
        body = self.parent(pointer_line("-", 1))
        result = es.undefer_parent(body, CHILD_REF)
        self.assertEqual(result["body"], self.parent(pointer_line(" ", 1)))

    def test_an_open_pointer_task_is_unchanged(self):
        body = self.parent(pointer_line(" ", 1))
        result = es.undefer_parent(body, CHILD_REF)
        self.assertIsNone(result["task"])
        self.assertEqual(result["body"], body)

    def test_done_and_cancelled_pointer_tasks_are_unchanged(self):
        for mark in ("x", "~"):
            body = self.parent(pointer_line(mark, 1))
            result = es.undefer_parent(body, CHILD_REF)
            self.assertIsNone(result["task"], mark)
            self.assertEqual(result["body"], body, mark)

    def test_no_pointer_task_is_fine(self):
        for how in ("", "- [-] [T1] deferred but unrelated"):
            body = self.parent(how)
            result = es.undefer_parent(body, CHILD_REF)
            self.assertIsNone(result["task"])
            self.assertEqual(result["body"], body)

    def test_another_childs_deferred_task_is_left_alone(self):
        body = self.parent(pointer_line("-", 1, OTHER_REF))
        result = es.undefer_parent(body, CHILD_REF)
        self.assertIsNone(result["task"])
        self.assertEqual(result["body"], body)

    def test_gaps_are_not_touched(self):
        gaps = f"- [ ] [G1] {DEFER_GAP}"
        body = body_with({"How": pointer_line("-", 1), "Gaps": gaps})
        result = es.undefer_parent(body, CHILD_REF)
        self.assertEqual(es.parse(result["body"])["sections"]["Gaps"], gaps)

    def test_defer_then_undefer_restores_the_task_line(self):
        how = how_with(pointer_line(" ", 1), "- [ ] [T2] unrelated")
        deferred = es.defer_parent(self.parent(how), CHILD_REF, CHILD_TITLE, "Doing", 0)
        restored = es.undefer_parent(deferred["body"], CHILD_REF)
        self.assertEqual(es.parse(restored["body"])["sections"]["How"], how)

    def test_cli_prints_the_result_as_json(self):
        with tempfile.TemporaryDirectory() as tmp:
            body = self.parent(pointer_line("-", 1))
            path = write_file(tmp, "body.md", body)
            rc, out, err = run_cli([
                "undefer-parent", "--body-file", path, "--child", CHILD_REF,
            ])
            self.assertEqual(rc, 0, err)
            result = json.loads(out)
            self.assertEqual(set(result), {"body", "task"})
            self.assertEqual(result["task"], "T1")
            self.assertEqual(result, es.undefer_parent(body, CHILD_REF))
            self.assertIn("- [ ] [T1]", result["body"])


class PlacePriority(unittest.TestCase):
    def assertStrictlyBetween(self, value, low, high):
        self.assertGreater(value, low)
        self.assertLess(value, high)

    def test_lands_between_the_child_and_the_next_more_urgent_item(self):
        self.assertEqual(es.place_priority(5, [1, 2, 3, 7, 9]), 4)
        self.assertStrictlyBetween(es.place_priority(5, [1, 2, 3, 7, 9]), 3, 5)

    def test_uses_the_largest_strictly_smaller_value(self):
        value = es.place_priority(10, [30, 2, 8, 4, 12])
        self.assertStrictlyBetween(value, 8, 10)

    def test_adjacent_integers_give_a_fraction(self):
        value = es.place_priority(3, [2, 9])
        self.assertStrictlyBetween(value, 2, 3)
        self.assertEqual(value, 2.5)

    def test_fractional_inputs(self):
        value = es.place_priority(2.5, [2, 1])
        self.assertStrictlyBetween(value, 2, 2.5)

    def test_no_smaller_value_steps_one_more_urgent(self):
        self.assertEqual(es.place_priority(1, [2, 3]), 0)
        self.assertEqual(es.place_priority(4, []), 3)
        self.assertEqual(es.place_priority(4, [4, 4, 7]), 3)
        self.assertEqual(es.place_priority(0, [1]), -1)

    def test_a_none_child_priority_gives_none(self):
        self.assertIsNone(es.place_priority(None, [1, 2]))
        self.assertIsNone(es.place_priority(None, []))

    def test_none_entries_on_the_board_are_ignored(self):
        self.assertEqual(es.place_priority(5, [None, 3, None]), 4)
        self.assertEqual(es.place_priority(5, [None]), 4)

    def test_equal_values_are_not_smaller(self):
        self.assertEqual(es.place_priority(6, [6, 2]), 4)


class DoneRedirect(unittest.TestCase):
    def children(self):
        return [
            {"ref": "simplesalt/projects#801", "title": "Later one", "priority": 20},
            {"ref": "simplesalt/projects#802", "title": "Urgent one", "priority": 6},
            {"ref": "simplesalt/projects#803", "title": "Unranked one", "priority": None},
        ]

    def expected_gap(self, ref, title):
        return (
            f"Should we work on {ref} ({title}) next? It is the most urgent deferred "
            f"child: yes picks it back up and adds it to this Effort's What; no opens a "
            f"conversation about what to do instead"
        )

    def test_picks_the_most_urgent_child(self):
        result = es.done_redirect(body_with({}), self.children(), [1, 3, 9])
        self.assertEqual(set(result), {"body", "status", "priority", "child", "gap_added"})
        self.assertEqual(result["child"], "simplesalt/projects#802")
        self.assertEqual(result["status"], "Planning")
        self.assertTrue(result["gap_added"])
        self.assertEqual(result["priority"], es.place_priority(6, [1, 3, 9]))
        self.assertEqual(result["priority"], 4.5)

    def test_adds_the_exact_open_gap(self):
        result = es.done_redirect(body_with({}), self.children(), [])
        gaps = es.parse(result["body"])["items"]["Gaps"]
        self.assertEqual(len(gaps), 1)
        self.assertEqual(gaps[0]["state"], "open")
        self.assertEqual(gaps[0]["text"], self.expected_gap("simplesalt/projects#802", "Urgent one"))

    def test_gap_id_continues_after_existing_gaps(self):
        gaps = "- [x] [G2] old — resolved: done"
        result = es.done_redirect(body_with({"Gaps": gaps}), self.children(), [])
        section = es.parse(result["body"])["sections"]["Gaps"]
        self.assertEqual(
            section, gaps + "\n- [ ] [G3] " + self.expected_gap("simplesalt/projects#802", "Urgent one"),
        )

    def test_untouched_sections_stay_byte_identical(self):
        body = body_with({"How": pointer_line("-", 1), "Facts": "- [F1] a fact — evidence: `x.py`"})
        result = es.done_redirect(body, self.children(), [1])
        other_sections_identical(self, body, result["body"], {"Gaps"})

    def test_without_any_priority_the_first_child_wins(self):
        children = [
            {"ref": "simplesalt/projects#803", "title": "First", "priority": None},
            {"ref": "simplesalt/projects#804", "title": "Second", "priority": None},
        ]
        result = es.done_redirect(body_with({}), children, [1, 2])
        self.assertEqual(result["child"], "simplesalt/projects#803")
        self.assertIsNone(result["priority"])
        self.assertEqual(result["status"], "Planning")
        gaps = es.parse(result["body"])["items"]["Gaps"]
        self.assertEqual(gaps[0]["text"], self.expected_gap("simplesalt/projects#803", "First"))

    def test_a_missing_priority_key_counts_as_no_priority(self):
        children = [
            {"ref": "simplesalt/projects#803", "title": "First"},
            {"ref": "simplesalt/projects#804", "title": "Second", "priority": 9},
        ]
        result = es.done_redirect(body_with({}), children, [])
        self.assertEqual(result["child"], "simplesalt/projects#804")

    def test_a_zero_priority_is_a_priority(self):
        children = [
            {"ref": "simplesalt/projects#803", "title": "First", "priority": None},
            {"ref": "simplesalt/projects#804", "title": "Zero", "priority": 0},
            {"ref": "simplesalt/projects#805", "title": "One", "priority": 1},
        ]
        result = es.done_redirect(body_with({}), children, [])
        self.assertEqual(result["child"], "simplesalt/projects#804")
        self.assertEqual(result["priority"], -1)

    def test_a_priority_tie_goes_to_the_first_listed(self):
        children = [
            {"ref": "simplesalt/projects#806", "title": "A", "priority": 5},
            {"ref": "simplesalt/projects#807", "title": "B", "priority": 5},
        ]
        self.assertEqual(es.done_redirect(body_with({}), children, [])["child"], "simplesalt/projects#806")

    def test_no_smaller_board_priority_steps_one_more_urgent(self):
        result = es.done_redirect(body_with({}), self.children(), [8, 9])
        self.assertEqual(result["priority"], 5)

    def test_no_duplicate_gap_when_one_is_already_open(self):
        gaps = "- [ ] [G1] " + self.expected_gap("simplesalt/projects#802", "Urgent one")
        body = body_with({"Gaps": gaps})
        result = es.done_redirect(body, self.children(), [1])
        self.assertFalse(result["gap_added"])
        self.assertEqual(es.parse(result["body"])["sections"]["Gaps"], gaps)
        self.assertEqual(result["status"], "Planning")
        self.assertEqual(result["child"], "simplesalt/projects#802")
        self.assertEqual(result["priority"], 3.5)

    def test_a_closed_gap_does_not_suppress_a_new_one(self):
        gaps = "- [x] [G1] " + self.expected_gap("simplesalt/projects#802", "Urgent one") + " " + es.EMDASH + " resolved: no"
        result = es.done_redirect(body_with({"Gaps": gaps}), self.children(), [])
        self.assertTrue(result["gap_added"])
        self.assertEqual(len(es.parse(result["body"])["items"]["Gaps"]), 2)

    def test_an_open_gap_about_another_child_does_not_suppress(self):
        gaps = "- [ ] [G1] " + self.expected_gap("simplesalt/projects#801", "Later one")
        result = es.done_redirect(body_with({"Gaps": gaps}), self.children(), [])
        self.assertTrue(result["gap_added"])

    def test_an_open_deferred_gap_without_next_does_not_suppress(self):
        gaps = f"- [ ] [G1] {DEFER_GAP.replace(CHILD_REF, 'simplesalt/projects#802')}"
        result = es.done_redirect(body_with({"Gaps": gaps}), self.children(), [])
        self.assertTrue(result["gap_added"])

    def test_empty_children_is_refused(self):
        with self.assertRaises(ValueError):
            es.done_redirect(body_with({}), [], [1, 2])

    def test_cli_prints_the_result_as_json(self):
        with tempfile.TemporaryDirectory() as tmp:
            body = body_with({})
            path = write_file(tmp, "body.md", body)
            children = write_file(tmp, "children.json", json.dumps(self.children()))
            priorities = write_file(tmp, "priorities.json", json.dumps([1, 3, 9]))
            rc, out, err = run_cli([
                "done-redirect", "--body-file", path, "--children-file", children,
                "--priorities-file", priorities,
            ])
            self.assertEqual(rc, 0, err)
            result = json.loads(out)
            self.assertEqual(set(result), {"body", "status", "priority", "child", "gap_added"})
            self.assertEqual(result, es.done_redirect(body, self.children(), [1, 3, 9]))

    def test_cli_with_no_priority_prints_null(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = write_file(tmp, "body.md", body_with({}))
            children = write_file(tmp, "children.json", json.dumps(
                [{"ref": "simplesalt/projects#803", "title": "Only", "priority": None}]
            ))
            priorities = write_file(tmp, "priorities.json", "[]")
            rc, out, err = run_cli([
                "done-redirect", "--body-file", path, "--children-file", children,
                "--priorities-file", priorities,
            ])
            self.assertEqual(rc, 0, err)
            self.assertIsNone(json.loads(out)["priority"])

    def test_cli_exits_nonzero_without_children_and_names_a_step(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = write_file(tmp, "body.md", body_with({}))
            children = write_file(tmp, "children.json", "[]")
            priorities = write_file(tmp, "priorities.json", "[1]")
            rc, out, err = run_cli([
                "done-redirect", "--body-file", path, "--children-file", children,
                "--priorities-file", priorities,
            ])
            self.assertEqual(rc, 1)
            self.assertEqual(out, "")
            self.assertRegex(err, r"step \d")


class BlockerEdgeRefusal(unittest.TestCase):
    def test_a_parent_blocked_by_its_own_deferred_child_is_refused(self):
        message = es.blocker_edge_refusal(PARENT_REF, "Deferred", PARENT_REF)
        self.assertIsInstance(message, str)
        self.assertIn("Deferred", message)
        self.assertRegex(message, r"step \d")
        self.assertIn(PARENT_REF, message)

    def test_a_deferred_blocker_of_someone_else_is_allowed(self):
        self.assertIsNone(es.blocker_edge_refusal(PARENT_REF, "Deferred", "simplesalt/projects#999"))
        self.assertIsNone(es.blocker_edge_refusal(PARENT_REF, "Deferred", ""))
        self.assertIsNone(es.blocker_edge_refusal(PARENT_REF, "Deferred", None))

    def test_a_live_child_blocker_is_allowed(self):
        for status in ("Empty", "Planning", "Doing", "Blocked", "Review", "Paused", "Cancelled", "Done"):
            self.assertIsNone(es.blocker_edge_refusal(PARENT_REF, status, PARENT_REF), status)

    def test_cli_exits_zero_when_allowed(self):
        for status, parent in (("Doing", PARENT_REF), ("Deferred", "simplesalt/projects#999"), ("Deferred", "")):
            rc, out, err = run_cli([
                "may-add-blocker", "--blocked", PARENT_REF,
                "--blocker-status", status, "--blocker-parent", parent,
            ])
            self.assertEqual(rc, 0, out + err)
            self.assertEqual(out, "")

    def test_cli_exits_one_and_prints_the_message_when_refused(self):
        rc, out, err = run_cli([
            "may-add-blocker", "--blocked", PARENT_REF,
            "--blocker-status", "Deferred", "--blocker-parent", PARENT_REF,
        ])
        self.assertEqual(rc, 1)
        self.assertEqual(out.strip(), es.blocker_edge_refusal(PARENT_REF, "Deferred", PARENT_REF))

    def test_cli_blocker_parent_is_optional(self):
        rc, out, err = run_cli([
            "may-add-blocker", "--blocked", PARENT_REF, "--blocker-status", "Deferred",
        ])
        self.assertEqual(rc, 0, out + err)

class AddTaskNamingADeferredChild(unittest.TestCase):
    def add(self, tmp, text, children, how="", section="How", trailing=None):
        body = body_with({"How": how} if how else {})
        path = write_file(tmp, "body.md", body)
        args = ["item-add", "--body-file", path, "--section", section, "--text", text]
        if trailing is not None:
            args += ["--trailing", trailing]
        for ref in children:
            args += ["--deferred-child", ref]
        rc, out, err = run_cli(args)
        self.assertEqual(rc, 0, err)
        return out

    def test_a_task_naming_a_deferred_child_is_added_with_the_deferred_mark(self):
        with tempfile.TemporaryDirectory() as tmp:
            out = self.add(tmp, f"Carry out {CHILD_REF}", [CHILD_REF])
            self.assertEqual(es.parse(out)["sections"]["How"], f"- [-] [T1] Carry out {CHILD_REF}")
            item = es.parse(out)["items"]["How"][0]
            self.assertEqual(item["state"], "deferred")
            self.assertEqual(item["id"], 1)

    def test_the_trailing_metadata_is_kept(self):
        with tempfile.TemporaryDirectory() as tmp:
            trailing = es.EMDASH + " blockers: none; indicators: it works"
            out = self.add(tmp, f"Carry out {CHILD_REF}", [CHILD_REF], trailing=trailing)
            self.assertEqual(
                es.parse(out)["sections"]["How"],
                f"- [-] [T1] Carry out {CHILD_REF} {trailing}",
            )

    def test_a_task_not_naming_the_child_stays_open(self):
        with tempfile.TemporaryDirectory() as tmp:
            out = self.add(tmp, "plain work", [CHILD_REF])
            self.assertEqual(es.parse(out)["sections"]["How"], "- [ ] [T1] plain work")
            self.assertEqual(es.parse(out)["items"]["How"][0]["state"], "open")

    def test_a_task_naming_a_different_child_stays_open(self):
        with tempfile.TemporaryDirectory() as tmp:
            out = self.add(tmp, f"Carry out {OTHER_REF}", [CHILD_REF])
            self.assertEqual(es.parse(out)["sections"]["How"], f"- [ ] [T1] Carry out {OTHER_REF}")

    def test_a_longer_issue_number_is_not_a_match(self):
        with tempfile.TemporaryDirectory() as tmp:
            out = self.add(tmp, "Carry out simplesalt/projects#7100", [CHILD_REF])
            self.assertIn("- [ ] [T1]", out)

    def test_any_of_several_deferred_children_matches(self):
        with tempfile.TemporaryDirectory() as tmp:
            out = self.add(tmp, f"Carry out {OTHER_REF}", [CHILD_REF, OTHER_REF])
            self.assertEqual(es.parse(out)["sections"]["How"], f"- [-] [T1] Carry out {OTHER_REF}")

    def test_only_the_text_is_matched_not_the_trailing(self):
        with tempfile.TemporaryDirectory() as tmp:
            trailing = es.EMDASH + f" blockers: {CHILD_REF}; indicators: it works"
            out = self.add(tmp, "plain work", [CHILD_REF], trailing=trailing)
            self.assertIn("- [ ] [T1] plain work", out)

    def test_existing_tasks_are_left_alone(self):
        with tempfile.TemporaryDirectory() as tmp:
            existing = pointer_line(" ", 1)
            out = self.add(tmp, f"Carry out {CHILD_REF}", [CHILD_REF], how=existing)
            self.assertEqual(
                es.parse(out)["sections"]["How"],
                existing + f"\n- [-] [T2] Carry out {CHILD_REF}",
            )

    def test_other_sections_are_never_marked(self):
        with tempfile.TemporaryDirectory() as tmp:
            out = self.add(tmp, f"Is {CHILD_REF} still wanted?", [CHILD_REF], section="Gaps")
            self.assertEqual(
                es.parse(out)["sections"]["Gaps"], f"- [ ] [G1] Is {CHILD_REF} still wanted?"
            )

    def test_a_deferred_task_does_not_count_as_open(self):
        with tempfile.TemporaryDirectory() as tmp:
            out = self.add(tmp, f"Carry out {CHILD_REF}", [CHILD_REF])
            self.assertEqual(es.open_item_count(es.parse(out)["sections"], "How"), 0)

    def test_without_the_option_a_naming_task_is_open(self):
        with tempfile.TemporaryDirectory() as tmp:
            out = self.add(tmp, f"Carry out {CHILD_REF}", [])
            self.assertEqual(es.parse(out)["sections"]["How"], f"- [ ] [T1] Carry out {CHILD_REF}")


if __name__ == "__main__":
    unittest.main()
