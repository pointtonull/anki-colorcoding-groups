"""Shared plumbing for the drivers that run inside the throwaway Anki (screenshot_driver.py, integration_driver.py).
Not part of the add-on. sandbox.py copies it next to the driver; the driver imports it as `from . import driver_kit as kit`.

A scenario is a generator that yields what to wait for:

    yield 500                                                   # sleep 500 ms
    yield kit.wait(lambda: dialog.isVisible(), "the picker to open")
    page = kit.js_value(web, "document.title", "the page title")  # run JS, get the answer in page.value
    yield page
    yield kit.js_until(web, "document.querySelector('.x')", "x to render")   # poll JS until it is truthy

Nothing here waits forever, so a run never needs a person to unstick it:
  * a wait that does not come true in time stops the run with a TIMEOUT line naming what never happened, the step it
    was in, the windows that were on screen, and a PNG of each of them (in <sandbox>/debug);
  * the run as a whole, and Anki's start-up, have their own deadlines (COLORCODING_DEADLINE_S, COLORCODING_STARTUP_S);
  * a modal dialog nobody expected (an update prompt, an error box...) is logged with its text, closed, and counts as a
    failure, instead of freezing the run.
The verdict is the `done` file next to the log: "ok" or "failed".
"""
import os
import time
import traceback

from aqt import gui_hooks, mw
from PyQt6.QtCore import QTimer
from PyQt6.QtWidgets import QApplication, QLabel, QMessageBox, QProgressDialog

LOG = os.environ["COLORCODING_SHOTS_LOG"]
BASE = os.path.dirname(LOG)
STARTUP_S = float(os.environ.get("COLORCODING_STARTUP_S", 45))
DEADLINE_S = float(os.environ.get("COLORCODING_DEADLINE_S", 150))
POLL_MS = 150

failures = []
_state = {"step": "(loading)", "t0": time.time(), "profile_open": False, "finished": False}
_keep = []  # timers must stay referenced


def log(message):
    with open(LOG, "a", encoding="utf-8") as file:
        file.write(message + "\n")


def step(name):
    _state["step"] = name
    log(f"step: {name}")


def fail(name, detail=""):
    failures.append(name)
    log(f"FAIL  {name}" + (f"   [{detail}]" if detail != "" else ""))


def check(name, ok, detail=""):
    if ok:
        log(f"PASS  {name}")
    else:
        fail(name, detail)


# --- what is on screen -----------------------------------------------------------------------------------------

def _visible_windows():
    return [w for w in QApplication.topLevelWidgets() if w.isVisible() and w.width() > 1 and w.height() > 1]


def _describe(widget):
    title = widget.windowTitle() if hasattr(widget, "windowTitle") else ""
    return f'{type(widget).__name__} "{title}" {widget.width()}x{widget.height()}'


def _snapshot_windows(tag):
    folder = os.path.join(BASE, "debug")
    os.makedirs(folder, exist_ok=True)
    paths = []
    for index, widget in enumerate(_visible_windows()):
        path = os.path.join(folder, f"{tag}-{index}-{type(widget).__name__}.png")
        if widget.grab().save(path):
            paths.append(path)
    return paths


def abort(reason):
    """Stop the run, saying what was happening and what was on screen."""
    if _state["finished"]:
        return
    log(f"{reason}   [step: {_state['step']}]")
    log("windows on screen: " + ("; ".join(_describe(w) for w in _visible_windows()) or "(none)"))
    paths = _snapshot_windows(time.strftime("%H%M%S"))
    if paths:
        log("snapshots of them: " + ", ".join(paths))
    failures.append(reason.split(":")[0])
    finish()


def finish():
    if _state["finished"]:
        return
    _state["finished"] = True
    ok = not failures
    log("RESULT: ok" if ok else f"RESULT: {len(failures)} failure(s): " + ", ".join(failures))
    with open(os.path.join(BASE, "done"), "w") as file:
        file.write("ok" if ok else "failed")
    QTimer.singleShot(8000, lambda: os._exit(0 if ok else 1))  # a shutdown that hangs must not hang the run
    try:
        mw.close()
    except Exception:
        pass


