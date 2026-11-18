#!/usr/bin/env python3
"""Offline tests for skill-diff.py: frontmatter parsing, compare, allow-list flags, exit codes."""
import contextlib
import importlib.util
import io
import json
import os
import unittest
from unittest import mock

SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))
SPEC = importlib.util.spec_from_file_location(
    "skill_diff", os.path.join(SCRIPT_DIR, "skill-diff.py")
)
sd = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(sd)

OLD_SHA = "a" * 40
NEW_SHA = "b" * 40
OLD_DIGEST = "sha256:" + "1" * 64
NEW_DIGEST = "sha256:" + "2" * 64


def skill(name, path=None, fingerprint="fp"):
    return sd.Skill(name, path or f"skills/{name}", fingerprint)


def index(*skills):
    return {s.name: s for s in skills}


def snapshot(ref, *skills, revision=None):
    return sd.Snapshot(ref, revision or ref, index(*skills))


def blob(path, sha="f" * 40):
    return {"path": path, "type": "blob", "sha": sha}


def tree(path, sha="e" * 40):
    return {"path": path, "type": "tree", "sha": sha}


class FrontmatterNameTests(unittest.TestCase):
    def test_plain_name(self):
        self.assertEqual(sd.parse_frontmatter_name("---\nname: query\n---\nbody\n"), "query")

    def test_double_quoted_name(self):
        self.assertEqual(sd.parse_frontmatter_name('---\nname: "brain-ops"\n---\n'), "brain-ops")

    def test_single_quoted_name(self):
        self.assertEqual(sd.parse_frontmatter_name("---\nname: 'brain-ops'\n---\n"), "brain-ops")

    def test_quoted_name_with_trailing_comment(self):
        self.assertEqual(sd.parse_frontmatter_name('---\nname: "maps"  # nav\n---\n'), "maps")

    def test_plain_name_with_trailing_comment(self):
        self.assertEqual(sd.parse_frontmatter_name("---\nname: maps # nav\n---\n"), "maps")

    def test_crlf_line_endings(self):
        self.assertEqual(
            sd.parse_frontmatter_name("---\r\nname: arxiv\r\ndescription: x\r\n---\r\nbody\r\n"),
            "arxiv",
        )

    def test_name_after_other_keys(self):
        text = "---\ndescription: |\n  long\n  text\nname: humanizer\n---\n"
        self.assertEqual(sd.parse_frontmatter_name(text), "humanizer")

    def test_byte_order_mark(self):
        self.assertEqual(sd.parse_frontmatter_name("﻿---\nname: maps\n---\n"), "maps")

    def test_missing_name_key(self):
        self.assertIsNone(sd.parse_frontmatter_name("---\ndescription: x\n---\nname: body\n"))

    def test_empty_name(self):
        self.assertIsNone(sd.parse_frontmatter_name("---\nname:\n---\n"))
        self.assertIsNone(sd.parse_frontmatter_name('---\nname: ""\n---\n'))
        self.assertIsNone(sd.parse_frontmatter_name("---\nname: # nothing\n---\n"))

    def test_block_scalar_name_is_unusable(self):
        self.assertIsNone(sd.parse_frontmatter_name("---\nname: >-\n  folded\n---\n"))

    def test_no_frontmatter(self):
        self.assertIsNone(sd.parse_frontmatter_name("# Title\nname: query\n"))

    def test_empty_file(self):
        self.assertIsNone(sd.parse_frontmatter_name(""))

    def test_unterminated_frontmatter_still_reads_name(self):
        self.assertEqual(sd.parse_frontmatter_name("---\nname: query\n"), "query")

    def test_name_only_in_body_after_closing_delimiter(self):
        self.assertIsNone(sd.parse_frontmatter_name("---\ntitle: x\n---\nname: query\n"))

    def test_indented_name_is_ignored(self):
        self.assertIsNone(sd.parse_frontmatter_name("---\nmetadata:\n  name: nested\n---\n"))

    def test_name_key_prefix_is_not_name(self):
        self.assertIsNone(sd.parse_frontmatter_name("---\nnamespace: x\n---\n"))


