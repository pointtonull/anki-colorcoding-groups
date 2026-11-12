"""Throwaway-Anki plumbing shared by make_screenshots.py and check_in_anki.py. Not part of the add-on.

`run_driver` starts a second Anki with its own base folder and profile (so your collection, add-ons and
vocabulary are never touched), loads a copy of this working tree plus a driver add-on into it, and waits for
the driver to finish. It never waits forever and never leaves processes behind:
  * the driver (see driver_kit.py) has its own deadlines and stops with a TIMEOUT line when something it waits for
    does not appear, saving PNGs of what was on screen;
  * the launcher backs that up for when the driver cannot even start (a dialog before add-ons load, a crash): a
    start-up deadline, a total deadline, and an exit if Anki dies;
  * on every way out, Anki and all its helper processes (audio player, web engine) are stopped.

The driver gets these environment variables:
    COLORCODING_SHOTS_OUT   a folder it may write files to
    COLORCODING_SHOTS_LOG   its log file; the `done` file next to it holds the verdict, "ok" or "failed"
    COLORCODING_STARTUP_S / COLORCODING_DEADLINE_S   its deadlines, in seconds
    COLORCODING_ADDON       the add-on's folder name in the sandbox
"""
import os
import pickle
import random
import shutil
import signal
import sqlite3
import subprocess
import tempfile
import time
from dataclasses import dataclass
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent
DEFAULT_ANKI = "/Applications/Anki.app/Contents/MacOS/Anki"
DEV_DIRS = ("tools/", "docs/", "tests/")  # not part of the add-on, so not copied into the sandbox
BACKSTOP_S = 20  # how much longer than the driver's own deadlines the launcher waits before giving up on it


BOOTSTRAP = '''\
import os
import traceback

try:
    from . import driver  # noqa: F401  (importing it arms the driver)
except BaseException:
    log = os.environ["COLORCODING_SHOTS_LOG"]
    with open(log, "a") as file:
        file.write("EXCEPTION while loading the driver:\\n" + traceback.format_exc() + "RESULT: 1 failure(s): driver import\\n")
    with open(os.path.join(os.path.dirname(log), "done"), "w") as file:
        file.write("failed")
'''


@dataclass
class Result:
    ok: bool        # the driver finished and reported success
    log: str        # what happened, ready to print
    problem: str    # why the harness itself gave up ("" if the driver delivered a verdict)
    base: Path      # the sandbox folder (kept when something went wrong, or on request)

    @property
    def exit_code(self):
        return 0 if self.ok else (2 if self.problem else 1)


def seed_preferences(base):
    """Write the preferences file Anki would have after its first run.

    Without prefs21.db Anki opens with a language picker (and an "are you sure?") before any add-on loads, so nothing
    in the harness could dismiss it: the run stalled until a person clicked OK. These are the values Anki itself
    stores after that dialog, minus the window geometry, plus no update check and no sync so nothing else can
    pop up or phone home."""
    meta = {"ver": 0, "updates": False, "created": int(time.time()), "id": random.getrandbits(62), "lastMsg": 0,
            "suppressUpdate": True, "firstRun": False, "defaultLang": "en_US", "last_run_version": 260904,
            "last_loaded_profile_name": "User 1", "browser_layout": "auto", "last_addon_update_check": int(time.time()),
            "answer_keys": {1: "1", 2: "2", 3: "3", 4: "4"}}
    profile = {"mainWindowGeom": None, "mainWindowState": None, "numBackups": 0, "lastOptimize": int(time.time()),
               "searchHistory": [], "syncKey": None, "syncMedia": False, "autoSync": False, "allowHTML": False,
               "importMode": 1, "lastColour": "#00f", "stripHTML": True, "deleteMedia": False}
    db = sqlite3.connect(base / "prefs21.db")
    db.execute("create table if not exists profiles (name text primary key collate nocase, data blob not null)")
    for name, data in (("_global", meta), ("User 1", profile)):
        db.execute("insert or replace into profiles values (?, ?)", (name, pickle.dumps(data, protocol=4)))
    db.commit()
    db.close()