# --- waiting ---------------------------------------------------------------------------------------------------

class Wait:
    def __init__(self, predicate, what, timeout_ms):
        self.predicate, self.what, self.timeout_ms, self.value = predicate, what, timeout_ms, None


def wait(predicate, what, timeout_ms=15000):
    """Wait until predicate() is true; `what` completes "waited N s for ..." in the timeout message."""
    return Wait(predicate, what, timeout_ms)


def js_value(web, js, what, timeout_ms=10000):
    """Run `js` in `web` now; the wait is over when the answer arrives, and it is in .value (whatever it is)."""
    result = Wait(None, what, timeout_ms)
    answered = []

    def on_answer(value):
        result.value = value
        answered.append(True)

    result.predicate = lambda: bool(answered)
    web.evalWithCallback(js, on_answer)
    return result


def js_until(web, js, what, timeout_ms=15000):
    """Run `js` in `web` again and again until it returns something truthy (left in .value)."""
    result = Wait(None, what, timeout_ms)
    pending = []

    def on_answer(value):
        result.value = value
        pending.clear()

    def predicate():
        if not pending:
            pending.append(True)
            web.evalWithCallback(js, on_answer)
        return bool(result.value)

    result.predicate = predicate
    return result


def exposed(widget):
    """True once the window is really on screen (shown, not just constructed)."""
    handle = widget.windowHandle()
    return widget.isVisible() and handle is not None and handle.isExposed()


def run(scenario):
    if _state["finished"]:
        return
    try:
        item = next(scenario)
    except StopIteration:
        return finish()
    except Exception:
        return abort("EXCEPTION: " + traceback.format_exc())
    if isinstance(item, Wait):
        deadline = time.time() + item.timeout_ms / 1000

        def poll():
            if _state["finished"]:
                return
            try:
                ready = item.predicate()
            except Exception:
                return abort(f"EXCEPTION while waiting for {item.what}: " + traceback.format_exc())
            if ready:
                run(scenario)
            elif time.time() > deadline:
                abort(f"TIMEOUT: waited {item.timeout_ms / 1000:g} s for {item.what}")
            else:
                QTimer.singleShot(POLL_MS, poll)

        poll()
    else:
        QTimer.singleShot(int(item), lambda: run(scenario))


# --- watchdogs -------------------------------------------------------------------------------------------------

def _modal_text(modal):
    if isinstance(modal, QMessageBox):
        return " ".join(part for part in (modal.text(), modal.informativeText()) if part)
    return " | ".join(label.text() for label in modal.findChildren(QLabel) if label.text())


def _tick():
    elapsed = time.time() - _state["t0"]
    if not _state["finished"]:
        if not _state["profile_open"] and elapsed > STARTUP_S:
            abort(f"TIMEOUT: Anki did not open the profile within {STARTUP_S:g} s")
        elif elapsed > DEADLINE_S:
            abort(f"TIMEOUT: the run took longer than {DEADLINE_S:g} s")
    modal = QApplication.activeModalWidget()
    if modal is not None and not isinstance(modal, QProgressDialog):  # Anki's own busy indicator is expected
        if not _state["finished"]:
            fail("unexpected modal dialog", f"{_describe(modal)}: {_modal_text(modal)[:300]}")
            log("UNEXPECTED MODAL (closed it); snapshots: " + ", ".join(_snapshot_windows("modal")))
        modal.close()


def start(scenario_factory, settle_ms=1500):
    """Arm the watchdogs now (while Anki is still starting) and run the scenario once the profile is open."""
    timer = QTimer(mw)
    timer.timeout.connect(_tick)
    timer.start(500)
    _keep.append(timer)

    def on_profile_open():
        _state["profile_open"] = True
        log("profile open")
        QTimer.singleShot(settle_ms, lambda: run(scenario_factory()))

    gui_hooks.profile_did_open.append(on_profile_open)