class FindSkillFoldersTests(unittest.TestCase):
    ENTRIES = [
        blob("RESOLVER.md"),
        tree("query", "t-query"),
        blob("query/SKILL.md", "b-query"),
        blob("query/notes.md"),
        tree("conventions", "t-conv"),
        blob("conventions/quality.md"),
        tree("migrations", "t-mig"),
        blob("migrations/v1.md"),
        tree("apple", "t-apple"),
        tree("apple/apple-notes", "t-notes"),
        blob("apple/apple-notes/SKILL.md", "b-notes"),
        tree("apple/apple-notes/scripts", "t-scripts"),
        blob("apple/apple-notes/scripts/run.sh"),
        blob("SKILL.md"),
    ]

    def test_depth_one_keeps_only_direct_skill_folders(self):
        folders = sd.find_skill_folders(self.ENTRIES, max_depth=1)
        self.assertEqual(
            [(f.path, f.tree_sha, f.file_sha) for f in folders],
            [("query", "t-query", "b-query")],
        )

    def test_unlimited_depth_finds_nested_skill_folders(self):
        folders = sd.find_skill_folders(self.ENTRIES)
        self.assertEqual(
            [(f.path, f.tree_sha, f.file_sha) for f in folders],
            [
                ("apple/apple-notes", "t-notes", "b-notes"),
                ("query", "t-query", "b-query"),
            ],
        )

    def test_folders_without_skill_file_are_excluded(self):
        paths = [f.path for f in sd.find_skill_folders(self.ENTRIES)]
        self.assertNotIn("conventions", paths)
        self.assertNotIn("migrations", paths)

    def test_root_level_skill_file_is_not_a_skill(self):
        self.assertEqual(sd.find_skill_folders([blob("SKILL.md")]), [])

    def test_missing_folder_tree_entry_is_an_error(self):
        with self.assertRaises(sd.SkillDiffError):
            sd.find_skill_folders([blob("query/SKILL.md")])


class IndexSkillsTests(unittest.TestCase):
    def test_keys_by_frontmatter_name_with_repo_relative_path(self):
        folders = [sd.SkillFolder("apple/apple-notes", "t1", "b1")]
        skills = sd.index_skills(folders, {"b1": "notes"})
        self.assertEqual(skills, {"notes": sd.Skill("notes", "skills/apple/apple-notes", "t1")})

    def test_duplicate_names_are_an_error(self):
        folders = [sd.SkillFolder("a/x", "t1", "b1"), sd.SkillFolder("b/y", "t2", "b2")]
        with self.assertRaises(sd.SkillDiffError):
            sd.index_skills(folders, {"b1": "same", "b2": "same"})


class CompareSkillsTests(unittest.TestCase):
    def test_added_removed_changed_unchanged(self):
        old = index(
            skill("keep"), skill("edit", fingerprint="v1"), skill("gone"), skill("drop")
        )
        new = index(
            skill("keep"), skill("edit", fingerprint="v2"), skill("fresh"), skill("alpha")
        )
        result = sd.compare_skills(old, new)
        self.assertEqual(
            result["added"],
            [{"name": "alpha", "path": "skills/alpha"}, {"name": "fresh", "path": "skills/fresh"}],
        )
        self.assertEqual(
            result["removed"],
            [{"name": "drop", "path": "skills/drop"}, {"name": "gone", "path": "skills/gone"}],
        )
        self.assertEqual(
            result["changed"],
            [{"name": "edit", "old_path": "skills/edit", "new_path": "skills/edit"}],
        )
        self.assertEqual(result["unchanged"], 1)

    def test_moved_folder_with_same_content_counts_as_changed(self):
        old = index(skill("dogfood", "skills/dogfood", "same"))
        new = index(skill("dogfood", "skills/software-development/dogfood", "same"))
        result = sd.compare_skills(old, new)
        self.assertEqual(
            result["changed"],
            [{
                "name": "dogfood",
                "old_path": "skills/dogfood",
                "new_path": "skills/software-development/dogfood",
            }],
        )
        self.assertEqual(result["unchanged"], 0)

    def test_moved_and_edited_reports_both_paths(self):
        old = index(skill("x", "skills/x", "v1"))
        new = index(skill("x", "skills/cat/x", "v2"))
        change = sd.compare_skills(old, new)["changed"][0]
        self.assertEqual((change["old_path"], change["new_path"]), ("skills/x", "skills/cat/x"))

    def test_identical_sets(self):
        same = index(skill("a"), skill("b"))
        result = sd.compare_skills(same, dict(same))
        self.assertEqual(
            (result["added"], result["removed"], result["changed"], result["unchanged"]),
            ([], [], [], 2),
        )

    def test_lists_are_sorted_by_name(self):
        old = index(skill("c", fingerprint="1"), skill("a", fingerprint="1"), skill("b", fingerprint="1"))
        new = index(skill("b", fingerprint="2"), skill("c", fingerprint="2"), skill("a", fingerprint="2"))
        names = [entry["name"] for entry in sd.compare_skills(old, new)["changed"]]
        self.assertEqual(names, ["a", "b", "c"])


