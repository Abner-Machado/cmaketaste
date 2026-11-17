#!/usr/bin/env python3
"""cmaketaste - audit a C++ project's build setup, not its CMake formatting.

The existing linters check CMake style: line length, indentation, whether a
command is upper or lower case. None of them answers the questions that decide
whether a build is reproducible and portable, so this does:

    is a fetched dependency pinned, or riding a moving branch
    are include paths attached to targets or leaking from directory scope
    does anything run a sanitizer, ever
    can another project consume this one with find_package

Every finding carries a confidence, because some of these are laws and some are
taste, and a tool that pretends otherwise gets switched off.

Python standard library only. No install, no network.

    python cmaketaste.py path/to/project
"""

from __future__ import annotations

import argparse
import json
import os
import re
import sys

__version__ = "0.1.0"

BRACKET_OPEN = re.compile(r"\[(=*)\[")


# ------------------------------------------------------------------ scanner

class Command:
    __slots__ = ("name", "args", "line", "raw")

    def __init__(self, name, args, line, raw):
        self.name = name
        self.args = args
        self.line = line
        self.raw = raw

    def __repr__(self):
        return f"Command({self.name!r}, {self.args!r}, line={self.line})"

    def arg_after(self, keyword):
        """The argument that follows KEYWORD, or None. Case sensitive, as CMake is."""
        for i, a in enumerate(self.args):
            if a == keyword and i + 1 < len(self.args):
                return self.args[i + 1]
        return None

    def has(self, keyword):
        return keyword in self.args


def _skip_bracket(text, i):
    """Skip a bracket argument or bracket comment starting at i. Returns the index after it."""
    m = BRACKET_OPEN.match(text, i)
    if not m:
        return None
    closing = "]" + m.group(1) + "]"
    end = text.find(closing, m.end())
    return len(text) if end == -1 else end + len(closing)


def parse_cmake(text: str) -> list:
    """Tokenize a CMake listfile into commands with their line numbers.

    Handles line comments, bracket comments, quoted arguments with escapes,
    bracket arguments, and commands spanning several lines. It is not a CMake
    interpreter and does not try to be: it reports what is written.
    """
    commands: list = []
    i = 0
    n = len(text)
    line = 1

    def bump(chunk):
        nonlocal line
        line += chunk.count("\n")

    while i < n:
        ch = text[i]

        if ch == "\n":
            line += 1
            i += 1
            continue
        if ch in " \t\r":
            i += 1
            continue

        if ch == "#":
            if text.startswith("#[", i):
                end = _skip_bracket(text, i + 1)
                if end is not None:
                    bump(text[i:end])
                    i = end
                    continue
            end = text.find("\n", i)
            i = n if end == -1 else end
            continue

        m = re.match(r"([A-Za-z_][A-Za-z0-9_]*)\s*\(", text, pos=i) if False else \
            re.compile(r"([A-Za-z_][A-Za-z0-9_]*)[ \t\r\n]*\(").match(text, i)
        if not m:
            i += 1
            continue

        name = m.group(1)
        start_line = line
        bump(text[i:m.end()])
        i = m.end()

        args: list = []
        depth = 1
        buf = ""
        while i < n and depth > 0:
            c = text[i]

            if c == '"':
                j = i + 1
                out = []
                while j < n:
                    if text[j] == "\\" and j + 1 < n:
                        out.append(text[j + 1])
                        j += 2
                        continue
                    if text[j] == '"':
                        break
                    out.append(text[j])
                    j += 1
                bump(text[i:j])
                args.append("".join(out))
                i = j + 1
                continue

            if c == "[" and BRACKET_OPEN.match(text, i):
                end = _skip_bracket(text, i)
                args.append(text[i:end])
                bump(text[i:end])
                i = end
                continue

            if c == "#":
                end = text.find("\n", i)
                i = n if end == -1 else end
                continue

            if c == "(":
                depth += 1
                i += 1
                continue
            if c == ")":
                depth -= 1
                i += 1
                if depth == 0:
                    break
                continue

            if c in " \t\r\n":
                if buf:
                    args.append(buf)
                    buf = ""
                if c == "\n":
                    line += 1
                i += 1
                continue

            buf += c
            i += 1

        if buf:
            args.append(buf)

        commands.append(Command(name, args, start_line, ""))

    return commands


