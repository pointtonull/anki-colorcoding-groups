#!/usr/bin/env python3
"""Check that the test harness itself fails properly: each case feeds it a deliberately broken driver and expects a
quick, clear failure and no process left running, never a hang that needs a person.

    python3 tools/check_harness.py

Cases: a wait that never ends, a surprise modal dialog, a driver that cannot be imported, a driver that runs past
its deadline, and Anki stalled before any add-on loads (the first-run language picker, which is what used to need a
click). Takes about two minutes. Exit status 0 only if every case behaved.
"""
import shutil
import subprocess
import sys
import tempfile
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import sandbox  # noqa: E402

WAITS_FOREVER = '''
from . import driver_kit as kit

def scenario():
    kit.step("waiting for something that never comes")
    yield kit.wait(lambda: False, "a window that never comes", 2000)

kit.start(scenario)
'''

SURPRISE_DIALOG = '''
from PyQt6.QtCore import QTimer
from PyQt6.QtWidgets import QMessageBox
from . import driver_kit as kit

def scenario():
    kit.step("a surprise dialog")
    QTimer.singleShot(0, lambda: QMessageBox.warning(None, "Surprise", "Something nobody expected"))
    yield 3000

kit.start(scenario)
'''

CANNOT_IMPORT = '''
raise RuntimeError("the driver is broken")
'''

RUNS_FOREVER = '''
from . import driver_kit as kit

def scenario():
    kit.step("sleeping")
    yield 10 ** 8

kit.start(scenario)
'''

# (name, driver source, run_driver options, text the log must contain, longest acceptable run in seconds)
CASES = [
    ("a wait that never ends", WAITS_FOREVER, {"timeout": 60},
     "TIMEOUT: waited 2 s for a window that never comes", 45),
    ("a surprise modal dialog", SURPRISE_DIALOG, {"timeout": 60},
     "UNEXPECTED MODAL", 45),
    ("a driver that cannot be imported", CANNOT_IMPORT, {"timeout": 60},
     "EXCEPTION while loading the driver", 45),
    ("a driver that runs past its deadline", RUNS_FOREVER, {"timeout": 8},
     "TIMEOUT: the run took longer than 8 s", 45),
    ("Anki stalled before any add-on loads", WAITS_FOREVER, {"timeout": 60, "startup_timeout": 3, "no_seed": True},
     "something is waiting on screen before any add-on loads", 60),
]


def leftovers(base):
    return subprocess.run(["pgrep", "-f", str(base)], capture_output=True, text=True).stdout.split()


def main():
    problems = []
    real_seed = sandbox.seed_preferences
    with tempfile.TemporaryDirectory(prefix="colorcoding-harness-") as scratch:
        for name, source, options, expected, longest in CASES:
            driver = Path(scratch) / "driver.py"
            driver.write_text(source)
            options = dict(options)
            # without the seeded preferences Anki opens its first-run language picker: the old stall
            sandbox.seed_preferences = (lambda base: None) if options.pop("no_seed", False) else real_seed
            started = time.time()
            result = sandbox.run_driver(driver, Path(scratch) / "out", **options)
            took = time.time() - started
            sandbox.seed_preferences = real_seed
            verdicts = []
            if result.ok:
                verdicts.append("reported success")
            if expected not in result.log:
                verdicts.append(f"log does not say {expected!r}")
            if took > longest:
                verdicts.append(f"took {took:.0f} s (limit {longest} s)")
            if leftovers(result.base):
                verdicts.append("processes left running")
            print(f"{'ok  ' if not verdicts else 'FAIL'} {name}: {took:.0f} s, exit code {result.exit_code}"
                  + ("" if not verdicts else "  <- " + "; ".join(verdicts)))
            if verdicts:
                problems.append(name)
                print(result.log)
            shutil.rmtree(result.base, ignore_errors=True)
    print("harness behaves" if not problems else "harness problems: " + ", ".join(problems))
    return 1 if problems else 0


if __name__ == "__main__":
    sys.exit(main())