class AllowListTests(unittest.TestCase):
    def comparison(self, old, new):
        return sd.compare_skills(index(*old), index(*new))

    def test_flags_changed_removed_and_missing(self):
        old = [skill("kept"), skill("edited", fingerprint="1"), skill("gone")]
        new = [skill("kept"), skill("edited", fingerprint="2")]
        flags = sd.flag_allow_list(
            "bundled",
            ["gone", "edited", "kept", "never-existed"],
            self.comparison(old, new),
            {"kept", "edited"},
        )
        self.assertEqual(flags["section"], "bundled")
        self.assertEqual(flags["names"], ["edited", "gone", "kept", "never-existed"])
        self.assertEqual(flags["changed"], ["edited"])
        self.assertEqual(flags["removed"], ["gone"])
        self.assertEqual(flags["missing_in_new"], ["gone", "never-existed"])

    def test_names_outside_the_diff_are_not_flagged(self):
        old = [skill("a", fingerprint="1"), skill("b")]
        new = [skill("a", fingerprint="2"), skill("b")]
        flags = sd.flag_allow_list("gbrain", ["b"], self.comparison(old, new), {"a", "b"})
        self.assertEqual((flags["changed"], flags["removed"], flags["missing_in_new"]), ([], [], []))

    def test_duplicate_allow_list_names_collapse(self):
        flags = sd.flag_allow_list("gbrain", ["b", "a", "b"], self.comparison([], []), set())
        self.assertEqual(flags["names"], ["a", "b"])

    def test_extract_allow_names_picks_the_section(self):
        document = {"gbrain": ["query"], "bundled": ["maps", "arxiv"]}
        self.assertEqual(sd.extract_allow_names(document, "bundled"), ["maps", "arxiv"])

    def test_extract_allow_names_rejects_bad_documents(self):
        for document in (None, [], {}, {"bundled": None}, {"bundled": "maps"}, {"bundled": [1]}, {"bundled": [""]}):
            with self.subTest(document=document):
                with self.assertRaises(sd.SkillDiffError):
                    sd.extract_allow_names(document, "bundled")