# ------------------------------------------------------------------ project

SKIP_DIRS = {".git", "build", "out", "cmake-build-debug", "cmake-build-release",
             "node_modules", ".venv", "venv", "__pycache__", ".idea", ".vs"}


class Project:
    def __init__(self, root: str):
        self.root = os.path.abspath(root)
        self.listfiles: dict = {}     # relpath -> list[Command]
        self.files: set = set()       # every relpath seen
        self.workflows: dict = {}     # relpath -> text
        self.presets = None
        self.presets_error = None
        self.read()

    def rel(self, path):
        return os.path.relpath(path, self.root).replace("\\", "/")

    def read(self):
        for dirpath, dirnames, filenames in os.walk(self.root):
            dirnames[:] = [d for d in dirnames if d not in SKIP_DIRS]
            for fn in filenames:
                full = os.path.join(dirpath, fn)
                rp = self.rel(full)
                self.files.add(rp)
                if fn == "CMakeLists.txt" or fn.endswith(".cmake"):
                    try:
                        with open(full, encoding="utf-8", errors="replace") as fh:
                            self.listfiles[rp] = parse_cmake(fh.read())
                    except OSError:
                        continue
                elif "/.github/workflows/" in "/" + rp and fn.endswith((".yml", ".yaml")):
                    try:
                        with open(full, encoding="utf-8", errors="replace") as fh:
                            self.workflows[rp] = fh.read()
                    except OSError:
                        continue

        presets = os.path.join(self.root, "CMakePresets.json")
        if os.path.exists(presets):
            try:
                with open(presets, encoding="utf-8") as fh:
                    self.presets = json.load(fh)
            except (OSError, ValueError) as exc:
                self.presets_error = str(exc)

    def commands(self, *names):
        """Yield (relpath, Command) for every command with one of these names."""
        wanted = {n.lower() for n in names}
        for rp, cmds in sorted(self.listfiles.items()):
            for c in cmds:
                if c.name.lower() in wanted:
                    yield rp, c

    def has_file(self, name):
        return name in self.files

    def text_of(self, relpath):
        try:
            with open(os.path.join(self.root, relpath), encoding="utf-8",
                      errors="replace") as fh:
                return fh.read()
        except OSError:
            return ""

    @property
    def workflow_text(self):
        return "\n".join(self.workflows.values())

    @property
    def root_listfile(self):
        return "CMakeLists.txt" if "CMakeLists.txt" in self.listfiles else None

    def project_names(self):
        names = []
        for _rp, c in self.commands("project"):
            if c.args:
                names.append(c.args[0])
        return names


# ------------------------------------------------------------------ findings

class Finding:
    __slots__ = ("rule", "confidence", "title", "detail", "file", "line")

    def __init__(self, rule, confidence, title, detail, file=None, line=None):
        self.rule = rule
        self.confidence = confidence
        self.title = title
        self.detail = detail
        self.file = file
        self.line = line

    def as_dict(self):
        return {"rule": self.rule, "confidence": self.confidence,
                "title": self.title, "detail": self.detail,
                "file": self.file, "line": self.line}

    @property
    def where(self):
        if self.file and self.line:
            return f"{self.file}:{self.line}"
        return self.file or "project"


RULES: list = []


def rule(rule_id, confidence, title):
    def wrap(fn):
        fn.rule_id = rule_id
        fn.confidence = confidence
        fn.title = title
        RULES.append(fn)
        return fn
    return wrap


def F(fn, detail, file=None, line=None):
    return Finding(fn.rule_id, fn.confidence, fn.title, detail, file, line)


# ------------------------------------------------------------------ rules
# Each rule reports what is written in the project. None of them guesses.

MIN_CMAKE = (3, 24)


def _version_tuple(text):
    m = re.match(r"(\d+)\.(\d+)", str(text))
    return (int(m.group(1)), int(m.group(2))) if m else None


