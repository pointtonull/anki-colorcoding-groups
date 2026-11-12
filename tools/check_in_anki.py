#!/usr/bin/env python3
"""Check the add-on inside a real (throwaway) Anki: shared stylesheet, bulk colouring and undo, the editor, the
deck scope, and the colours the reviewer really shows. The unit tests (tests/) cover the pure logic; this covers
what needs Anki.

    python3 tools/check_in_anki.py [--anki PATH] [--addon-folder NAME] [--keep]

Uses a separate Anki profile with invented words (see sandbox.py), never yours. Windows of that Anki flash up for
about a minute. It never waits forever: if something it waits for does not appear it stops, says what and why, and
leaves PNGs of what was on screen in the sandbox folder it names.
Exit status: 0 all checks passed, 1 a check failed or the run timed out, 2 the harness itself gave up (Anki stuck).
"""
import argparse
import sys
from pathlib import Path
import tempfile

sys.path.insert(0, str(Path(__file__).resolve().parent))
import sandbox  # noqa: E402


def main():
    parser = argparse.ArgumentParser(description=(__doc__ or "").split("\n\n")[0])
    parser.add_argument("--anki", default=sandbox.DEFAULT_ANKI, help="Anki executable (default: %(default)s)")
    parser.add_argument("--timeout", type=int, default=150, help="seconds for the whole run (default: %(default)s)")
    parser.add_argument("--startup-timeout", type=int, default=45, help="seconds for Anki to open the profile (default: %(default)s)")
    parser.add_argument("--keep", action="store_true", help="keep the sandbox folder (its logs) afterwards")
    parser.add_argument("--addon-folder", default="colorcoding_groups",
                        help="folder name for the add-on in the sandbox; AnkiWeb installs use the number "
                             "(267803161), so try that too (default: %(default)s)")
    args = parser.parse_args()
    with tempfile.TemporaryDirectory(prefix="colorcoding-check-") as out:
        result = sandbox.run_driver(sandbox.REPO / "tools" / "integration_driver.py", Path(out), args.anki,
                                    args.timeout, args.keep, addon_name=args.addon_folder,
                                    startup_timeout=args.startup_timeout)
    print(result.log)
    return result.exit_code


if __name__ == "__main__":
    sys.exit(main())
