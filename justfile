# Task runner for this repo (https://github.com/casey/just, `brew install just`).
# Recipes only name and chain the scripts in tools/ and tests/: no logic lives here, so every command also
# works without just (they are listed in CLAUDE.md). `just` alone lists the recipes.

[private]
default:
    @just --list

# Unit tests (instant, no Anki needed)
test:
    python3 -m unittest discover -s tests

# Syntax of the add-on's Python and JavaScript
syntax:
    python3 -m py_compile *.py
    node --check changer_static.js

# Fast gate before a commit: unit tests and syntax
verify: test syntax

# The add-on inside a throwaway Anki, installed under its repo name and under its AnkiWeb id (~1 min each)
check:
    python3 tools/check_in_anki.py
    python3 tools/check_in_anki.py --addon-folder 267803161

# Regenerate docs/screenshots from the real windows (all-or-nothing; commit only the images that changed)
shots:
    python3 tools/make_screenshots.py

# Prove the Anki harness fails fast and clean (~2 min; run after touching tools/)
harness:
    python3 tools/check_harness.py

# Build colorcoding-groups.ankiaddon from HEAD, to install by double-click (the release script builds its own)
package:
    git archive --format=zip -o colorcoding-groups.ankiaddon HEAD

# Release to AnkiWeb: checks, publish, verify. It asks before saving; pass --yes only once the owner agreed
release *args:
    python3 tools/release_ankiweb.py {{ args }}

# Rehearse a release: everything except pressing Save
release-dry-run:
    python3 tools/release_ankiweb.py --dry-run

# Is AnkiWeb in sync with HEAD and DESCRIPTION.md? Changes nothing
release-check:
    python3 tools/release_ankiweb.py --check

# Adopt edits made on the AnkiWeb page into DESCRIPTION.md
pull-description:
    python3 tools/release_ankiweb.py --pull-description