@rule("cmake-minimum-version", 0.90, "Require a modern CMake")
def check_minimum_version(p):
    found = list(p.commands("cmake_minimum_required"))
    if not found:
        return [F(check_minimum_version,
                  "No cmake_minimum_required anywhere. CMake then runs in a "
                  "compatibility mode whose behaviour depends on the version "
                  "installed on the machine.")]
    out = []
    for rp, c in found:
        raw = c.arg_after("VERSION")
        ver = _version_tuple(raw) if raw else None
        if ver is None:
            out.append(F(check_minimum_version,
                         f"cmake_minimum_required without a readable VERSION: {c.args}",
                         rp, c.line))
        elif ver < MIN_CMAKE:
            out.append(F(check_minimum_version,
                         f"Requires CMake {raw}, below "
                         f"{MIN_CMAKE[0]}.{MIN_CMAKE[1]}. Presets version 3 and up, "
                         "and several target properties, are not available below it.",
                         rp, c.line))
    return out


GLOBAL_COMMANDS = {
    "include_directories": "target_include_directories",
    "add_definitions": "target_compile_definitions",
    "add_compile_options": "target_compile_options",
    "link_directories": "target_link_directories",
    "link_libraries": "target_link_libraries",
}


@rule("global-scope-commands", 0.90, "Attach flags to targets, not to directories")
def check_global_scope(p):
    out = []
    for rp, c in p.commands(*GLOBAL_COMMANDS):
        better = GLOBAL_COMMANDS[c.name.lower()]
        out.append(F(check_global_scope,
                     f"{c.name}() applies to every target in this directory and "
                     f"below, with no scope control. Use {better}() with PUBLIC, "
                     "PRIVATE or INTERFACE.",
                     rp, c.line))
    return out


@rule("no-global-cxx-flags", 0.80, "Leave global flags to presets and toolchains")
def check_global_flags(p):
    out = []
    for rp, c in p.commands("set"):
        if c.args and c.args[0] in ("CMAKE_CXX_FLAGS", "CMAKE_C_FLAGS",
                                    "CMAKE_EXE_LINKER_FLAGS"):
            out.append(F(check_global_flags,
                         f"set({c.args[0]} ...) overwrites what the preset, the "
                         "toolchain file and the user passed in. Flags belong on "
                         "targets or in presets.",
                         rp, c.line))
    return out


@rule("build-dir-ignored", 0.95, "Build out of source, and ignore the build directory")
def check_build_ignored(p):
    if not p.has_file(".gitignore"):
        return [F(check_build_ignored,
                  "No .gitignore. An in-tree configure then drops CMakeCache.txt "
                  "and CMakeFiles/ straight into version control.")]
    text = p.text_of(".gitignore")
    lines = [l.strip() for l in text.splitlines()]
    if any(l.rstrip("/") in ("build", "/build", "out", "/out") or
           l.startswith("build/") or l.startswith("/build")
           for l in lines if l and not l.startswith("#")):
        return []
    return [F(check_build_ignored,
              "The .gitignore does not ignore build/ or out/.", ".gitignore")]


SANITIZER_WORDS = ("sanitiz", "asan", "ubsan", "tsan", "msan")


@rule("presets-committed", 0.85, "Commit CMakePresets.json so everyone configures the same way")
def check_presets(p):
    if p.presets_error:
        return [F(check_presets,
                  f"CMakePresets.json is present but is not valid JSON: "
                  f"{p.presets_error}", "CMakePresets.json")]
    if p.presets is None:
        return [F(check_presets,
                  "No CMakePresets.json. Everyone then configures by hand and "
                  "the CI command drifts from the one developers run.")]
    names = []
    for key in ("configurePresets", "buildPresets"):
        for item in p.presets.get(key, []):
            names.append(str(item.get("name", "")).lower())
    blob = " ".join(names)
    missing = [w for w in ("debug", "release") if w not in blob]
    out = []
    if missing:
        out.append(F(check_presets,
                     f"CMakePresets.json has no preset named for: "
                     f"{', '.join(missing)}. Presets found: "
                     f"{', '.join(n for n in names if n) or 'none'}",
                     "CMakePresets.json"))
    if not any(w in blob for w in SANITIZER_WORDS):
        out.append(F(check_presets,
                     "No sanitizer preset. A preset is where an AddressSanitizer "
                     "build stops being a command somebody remembers.",
                     "CMakePresets.json"))
    return out


