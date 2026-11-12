#!/usr/bin/env python3
"""Regenerate docs/screenshots/*.png from the add-on's real UI.

Starts a second, throwaway Anki with its own base folder and profile, so your collection, your add-ons and
your vocabulary are never touched or shown. The sandbox gets a copy of this working tree plus
user_files/config.example.json (invented words), and tools/screenshot_driver.py drives it and saves the PNGs.

    python3 tools/make_screenshots.py [--anki PATH] [--out DIR] [--keep]

Needs Anki installed (default: the macOS app bundle). Windows of the sandbox Anki flash up for about half a
minute; leave the mouse and keyboard alone meanwhile. Run it again after any UI change.
"""
import argparse
import os
import shutil
import subprocess
import sys
import tempfile
import time
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent
DEFAULT_ANKI = "/Applications/Anki.app/Contents/MacOS/Anki"
DEV_DIRS = ("tools/", "docs/")  # not part of the add-on, so not copied into the sandbox


def build_sandbox(base):
    addons = base / "addons21"
    addon = addons / "colorcoding_groups"
    listing = subprocess.run(["git", "-C", str(REPO), "ls-files", "-z", "--cached", "--others", "--exclude-standard"],
                             check=True, capture_output=True, text=True).stdout
    for rel in filter(None, listing.split("\0")):
        if rel.startswith(DEV_DIRS) or not (REPO / rel).is_file():
            continue
        target = addon / rel
        target.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(REPO / rel, target)
    user_files = addon / "user_files"
    user_files.mkdir(exist_ok=True)
    shutil.copy2(REPO / "user_files" / "config.example.json", user_files / "config.json")
    (user_files / "settings.json").write_text('{"autoColorAllFields": true}')
    driver = addons / "zz_screenshots"
    driver.mkdir()
    shutil.copy2(REPO / "tools" / "screenshot_driver.py", driver / "__init__.py")


def main():
    parser = argparse.ArgumentParser(description=(__doc__ or "").split("\n\n")[0])
    parser.add_argument("--anki", default=DEFAULT_ANKI, help="Anki executable (default: %(default)s)")
    parser.add_argument("--out", type=Path, default=REPO / "docs" / "screenshots", help="where to write the PNGs")
    parser.add_argument("--timeout", type=int, default=120, help="seconds to wait for the run (default: %(default)s)")
    parser.add_argument("--keep", action="store_true", help="keep the sandbox folder (its logs) afterwards")
    args = parser.parse_args()

    base = Path(tempfile.mkdtemp(prefix="colorcoding-shots-"))
    args.out.mkdir(parents=True, exist_ok=True)
    build_sandbox(base)
    log, done = base / "driver.log", base / "done"
    env = dict(os.environ,
               ANKI_SINGLE_INSTANCE_KEY=f"colorcoding-shots-{os.getpid()}",  # don't hand over to a running Anki
               COLORCODING_SHOTS_OUT=str(args.out.resolve()),
               COLORCODING_SHOTS_LOG=str(log))
    with open(base / "anki.log", "w") as anki_log:
        anki = subprocess.Popen([args.anki, "-b", str(base), "-p", "User 1", "-l", "en"],
                                env=env, stdout=anki_log, stderr=subprocess.STDOUT)
        deadline = time.time() + args.timeout
        while time.time() < deadline and anki.poll() is None and not done.exists():
            time.sleep(1)
        if anki.poll() is None:  # give it a moment to quit by itself, then stop it
            time.sleep(5)
            anki.terminate()
            try:
                anki.wait(10)
            except subprocess.TimeoutExpired:
                anki.kill()

    print(log.read_text() if log.exists() else "driver never started (see anki.log)")
    if args.keep or not done.exists():  # keep the logs when something went wrong
        print(f"sandbox: {base}")
    else:
        shutil.rmtree(base)
    return 0 if done.exists() else 1


if __name__ == "__main__":
    sys.exit(main())