def build_sandbox(base, driver_file, settings='{"autoColorAllFields": true}', addon_name="colorcoding_groups"):
    seed_preferences(base)
    addons = base / "addons21"
    addon = addons / addon_name
    listing = subprocess.run(["git", "-C", str(REPO), "ls-files", "-z", "--cached", "--others", "--exclude-standard"],
                             check=True, capture_output=True, text=True).stdout
    for rel in filter(None, listing.split("\0")):
        if rel.startswith(DEV_DIRS) or not (REPO / rel).is_file():
            continue
        target = addon / rel
        target.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(REPO / rel, target)
    if addon_name.isdigit():  # a numeric folder is an AnkiWeb id: without this Anki would replace our copy with the published one
        (addon / "meta.json").write_text('{"update_enabled": false}')
    user_files = addon / "user_files"
    user_files.mkdir(exist_ok=True)
    shutil.copy2(REPO / "user_files" / "config.example.json", user_files / "config.json")  # invented words only
    (user_files / "settings.json").write_text(settings)
    driver = addons / "zz_driver"
    driver.mkdir()
    shutil.copy2(driver_file, driver / "driver.py")
    for helper in ("driver_kit.py", "fake_embeddings.py"):
        shutil.copy2(REPO / "tools" / helper, driver / helper)
    # A driver that cannot even be imported would make Anki pop up an error dialog nobody sees; report it instead.
    (driver / "__init__.py").write_text(BOOTSTRAP)


def _stop(process, base):
    """Stop Anki and everything it started: its whole process group, then anything still naming the sandbox."""
    try:
        os.killpg(process.pid, signal.SIGTERM)
    except (ProcessLookupError, PermissionError):
        pass
    try:
        process.wait(6)
    except subprocess.TimeoutExpired:
        try:
            os.killpg(process.pid, signal.SIGKILL)
        except (ProcessLookupError, PermissionError):
            pass
        process.wait(6)
    subprocess.run(["pkill", "-f", str(base)], check=False)  # helpers that left the group (audio player...)


def _anki_log_tail(path, lines=12):
    try:
        text = [line for line in Path(path).read_text(errors="replace").splitlines()
                if line.strip() and "Called attribute on invalid object" not in line]
    except OSError:
        return ""
    return "\n".join(text[-lines:])


def run_driver(driver_file, out, anki=DEFAULT_ANKI, timeout=120, keep=False, settings='{"autoColorAllFields": true}',
               addon_name="colorcoding_groups", startup_timeout=45):
    """Run `driver_file` inside a throwaway Anki, with the add-on in a folder called `addon_name` (AnkiWeb installs
    use a number, which matters for imports). Returns a Result."""
    base = Path(tempfile.mkdtemp(prefix="colorcoding-sandbox-"))
    out.mkdir(parents=True, exist_ok=True)
    build_sandbox(base, driver_file, settings, addon_name)
    log, done = base / "driver.log", base / "done"
    env = dict(os.environ,
               ANKI_SINGLE_INSTANCE_KEY=f"colorcoding-sandbox-{os.getpid()}",  # don't hand over to a running Anki
               COLORCODING_SHOTS_OUT=str(out.resolve()),
               COLORCODING_SHOTS_LOG=str(log),
               COLORCODING_STARTUP_S=str(startup_timeout),
               COLORCODING_DEADLINE_S=str(timeout),
               COLORCODING_ADDON=addon_name)
    problem = ""
    started = time.time()
    with open(base / "anki.log", "w") as anki_log:
        process = subprocess.Popen([anki, "-b", str(base), "-p", "User 1", "-l", "en"], env=env,
                                   stdout=anki_log, stderr=subprocess.STDOUT,
                                   # own process group, so _stop() reaches every helper; a new *session* would cut
                                   # a GUI app off from the window server and Anki stalls before opening the profile
                                   process_group=0)
        try:
            while not done.exists():
                elapsed = time.time() - started
                opened = log.exists() and "profile open" in log.read_text()
                if process.poll() is not None:
                    problem = f"Anki exited (code {process.returncode}) before the driver finished"
                elif not opened and elapsed > startup_timeout + BACKSTOP_S:
                    problem = (f"Anki never opened the profile within {startup_timeout + BACKSTOP_S} s: something is "
                               f"waiting on screen before any add-on loads (a first-run dialog such as the language "
                               f"picker, an update prompt, a macOS permission request); see anki.log")
                elif elapsed > timeout + BACKSTOP_S:
                    problem = f"no result {timeout + BACKSTOP_S} s after starting (the driver is stuck; see driver.log)"
                if problem:
                    break
                time.sleep(0.5)
            if not problem:  # a verdict arrived: give Anki a moment to quit by itself
                try:
                    process.wait(12)
                except subprocess.TimeoutExpired:
                    pass
        finally:
            _stop(process, base)  # also on Ctrl-C or any error above

    verdict = done.read_text() if done.exists() else ""
    text = log.read_text() if log.exists() else "(the driver wrote no log)"
    if problem:
        text += f"\nHARNESS: {problem}\nlast lines of anki.log:\n{_anki_log_tail(base / 'anki.log')}"
    ok = verdict == "ok" and not problem
    if keep or not ok:  # keep the logs and snapshots when something went wrong
        text += f"\nsandbox kept: {base}"
    else:
        shutil.rmtree(base, ignore_errors=True)
    return Result(ok, text, problem, base)