@rule("ninja-generator", 0.80, "Default to Ninja, and cache compilations in CI")
def check_ninja(p):
    out = []
    if p.presets:
        gens = {str(item.get("generator", "")).lower()
                for item in p.presets.get("configurePresets", [])}
        gens.discard("")
        if gens and not any("ninja" in g for g in gens):
            out.append(F(check_ninja,
                         f"No preset uses Ninja. Generators found: "
                         f"{', '.join(sorted(gens))}", "CMakePresets.json"))
    wf = p.workflow_text.lower()
    if p.workflows and not any(w in wf for w in ("ccache", "sccache")):
        out.append(F(check_ninja,
                     "No ccache or sccache in CI. Every job then recompiles the "
                     "whole tree from cold."))
    return out


@rule("cxx-standard-on-target", 0.85, "Set the standard with target_compile_features")
def check_standard(p):
    has_feature = any(
        any(a.startswith("cxx_std_") for a in c.args)
        for _rp, c in p.commands("target_compile_features"))
    out = []
    for rp, c in p.commands("set", "target_compile_options", "add_compile_options"):
        for a in c.args:
            if a.startswith("-std=") or a.startswith("/std:"):
                out.append(F(check_standard,
                             f"Raw standard flag {a!r}. It is invisible to CMake, "
                             "so it does not propagate to consumers and it is not "
                             "portable across compilers. Use "
                             "target_compile_features(... cxx_std_NN).",
                             rp, c.line))
    if not has_feature and not out and p.listfiles:
        targets = any(True for _ in p.commands("add_library", "add_executable"))
        if targets:
            out.append(F(check_standard,
                         "No target_compile_features(... cxx_std_NN) anywhere, so "
                         "the standard is whatever the compiler defaults to."))
    return out


WARNING_FLAGS = ("-wall", "-wextra", "-wpedantic", "/w4", "-weverything")


@rule("strict-warnings", 0.85, "Turn warnings on through a shared interface target")
def check_warnings(p):
    seen = False
    for _rp, c in p.commands("target_compile_options", "add_compile_options", "set"):
        for a in c.args:
            if a.lower() in WARNING_FLAGS:
                seen = True
    if seen:
        return []
    if not any(True for _ in p.commands("add_library", "add_executable")):
        return []
    return [F(check_warnings,
              "No -Wall, -Wextra, -Wpedantic or /W4 anywhere. The compiler is the "
              "cheapest static analyser available and it is switched off.")]


@rule("no-file-glob", 0.75, "List sources explicitly")
def check_glob(p):
    out = []
    for rp, c in p.commands("file"):
        if c.args and c.args[0] in ("GLOB", "GLOB_RECURSE"):
            note = ("" if c.has("CONFIGURE_DEPENDS")
                    else " It does not even use CONFIGURE_DEPENDS, so adding a file "
                         "does not reconfigure the build and the new file is simply "
                         "not compiled.")
            out.append(F(check_glob,
                         f"file({c.args[0]} ...) builds the source list at configure "
                         f"time.{note}",
                         rp, c.line))
    return out


MOVING_REFS = {"main", "master", "develop", "dev", "trunk", "head", "latest"}
SHA_RE = re.compile(r"^[0-9a-f]{7,40}$", re.I)


@rule("pinned-dependencies", 0.90, "Pin every fetched dependency")
def check_pinning(p):
    out = []
    for rp, c in p.commands("FetchContent_Declare", "ExternalProject_Add"):
        name = c.args[0] if c.args else "?"
        tag = c.arg_after("GIT_TAG")
        if c.arg_after("GIT_REPOSITORY") is None:
            continue
        if tag is None:
            out.append(F(check_pinning,
                         f"{name} is fetched with no GIT_TAG, so it follows the "
                         "remote default branch. The build is not reproducible.",
                         rp, c.line))
        elif tag.lower().lstrip("origin/") in MOVING_REFS or tag.lower() in MOVING_REFS:
            out.append(F(check_pinning,
                         f"{name} is pinned to {tag!r}, which is a moving branch. "
                         "Two people configuring on different days get different "
                         "source.",
                         rp, c.line))
        elif not SHA_RE.match(tag) and not re.search(r"\d", tag):
            out.append(F(check_pinning,
                         f"{name} is pinned to {tag!r}, which carries no version "
                         "and no commit hash. Confirm it is an immutable tag.",
                         rp, c.line))
    return out


