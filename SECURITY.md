# Security

## Reporting

Use [private vulnerability reporting](https://github.com/Abner-Machado/cmaketaste/security/advisories/new)
on this repository, rather than a public issue. No bounty, and no promised response window:
what you get is an honest answer about whether it is real and what happens next.

## What the attack surface actually is

This tool exists to read somebody else's `CMakeLists.txt`. The input is untrusted by
definition, and the tokenizer is the whole surface.

Worth probing:

- A listfile that makes the scanner hang, recurse without bound, or exhaust memory. Bracket
  arguments, nested parentheses and unterminated quotes are the places to push.
- A finding that **disappears**: a listfile that fetches an unpinned dependency, or leaks
  include paths out of directory scope, and comes back clean. A linter that misses the thing
  it was built to catch is the bug that matters here.
- A reported line number that points at a line the finding is not on. The repository claims
  every finding that can point at a line does, and that the number is real.

The tool never runs CMake, never executes anything it reads, makes no network call, and
imports only the standard library. CI checks that last claim on every push; the one
exception is `scripts/make_banner.py`, which needs Pillow, is not imported by the tool, and
is excluded from the check on purpose.

## What is out of scope

Disagreeing with a rule, or with its confidence, is an issue rather than a vulnerability.
The confidences are published so they can be argued with, and `--min-confidence` exists so
you can switch a rule off on purpose.
