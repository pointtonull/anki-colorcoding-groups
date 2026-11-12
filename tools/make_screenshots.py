#!/usr/bin/env python3
"""Regenerate docs/screenshots/*.png from the add-on's real UI.

Starts a second, throwaway Anki with its own base folder and profile, so your collection, your add-ons and
your vocabulary are never touched or shown. The sandbox gets a copy of this working tree plus
user_files/config.example.json (invented words), and tools/screenshot_driver.py drives it and saves the PNGs.

    python3 tools/make_screenshots.py [--anki PATH] [--out DIR] [--keep]

Needs Anki installed (default: the macOS app bundle). Windows of the sandbox Anki flash up for about half a
minute; no need to touch anything. The run is all-or-nothing: the images are written to a scratch folder and only
replace the ones in --out when every expected image was made and nothing went wrong, so a failed run never leaves a
half-updated set. If something it waits for does not appear it stops with a TIMEOUT line saying what, and keeps PNGs
of what was on screen (in the sandbox folder it names).
Exit status: 0 done, 1 a step failed or timed out, 2 the harness itself gave up (Anki stuck).
"""
import argparse
import shutil
import sys
import tempfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import sandbox  # noqa: E402

EXPECTED = ["editor.png", "context-menu.png", "group-picker.png", "group-manager.png", "settings.png",
            "settings-decks.png", "deck-menu.png"]


def main():
    parser = argparse.ArgumentParser(description=(__doc__ or "").split("\n\n")[0])
    parser.add_argument("--anki", default=sandbox.DEFAULT_ANKI, help="Anki executable (default: %(default)s)")
    parser.add_argument("--out", type=Path, default=sandbox.REPO / "docs" / "screenshots", help="where to put the PNGs")
    parser.add_argument("--timeout", type=int, default=120, help="seconds for the whole run (default: %(default)s)")
    parser.add_argument("--startup-timeout", type=int, default=45, help="seconds for Anki to open the profile (default: %(default)s)")
    parser.add_argument("--keep", action="store_true", help="keep the sandbox folder (its logs) afterwards")
    args = parser.parse_args()

    with tempfile.TemporaryDirectory(prefix="colorcoding-shots-out-") as scratch:
        result = sandbox.run_driver(sandbox.REPO / "tools" / "screenshot_driver.py", Path(scratch), args.anki,
                                    args.timeout, args.keep, startup_timeout=args.startup_timeout)
        print(result.log)
        if not result.ok:
            print("Not touching", args.out, "- the run did not complete.")
            return result.exit_code
        missing = [name for name in EXPECTED if not (Path(scratch) / name).is_file()]
        if missing:
            print("The run finished but these images are missing, so nothing was copied:", ", ".join(missing))
            return 1
        args.out.mkdir(parents=True, exist_ok=True)
        for name in EXPECTED:
            shutil.copy2(Path(scratch) / name, args.out / name)
    print(f"Wrote {len(EXPECTED)} images to {args.out}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