@rule("dependency-discovery", 0.80, "Prefer find_package for third-party libraries")
def check_dependencies(p):
    fetches = list(p.commands("FetchContent_Declare"))
    finds = list(p.commands("find_package"))
    if len(fetches) >= 4 and not finds:
        return [F(check_dependencies,
                  f"{len(fetches)} dependencies come in through FetchContent and "
                  "none through find_package. Every one of them is then rebuilt "
                  "from source in every clean build, including CI.")]
    return []


@rule("ctest-registered", 0.85, "Register tests with CTest")
def check_ctest(p):
    enabled = any(True for _ in p.commands("enable_testing")) or \
        any(c.args and c.args[0] == "CTest" for _rp, c in p.commands("include"))
    registered = any(True for _ in p.commands("add_test", "gtest_discover_tests",
                                              "catch_discover_tests",
                                              "doctest_discover_tests"))
    out = []
    if not enabled:
        out.append(F(check_ctest,
                     "Neither enable_testing() nor include(CTest) appears, so "
                     "ctest has nothing to run."))
    if not registered:
        out.append(F(check_ctest,
                     "No add_test() or *_discover_tests(). A test executable that "
                     "is built but never registered is a test nobody runs."))
    return out


FSANITIZE_RE = re.compile(r"-?fsanitize=([A-Za-z0-9_,\-]+)")


def sanitizers_mentioned(*texts):
    """Every sanitizer named anywhere in these texts.

    -fsanitize takes a comma separated list, so -fsanitize=address,undefined
    enables two. Searching for the literal string 'fsanitize=undefined' misses
    that, which is exactly the shape of false positive that gets a tool deleted.
    """
    found = set()
    for text in texts:
        low = (text or "").lower()
        for m in FSANITIZE_RE.finditer(low):
            for part in m.group(1).split(","):
                part = part.strip()
                if part:
                    found.add(part)
        for word, name in (("asan", "address"), ("ubsan", "undefined"),
                           ("tsan", "thread"), ("msan", "memory")):
            if word in low:
                found.add(name)
    return found


@rule("sanitizer-in-ci", 0.85, "Run the suite under AddressSanitizer and UBSan in CI")
def check_sanitizer_ci(p):
    if not p.workflows:
        return [F(check_sanitizer_ci,
                  "No CI workflow found under .github/workflows, so nothing runs "
                  "the suite anywhere but on a developer machine.")]
    presets_blob = json.dumps(p.presets) if p.presets else ""
    found = sanitizers_mentioned(p.workflow_text, presets_blob)
    missing = []
    if "address" not in found:
        missing.append("AddressSanitizer")
    if "undefined" not in found:
        missing.append("UndefinedBehaviorSanitizer")
    if missing:
        seen = ", ".join(sorted(found)) if found else "none"
        return [F(check_sanitizer_ci,
                  f"Nothing in CI or in the presets enables "
                  f"{' or '.join(missing)}. Sanitizers found: {seen}. In C++ "
                  "these two catch the class of bug that otherwise reaches "
                  "production as an intermittent crash.")]
    return []


@rule("export-compile-commands", 0.90, "Export compile_commands.json")
def check_compile_commands(p):
    needle = "CMAKE_EXPORT_COMPILE_COMMANDS"
    for _rp, c in p.commands("set"):
        if c.args and c.args[0] == needle:
            return []
    if p.presets:
        blob = json.dumps(p.presets)
        if needle in blob:
            return []
    if ".clangd" in p.files or "compile_commands.json" in p.files:
        return []
    return [F(check_compile_commands,
              f"{needle} is not set anywhere. Without it clangd and clang-tidy do "
              "not know how the project is compiled, and editor tooling falls back "
              "to guessing.")]


@rule("clang-format-committed", 0.85, "Commit a .clang-format and check it in CI")
def check_clang_format(p):
    out = []
    if not p.has_file(".clang-format"):
        out.append(F(check_clang_format,
                     "No .clang-format in the repository, so formatting is decided "
                     "per editor and argued about in review."))
        return out
    text = p.workflow_text.lower()
    if p.workflows and "clang-format" not in text and "format" not in text:
        out.append(F(check_clang_format,
                     "A .clang-format exists but no CI job checks it, so it only "
                     "applies to whoever remembered to run it.", ".clang-format"))
    return out