class ReportAndExitCodeTests(unittest.TestCase):
    def report(self, old_skills, new_skills, allow=()):
        return sd.build_report(
            "hermes",
            snapshot(OLD_DIGEST, *old_skills, revision="c" * 40),
            snapshot(NEW_DIGEST, *new_skills, revision="d" * 40),
            list(allow),
        )

    def test_report_shape(self):
        report = self.report([skill("a")], [skill("a"), skill("b")], ["a", "zzz"])
        self.assertEqual(
            list(report),
            ["kind", "old", "new", "added", "removed", "changed", "unchanged", "allow_list", "changed_any"],
        )
        self.assertEqual(report["kind"], "hermes")
        self.assertEqual(report["old"], {"ref": OLD_DIGEST, "revision": "c" * 40, "count": 1})
        self.assertEqual(report["new"], {"ref": NEW_DIGEST, "revision": "d" * 40, "count": 2})
        self.assertEqual(report["allow_list"]["section"], "bundled")
        self.assertEqual(report["allow_list"]["missing_in_new"], ["zzz"])
        json.dumps(report)

    def test_exit_zero_when_nothing_differs(self):
        report = self.report([skill("a")], [skill("a")])
        self.assertFalse(report["changed_any"])
        self.assertEqual(sd.exit_code(report), 0)

    def test_exit_three_when_something_added(self):
        report = self.report([skill("a")], [skill("a"), skill("b")])
        self.assertTrue(report["changed_any"])
        self.assertEqual(sd.exit_code(report), 3)

    def test_exit_three_when_something_removed(self):
        self.assertEqual(sd.exit_code(self.report([skill("a"), skill("b")], [skill("a")])), 3)

    def test_exit_three_when_something_changed(self):
        self.assertEqual(sd.exit_code(self.report([skill("a", fingerprint="1")], [skill("a", fingerprint="2")])), 3)

    def test_exit_three_when_only_moved(self):
        report = self.report([skill("a", "skills/a")], [skill("a", "skills/x/a")])
        self.assertEqual(sd.exit_code(report), 3)

    def test_summary_mentions_counts_and_allow_list(self):
        report = self.report([skill("a", fingerprint="1")], [skill("a", fingerprint="2")], ["a"])
        summary = sd.format_summary(report)
        self.assertIn("changed 1", summary)
        self.assertIn("changed: a;", summary)
        self.assertIn("review needed", summary)


class MainTests(unittest.TestCase):
    def run_main(self, argv, old_skills=(), new_skills=(), allow=("a",), error=None):
        pair = (snapshot(OLD_SHA, *old_skills), snapshot(NEW_SHA, *new_skills))
        patches = [
            mock.patch.object(sd, "load_allow_names", return_value=list(allow)),
            mock.patch.object(sd, "load_snapshot_pair", side_effect=error, return_value=pair),
        ]
        out, err = io.StringIO(), io.StringIO()
        with contextlib.ExitStack() as stack:
            for patch in patches:
                stack.enter_context(patch)
            stack.enter_context(contextlib.redirect_stdout(out))
            stack.enter_context(contextlib.redirect_stderr(err))
            code = sd.main(argv)
        return code, out.getvalue(), err.getvalue()

    def test_unchanged_exits_zero_and_prints_json(self):
        code, out, err = self.run_main(["gbrain", OLD_SHA, NEW_SHA], [skill("a")], [skill("a")])
        self.assertEqual(code, 0)
        self.assertFalse(json.loads(out)["changed_any"])
        self.assertIn("no skill added, removed or changed", err)

    def test_changed_exits_three(self):
        code, out, _ = self.run_main(
            ["gbrain", OLD_SHA, NEW_SHA], [skill("a", fingerprint="1")], [skill("a", fingerprint="2")]
        )
        self.assertEqual(code, 3)
        self.assertEqual(json.loads(out)["allow_list"]["changed"], ["a"])

    def test_known_error_exits_one_without_stdout(self):
        code, out, err = self.run_main(
            ["gbrain", OLD_SHA, NEW_SHA], error=sd.SkillDiffError("tree listing is truncated")
        )
        self.assertEqual(code, 1)
        self.assertEqual(out, "")
        self.assertIn("tree listing is truncated", err)

    def test_unexpected_error_exits_one_without_stdout(self):
        code, out, err = self.run_main(["gbrain", OLD_SHA, NEW_SHA], error=KeyError("tree"))
        self.assertEqual(code, 1)
        self.assertEqual(out, "")
        self.assertIn("KeyError", err)

    def test_usage_errors_exit_two(self):
        bad_invocations = [
            [],
            ["gbrain"],
            ["gbrain", OLD_SHA],
            ["gbrain", "abc123", NEW_SHA],
            ["gbrain", OLD_DIGEST, NEW_DIGEST],
            ["hermes", OLD_SHA, NEW_SHA],
            ["hermes", "sha256:bogus", NEW_DIGEST],
            ["bogus", OLD_SHA, NEW_SHA],
        ]
        for argv in bad_invocations:
            with self.subTest(argv=argv):
                with contextlib.redirect_stderr(io.StringIO()):
                    with self.assertRaises(SystemExit) as raised:
                        sd.build_parser().parse_args(argv)
                self.assertEqual(raised.exception.code, 2)

    def test_allow_list_option_is_parsed(self):
        args = sd.build_parser().parse_args(["hermes", OLD_DIGEST, NEW_DIGEST, "--allow-list", "x.yaml"])
        self.assertEqual((args.kind, args.old, args.new, args.allow_list), ("hermes", OLD_DIGEST, NEW_DIGEST, "x.yaml"))

    def test_allow_list_defaults_to_remote_fetch(self):
        args = sd.build_parser().parse_args(["gbrain", OLD_SHA, NEW_SHA])
        self.assertIsNone(args.allow_list)


