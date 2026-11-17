"""Tests for cmaketaste. Standard library only: python tests/test_cmaketaste.py"""

import json
import os
import shutil
import subprocess
import sys
import tempfile
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
import cmaketaste as ct  # noqa: E402

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
CLI = os.path.join(ROOT, "cmaketaste.py")
MESSY = os.path.join(HERE, "fixtures", "messy")
GOOD = os.path.join(HERE, "fixtures", "good")


def rules_hit(path):
    return {f.rule for f in ct.audit(ct.Project(path))}


class TestScanner(unittest.TestCase):
    def test_simple_command(self):
        cmds = ct.parse_cmake("project(foo CXX)\n")
        self.assertEqual(cmds[0].name, "project")
        self.assertEqual(cmds[0].args, ["foo", "CXX"])
        self.assertEqual(cmds[0].line, 1)

    def test_line_numbers_survive_blank_lines_and_comments(self):
        text = "# a comment\n\n\nproject(foo)\n"
        self.assertEqual(ct.parse_cmake(text)[0].line, 4)

    def test_command_spanning_lines_reports_its_first_line(self):
        text = "set(X\n  a\n  b)\nproject(p)\n"
        cmds = ct.parse_cmake(text)
        self.assertEqual(cmds[0].line, 1)
        self.assertEqual(cmds[0].args, ["X", "a", "b"])
        self.assertEqual(cmds[1].line, 4)

    def test_quoted_argument_keeps_spaces(self):
        cmds = ct.parse_cmake('message("hello there" done)\n')
        self.assertEqual(cmds[0].args, ["hello there", "done"])

    def test_escaped_quote_inside_a_quoted_argument(self):
        cmds = ct.parse_cmake('message("a \\" b")\n')
        self.assertEqual(cmds[0].args, ['a " b'])

    def test_line_comment_inside_a_command_is_dropped(self):
        cmds = ct.parse_cmake("set(A # explain\n  B)\n")
        self.assertEqual(cmds[0].args, ["A", "B"])

    def test_bracket_comment_is_dropped_and_does_not_shift_lines(self):
        text = "#[[\nhidden\nlines\n]]\nproject(p)\n"
        self.assertEqual(ct.parse_cmake(text)[0].line, 5)

    def test_bracket_argument_is_one_argument(self):
        cmds = ct.parse_cmake("message([=[raw ) text]=])\n")
        self.assertEqual(len(cmds[0].args), 1)
        self.assertIn("raw ) text", cmds[0].args[0])

    def test_nested_parentheses_in_a_condition(self):
        cmds = ct.parse_cmake("if(A AND (B OR C))\nproject(p)\nendif()\n")
        self.assertEqual(cmds[0].name, "if")
        self.assertEqual(cmds[1].name, "project")

    def test_a_closing_paren_inside_quotes_does_not_end_the_command(self):
        cmds = ct.parse_cmake('message("a ) b" tail)\nproject(p)\n')
        self.assertEqual(cmds[0].args, ["a ) b", "tail"])
        self.assertEqual(cmds[1].name, "project")

    def test_arg_after_and_has(self):
        c = ct.parse_cmake("FetchContent_Declare(fmt GIT_TAG 10.2.1)\n")[0]
        self.assertEqual(c.arg_after("GIT_TAG"), "10.2.1")
        self.assertIsNone(c.arg_after("GIT_REPOSITORY"))
        self.assertTrue(c.has("fmt"))

    def test_empty_input(self):
        self.assertEqual(ct.parse_cmake(""), [])


class TestMessyFixture(unittest.TestCase):
    """The messy fixture breaks each rule on purpose."""

    @classmethod
    def setUpClass(cls):
        cls.hit = rules_hit(MESSY)

    def test_catches_old_minimum_version(self):
        self.assertIn("cmake-minimum-version", self.hit)

    def test_catches_directory_scope_commands(self):
        self.assertIn("global-scope-commands", self.hit)

    def test_catches_global_cxx_flags(self):
        self.assertIn("no-global-cxx-flags", self.hit)

    def test_catches_file_glob(self):
        self.assertIn("no-file-glob", self.hit)

    def test_catches_missing_presets(self):
        self.assertIn("presets-committed", self.hit)

    def test_catches_unignored_build_dir(self):
        self.assertIn("build-dir-ignored", self.hit)

    def test_catches_unpinned_dependencies(self):
        self.assertIn("pinned-dependencies", self.hit)

    def test_names_each_unpinned_dependency(self):
        found = [f for f in ct.audit(ct.Project(MESSY))
                 if f.rule == "pinned-dependencies"]
        detail = " ".join(f.detail for f in found)
        self.assertIn("fmt", detail)     # GIT_TAG master
        self.assertIn("json", detail)    # no GIT_TAG at all
        self.assertIn("catch", detail)   # GIT_TAG devel
        self.assertNotIn("spdlog", detail)   # pinned to v1.14.1, correctly silent

    def test_catches_missing_ctest(self):
        self.assertIn("ctest-registered", self.hit)

    def test_catches_missing_warnings(self):
        self.assertIn("strict-warnings", self.hit)

    def test_catches_missing_alias(self):
        self.assertIn("namespaced-alias", self.hit)

    def test_catches_unprefixed_option_defaulting_on(self):
        found = [f for f in ct.audit(ct.Project(MESSY))
                 if f.rule == "prefixed-options"]
        self.assertEqual(len(found), 2)

    def test_catches_undocumented_vendoring(self):
        self.assertIn("vendored-sources", self.hit)

    def test_catches_install_without_export(self):
        self.assertIn("install-and-export", self.hit)

    def test_every_finding_with_a_line_points_at_a_real_line(self):
        root = ct.Project(MESSY)
        for f in ct.audit(root):
            if not (f.file and f.line):
                continue
            path = os.path.join(MESSY, f.file)
            self.assertTrue(os.path.exists(path), f.file)
            with open(path, encoding="utf-8") as fh:
                total = len(fh.read().splitlines())
            self.assertLessEqual(f.line, total, f"{f.rule} at {f.where}")

    def test_no_rule_crashed(self):
        self.assertNotIn("internal-error", self.hit)