@rule("install-and-export", 0.75, "Ship install rules and an exported package config")
def check_install(p):
    libs = [(rp, c) for rp, c in p.commands("add_library")
            if not (len(c.args) > 1 and c.args[1] in ("INTERFACE", "ALIAS"))]
    if not libs:
        return []
    installs = list(p.commands("install"))
    if not installs:
        return [F(check_install,
                  "The project builds a library and has no install() rule, so it "
                  "can only be consumed by being vendored or built in tree.")]
    exported = any(c.args and c.args[0] == "EXPORT" for _rp, c in installs) or \
        any(c.args and c.has("EXPORT") for _rp, c in installs)
    config_in = any(f.endswith(("Config.cmake.in", "config.cmake.in"))
                    for f in p.files)
    if not exported and not config_in:
        return [F(check_install,
                  "There are install() rules but no install(EXPORT ...) and no "
                  "package config template, so find_package() cannot find this "
                  "project after it is installed.")]
    return []


@rule("namespaced-alias", 0.80, "Give every exported target a namespaced alias")
def check_alias(p):
    libs = [(rp, c) for rp, c in p.commands("add_library")
            if not (len(c.args) > 1 and c.args[1] == "ALIAS")]
    if not libs:
        return []
    aliases = [c.args[0] for _rp, c in p.commands("add_library")
               if len(c.args) > 1 and c.args[1] == "ALIAS"]
    if any("::" in a for a in aliases):
        return []
    names = ", ".join(sorted({c.args[0] for _rp, c in libs if c.args}))
    return [F(check_alias,
              f"No namespaced alias such as foo::foo. Libraries defined: {names}. "
              "Without the alias, in-tree use and installed use are written "
              "differently, and a typo in a plain name silently becomes a link to "
              "a system library instead of an error.")]


@rule("prefixed-options", 0.75, "Prefix options with the project name")
def check_options(p):
    prefixes = [n.upper() for n in p.project_names()]
    out = []
    for rp, c in p.commands("option"):
        if not c.args:
            continue
        name = c.args[0]
        if prefixes and not any(name.upper().startswith(pre) for pre in prefixes):
            out.append(F(check_options,
                         f"option({name}) is not prefixed with the project name. "
                         "As a subproject it collides with whatever the parent "
                         "defined.",
                         rp, c.line))
        if re.search(r"(BUILD_)?TEST", name.upper()) and len(c.args) >= 3:
            if str(c.args[2]).upper() == "ON":
                out.append(F(check_options,
                             f"option({name}) defaults to ON, so a parent project "
                             "that adds this one builds and runs your tests too.",
                             rp, c.line))
    return out


@rule("portable-ci", 0.80, "Build on every platform the project claims to support")
def check_platforms(p):
    if not p.workflows:
        return []
    text = p.workflow_text.lower()
    present = {name for name, needle in
               (("Linux", "ubuntu"), ("macOS", "macos"), ("Windows", "windows"))
               if needle in text}
    missing = {"Linux", "macOS", "Windows"} - present
    if missing and present:
        return [F(check_platforms,
                  f"CI runs on {', '.join(sorted(present))} and never on "
                  f"{', '.join(sorted(missing))}. A portability claim that is not "
                  "built is a portability hope.")]
    return []


@rule("vendored-sources", 0.75, "Explain any vendored copy")
def check_vendored(p):
    dirs = set()
    for f in p.files:
        head = f.split("/", 1)[0]
        if head in ("third_party", "thirdparty", "vendor", "external", "extern", "3rdparty"):
            dirs.add(head)
    out = []
    for d in sorted(dirs):
        documented = any(
            f.startswith(d + "/") and
            (f.lower().endswith((".patch", ".diff")) or
             f.rsplit("/", 1)[-1].lower() in ("readme.md", "readme", "readme.txt",
                                              "notice", "patches.md"))
            for f in p.files)
        if not documented:
            out.append(F(check_vendored,
                         f"{d}/ holds vendored sources with no README and no patch "
                         "file, so nobody can tell what was changed or why, or "
                         "whether the upstream fix is already in.",
                         d))
    return out


# ------------------------------------------------------------------ driver