class LoadFailureTests(unittest.TestCase):
    def test_truncated_tree_is_an_error(self):
        listing = json.dumps({"truncated": True, "tree": []}).encode()
        with mock.patch.object(sd, "gh_api", return_value=listing):
            with self.assertRaises(sd.SkillDiffError):
                sd.fetch_tree_entries("o/r", OLD_SHA)

    def test_skill_without_frontmatter_name_is_an_error(self):
        folder = sd.SkillFolder("query", "t", "b")
        with mock.patch.object(sd, "gh_api", return_value=b"---\ndescription: x\n---\n"):
            with self.assertRaises(sd.SkillDiffError):
                sd.fetch_skill_name("o/r", folder)

    def test_names_are_read_once_per_blob(self):
        folders = [sd.SkillFolder("a", "t1", "b1"), sd.SkillFolder("a2", "t2", "b1"), sd.SkillFolder("b", "t3", "b2")]
        contents = {
            "repos/o/r/git/blobs/b1": b"---\nname: first\n---\n",
            "repos/o/r/git/blobs/b2": b"---\nname: second\n---\n",
        }
        with mock.patch.object(sd, "gh_api", side_effect=lambda path, raw=False: contents[path]) as api:
            names = sd.read_skill_names("o/r", folders)
        self.assertEqual(api.call_count, 2)
        self.assertEqual(names, {"b1": "first", "b2": "second"})

    def test_missing_revision_label_is_an_error(self):
        responses = {
            "manifests/" + OLD_DIGEST: {"manifests": [{"digest": "sha256:" + "3" * 64, "platform": {"os": "linux", "architecture": "amd64"}}]},
            "manifests/sha256:" + "3" * 64: {"config": {"digest": "sha256:" + "4" * 64}},
            "blobs/sha256:" + "4" * 64: {"config": {"Labels": {}}},
        }

        def fake_registry_json(url, token, accept=None):
            return responses[url.split("/v2/nousresearch/hermes-agent/")[1]]

        with mock.patch.object(sd, "registry_json", side_effect=fake_registry_json), \
                mock.patch.object(sd, "release_tags_for_digest", return_value=[]):
            with self.assertRaises(sd.SkillDiffError):
                sd.hermes_source_revision(OLD_DIGEST, "token")

    def test_missing_revision_label_falls_back_to_release_tag(self):
        responses = {
            "manifests/" + OLD_DIGEST: {"config": {"digest": "sha256:" + "4" * 64}},
            "blobs/sha256:" + "4" * 64: {"config": {"Labels": None}},
        }

        def fake_registry_json(url, token, accept=None):
            return responses[url.split("/v2/nousresearch/hermes-agent/")[1]]

        with mock.patch.object(sd, "registry_json", side_effect=fake_registry_json), \
                mock.patch.object(sd, "release_tags_for_digest", return_value=["v0.22.2"]), \
                mock.patch.object(sd, "tag_commit", return_value="8" * 40) as commit, \
                contextlib.redirect_stderr(io.StringIO()):
            self.assertEqual(sd.hermes_source_revision(OLD_DIGEST, "token"), "8" * 40)
        commit.assert_called_once_with(sd.HERMES_SOURCE_REPO, "v0.22.2")

    def test_ambiguous_release_tags_are_an_error(self):
        with mock.patch.object(sd, "release_tags_for_digest", return_value=["v0.22.1", "v0.22.2"]):
            with self.assertRaises(sd.SkillDiffError):
                sd.revision_from_release_tag(OLD_DIGEST)

    def test_release_tags_follow_pages_and_keep_only_plain_versions(self):
        second = "https://hub.docker.com/v2/next-page"
        pages = {
            sd.HUB_TAGS_URL: {"next": second, "results": [
                {"name": "rc.3-v0.22.2", "digest": OLD_DIGEST},
                {"name": "v0.22.2-desktop", "digest": OLD_DIGEST},
                {"name": "v0.22.5", "digest": NEW_DIGEST},
            ]},
            second: {"next": None, "results": [{"name": "v0.22.2", "digest": OLD_DIGEST}, {"name": "latest", "digest": OLD_DIGEST}]},
        }
        with mock.patch.object(sd, "http_get", side_effect=lambda url, headers=None: json.dumps(pages[url]).encode()):
            self.assertEqual(sd.release_tags_for_digest(OLD_DIGEST), ["v0.22.2"])

    def test_release_tag_listing_must_stay_on_docker_hub(self):
        page = {"next": "https://example.com/v2/x", "results": []}
        with mock.patch.object(sd, "http_get", return_value=json.dumps(page).encode()):
            with self.assertRaises(sd.SkillDiffError):
                sd.release_tags_for_digest(OLD_DIGEST)

    def test_annotated_tag_is_peeled_to_its_commit(self):
        responses = {
            "repos/o/r/git/ref/tags/v1.0.0": {"object": {"type": "tag", "sha": "7" * 40}},
            "repos/o/r/git/tags/" + "7" * 40: {"object": {"type": "commit", "sha": "6" * 40}},
        }
        with mock.patch.object(sd, "gh_api", side_effect=lambda path, raw=False: json.dumps(responses[path]).encode()):
            self.assertEqual(sd.tag_commit("o/r", "v1.0.0"), "6" * 40)

    def test_tag_pointing_at_a_tree_is_an_error(self):
        listing = json.dumps({"object": {"type": "tree", "sha": "7" * 40}}).encode()
        with mock.patch.object(sd, "gh_api", return_value=listing):
            with self.assertRaises(sd.SkillDiffError):
                sd.tag_commit("o/r", "v1.0.0")

    def test_revision_is_read_from_label_via_linux_amd64_manifest(self):
        revision = "9" * 40
        amd64 = "sha256:" + "3" * 64
        responses = {
            "manifests/" + OLD_DIGEST: {"manifests": [
                {"digest": "sha256:" + "5" * 64, "platform": {"os": "linux", "architecture": "arm64"}},
                {"digest": amd64, "platform": {"os": "linux", "architecture": "amd64"}},
            ]},
            "manifests/" + amd64: {"config": {"digest": "sha256:" + "4" * 64}},
            "blobs/sha256:" + "4" * 64: {"config": {"Labels": {sd.REVISION_LABEL: revision}}},
        }

        def fake_registry_json(url, token, accept=None):
            return responses[url.split("/v2/nousresearch/hermes-agent/")[1]]

        with mock.patch.object(sd, "registry_json", side_effect=fake_registry_json):
            self.assertEqual(sd.hermes_source_revision(OLD_DIGEST, "token"), revision)

    def test_index_without_linux_amd64_is_an_error(self):
        index_doc = {"manifests": [{"digest": "sha256:" + "5" * 64, "platform": {"os": "linux", "architecture": "arm64"}}]}
        with self.assertRaises(sd.SkillDiffError):
            sd.select_linux_amd64(index_doc, OLD_DIGEST)


if __name__ == "__main__":
    unittest.main()