class TestGoodFixture(unittest.TestCase):
    """The good fixture must come back silent. A linter that cries on correct
    code is a linter people switch off."""

    def test_clean(self):
        findings = ct.audit(ct.Project(GOOD))
        self.assertEqual(findings, [],
                         "false positives: " + ", ".join(
                             f"{f.rule} ({f.detail[:60]})" for f in findings))

    def test_no_rule_crashed(self):
        self.assertNotIn("internal-error", rules_hit(GOOD))


class TestSanitizerParsing(unittest.TestCase):
    """-fsanitize takes a comma separated list. Searching for the literal
    'fsanitize=undefined' misses -fsanitize=address,undefined, which was a real
    false positive on the good fixture."""

    def test_comma_separated_list(self):
        self.assertEqual(ct.sanitizers_mentioned("-fsanitize=address,undefined"),
                         {"address", "undefined"})

    def test_single(self):
        self.assertEqual(ct.sanitizers_mentioned("-fsanitize=thread"), {"thread"})

    def test_short_names(self):
        self.assertIn("address", ct.sanitizers_mentioned("run the asan job"))
        self.assertIn("undefined", ct.sanitizers_mentioned("ubsan build"))

    def test_nothing(self):
        self.assertEqual(ct.sanitizers_mentioned("plain release build"), set())


class TestVersionParsing(unittest.TestCase):
    def test_reads_two_components(self):
        self.assertEqual(ct._version_tuple("3.24"), (3, 24))
        self.assertEqual(ct._version_tuple("3.28.1"), (3, 28))
        self.assertEqual(ct._version_tuple("3.24...3.30"), (3, 24))

    def test_unreadable_version(self):
        self.assertIsNone(ct._version_tuple("${SOME_VAR}"))

    def test_unreadable_version_is_reported_not_ignored(self):
        with tempfile.TemporaryDirectory() as d:
            with open(os.path.join(d, "CMakeLists.txt"), "w", encoding="utf-8") as fh:
                fh.write("cmake_minimum_required(VERSION ${MIN})\nproject(x)\n")
            hits = {f.rule for f in ct.audit(ct.Project(d))}
            self.assertIn("cmake-minimum-version", hits)


class TestCli(unittest.TestCase):
    def run_cli(self, args):
        return subprocess.run([sys.executable, "-I", CLI] + args,
                              capture_output=True, text=True)

    def test_messy_exits_0_without_fail_at(self):
        r = self.run_cli([MESSY])
        self.assertEqual(r.returncode, 0)
        self.assertIn("pinned-dependencies", r.stdout)

    def test_fail_at_turns_findings_into_exit_1(self):
        r = self.run_cli([MESSY, "--fail-at", "0.9"])
        self.assertEqual(r.returncode, 1)

    def test_fail_at_above_every_finding_exits_0(self):
        r = self.run_cli([GOOD, "--fail-at", "0.5"])
        self.assertEqual(r.returncode, 0)

    def test_min_confidence_filters(self):
        r = self.run_cli([MESSY, "--min-confidence", "0.9", "--json"])
        payload = json.loads(r.stdout)
        self.assertTrue(payload["findings"])
        self.assertTrue(all(f["confidence"] >= 0.9 for f in payload["findings"]))

    def test_json_is_valid_and_carries_locations(self):
        r = self.run_cli([MESSY, "--json"])
        payload = json.loads(r.stdout)
        located = [f for f in payload["findings"] if f["line"]]
        self.assertTrue(located)

    def test_not_a_directory_exits_2(self):
        r = self.run_cli([os.path.join(HERE, "nope")])
        self.assertEqual(r.returncode, 2)
        self.assertIn("not a directory", r.stderr)

    def test_directory_without_cmake_exits_2(self):
        with tempfile.TemporaryDirectory() as d:
            r = self.run_cli([d])
            self.assertEqual(r.returncode, 2)
            self.assertIn("does not look like a CMake project", r.stderr)

    def test_bad_confidence_exits_2(self):
        r = self.run_cli([GOOD, "--min-confidence", "7"])
        self.assertEqual(r.returncode, 2)
        self.assertIn("between 0.0 and 1.0", r.stderr)

    def test_list_rules(self):
        r = self.run_cli(["--list-rules"])
        self.assertEqual(r.returncode, 0)
        self.assertEqual(len(r.stdout.strip().splitlines()), len(ct.RULES))

    def test_broken_presets_json_is_reported_not_crashed(self):
        with tempfile.TemporaryDirectory() as d:
            shutil.copy(os.path.join(GOOD, "CMakeLists.txt"), d)
            with open(os.path.join(d, "CMakePresets.json"), "w", encoding="utf-8") as fh:
                fh.write("{ not json at all ")
            r = self.run_cli([d, "--json"])
            self.assertEqual(r.returncode, 0)
            payload = json.loads(r.stdout)
            rules = {f["rule"] for f in payload["findings"]}
            self.assertIn("presets-committed", rules)
            self.assertNotIn("internal-error", rules)


if __name__ == "__main__":
    unittest.main(verbosity=2)