def audit(project: Project, min_confidence: float = 0.0) -> list:
    findings: list = []
    for fn in RULES:
        if fn.confidence < min_confidence:
            continue
        try:
            findings.extend(fn(project) or [])
        except Exception as exc:          # a broken rule must not hide the rest
            findings.append(Finding("internal-error", 1.0,
                                    f"Rule {fn.rule_id} raised",
                                    f"{type(exc).__name__}: {exc}"))
    findings.sort(key=lambda f: (-f.confidence, f.rule, f.file or "", f.line or 0))
    return findings


def render_text(project, findings, min_confidence, color=False):
    def paint(code, s):
        return f"\033[{code}m{s}\033[0m" if color else s

    out = []
    out.append(f"cmaketaste {__version__}  {project.root}")
    out.append(f"{len(project.listfiles)} listfile(s), "
               f"{len(project.workflows)} workflow(s), "
               f"presets: {'yes' if project.presets else 'no'}")
    out.append("")

    if not findings:
        out.append(paint("32", "Nothing to report at this confidence. "
                               "That is a clean build setup."))
        return "\n".join(out)

    current = None
    for f in findings:
        if f.rule != current:
            current = f.rule
            out.append(paint("1", f"{f.rule}  [{f.confidence:.2f}]  {f.title}"))
        out.append(f"  {paint('36', f.where)}")
        for line in _wrap(f.detail, 76):
            out.append(f"    {line}")
        out.append("")

    counts = {}
    for f in findings:
        counts[f.rule] = counts.get(f.rule, 0) + 1
    out.append(f"{len(findings)} finding(s) across {len(counts)} rule(s), "
               f"at confidence >= {min_confidence:.2f}")
    out.append("Confidence is how sure the rule is, not how bad the finding is. "
               "Read the ones you disagree with and turn them off on purpose.")
    return "\n".join(out)


def _wrap(text, width):
    words = text.split()
    lines, cur = [], ""
    for w in words:
        if cur and len(cur) + 1 + len(w) > width:
            lines.append(cur)
            cur = w
        else:
            cur = f"{cur} {w}".strip()
    if cur:
        lines.append(cur)
    return lines or [""]


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(
        prog="cmaketaste",
        description="Audit a C++ project's build setup. Not a CMake formatter.")
    ap.add_argument("path", nargs="?", default=".",
                    help="project root (default: the current directory)")
    ap.add_argument("--min-confidence", type=float, default=0.0, metavar="N",
                    help="only run rules at or above this confidence, 0.0 to 1.0")
    ap.add_argument("--fail-at", type=float, default=None, metavar="N",
                    help="exit 1 when a finding at or above this confidence exists")
    ap.add_argument("--json", action="store_true", help="emit JSON instead of text")
    ap.add_argument("--list-rules", action="store_true",
                    help="print every rule with its confidence and exit")
    ap.add_argument("--color", action="store_true", help="colorize the text output")
    ap.add_argument("--version", action="version", version=f"cmaketaste {__version__}")
    args = ap.parse_args(argv)

    if args.list_rules:
        for fn in sorted(RULES, key=lambda f: -f.confidence):
            print(f"{fn.confidence:.2f}  {fn.rule_id:<26} {fn.title}")
        return 0

    if not 0.0 <= args.min_confidence <= 1.0:
        print("cmaketaste: --min-confidence must be between 0.0 and 1.0",
              file=sys.stderr)
        return 2
    if args.fail_at is not None and not 0.0 <= args.fail_at <= 1.0:
        print("cmaketaste: --fail-at must be between 0.0 and 1.0", file=sys.stderr)
        return 2

    root = args.path
    if not os.path.isdir(root):
        print(f"cmaketaste: not a directory: {root}", file=sys.stderr)
        return 2

    project = Project(root)
    if not project.listfiles:
        print(f"cmaketaste: no CMakeLists.txt or .cmake file under {project.root}. "
              "This does not look like a CMake project.", file=sys.stderr)
        return 2

    findings = audit(project, args.min_confidence)

    if args.json:
        print(json.dumps({
            "version": __version__,
            "root": project.root,
            "listfiles": sorted(project.listfiles),
            "min_confidence": args.min_confidence,
            "findings": [f.as_dict() for f in findings],
        }, indent=2))
    else:
        print(render_text(project, findings, args.min_confidence, args.color))

    if args.fail_at is not None:
        if any(f.confidence >= args.fail_at for f in findings):
            return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
