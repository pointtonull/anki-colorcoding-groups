"""Driver add-on for tools/make_screenshots.py. NOT part of the shipped add-on.

make_screenshots.py copies this file to <sandbox>/addons21/zz_screenshots/__init__.py (it sorts after
`colorcoding_groups`, so that add-on is already loaded). Once the sandbox profile is open it builds a demo
note from invented words, drives the add-on's own dialogs and menus, and writes PNGs to $COLORCODING_SHOTS_OUT.
Progress goes to $COLORCODING_SHOTS_LOG; an empty `done` file next to it marks a finished run.
"""
import importlib
import os
import traceback

from aqt import dialogs, gui_hooks, mw
from PyQt6.QtCore import QPoint, QRect, QTimer
from PyQt6.QtGui import QImage, QPainter
from PyQt6.QtWidgets import QApplication, QMenu, QPushButton

OUT = os.environ["COLORCODING_SHOTS_OUT"]
LOG = os.environ["COLORCODING_SHOTS_LOG"]

FRONT = "Who published the first computer algorithm in 1843?"
# "Manchester" is deliberately not in the example config: it is the word we select and assign.
BACK = ("Ada Lovelace, working with Charles Babbage in London. "
        "Alan Turing studied in Cambridge and later worked in Manchester.")
PICK_WORD = "Manchester"

SELECT_JS = """
(function (word) {
    var hosts = Array.prototype.filter.call(document.querySelectorAll('*'), function (e) { return e.shadowRoot; });
    for (var i = 0; i < hosts.length; i++) {
        var root = hosts[i].shadowRoot;
        var walker = document.createTreeWalker(root, NodeFilter.SHOW_TEXT);
        var node;
        while ((node = walker.nextNode())) {
            var at = node.data.indexOf(word);
            if (at < 0) continue;
            var editable = root.querySelector('[contenteditable]');
            if (editable) editable.focus();
            var range = document.createRange();
            range.setStart(node, at);
            range.setEnd(node, at + word.length);
            var sel = root.getSelection ? root.getSelection() : window.getSelection();
            sel.removeAllRanges();
            sel.addRange(range);
            return 'selected';
        }
    }
    return 'not found';
})(%r)
"""

state = {}


def log(message):
    with open(LOG, "a", encoding="utf-8") as file:
        file.write(message + "\n")


def neutral(window):
    """macOS paints the default button and the focus ring in the user's accent colour; drop both."""
    for button in window.findChildren(QPushButton):
        button.setAutoDefault(False)
        button.setDefault(False)
    focused = window.focusWidget()
    if focused is not None:
        focused.clearFocus()


def save(widget, name):
    neutral(widget)
    pixmap = widget.grab()
    ok = pixmap.save(os.path.join(OUT, name))
    log(f"saved {name}: ok={ok} {pixmap.width()}x{pixmap.height()}px dpr={pixmap.devicePixelRatio()}")


def save_composite(base, menus, name):
    """The window with its popup menus laid over it where they sit on screen (popups are separate windows)."""
    parts = [(base.grab().toImage(), QRect(base.mapToGlobal(QPoint(0, 0)), base.size()))]
    # widget.grab() leaves a popup's background out; grabbing the popup's own window (only that window,
    # never the desktop around it) keeps it.
    parts += [(menu.screen().grabWindow(int(menu.winId())).toImage(), menu.geometry()) for menu in menus]
    dpr = parts[0][0].devicePixelRatio()
    left = min(g.left() for _, g in parts)
    top = min(g.top() for _, g in parts)
    right = max(g.right() for _, g in parts) + 1
    bottom = max(g.bottom() for _, g in parts) + 1
    canvas = QImage(int((right - left) * dpr), int((bottom - top) * dpr), QImage.Format.Format_ARGB32)
    canvas.setDevicePixelRatio(dpr)
    canvas.fill(QApplication.palette().window().color())
    painter = QPainter(canvas)
    for image, geometry in parts:
        painter.drawImage(QPoint(geometry.left() - left, geometry.top() - top), image)
    painter.end()
    log(f"saved {name}: ok={canvas.save(os.path.join(OUT, name))} {canvas.width()}x{canvas.height()}px")


def cc():
    return importlib.import_module("colorcoding_groups")


# Each step returns the milliseconds to wait before the next one.

def step_theme():
    from aqt.theme import Theme, theme_manager
    mw.pm.set_theme(Theme.DARK)  # the example colours are tuned for a dark background
    theme_manager.apply_style()
    return 800


def step_open_editor():
    add = dialogs.open("AddCards", mw)
    state["add"] = add
    add.resize(1000, 460)
    add.move(80, 80)
    note = mw.col.new_note(mw.col.models.by_name("Basic"))
    note["Front"] = FRONT
    note["Back"] = BACK
    add.set_note(note, mw.col.decks.id("Default"))
    return 3000


def step_colour():
    cc().performConversion(state["add"].editor)  # what the "C" button does
    return 2000


def step_shot_editor():
    save(state["add"], "editor.png")
    return 300


def step_select():
    state["add"].editor.web.evalWithCallback(SELECT_JS % PICK_WORD, lambda result: log(f"select: {result}"))
    return 800


def step_context_menu():
    # Anki's own contextMenuEvent needs a real Chromium click, so build the menu the way it does: its
    # standard entries (stand-ins here), then the editor_will_show_context_menu hook that add-ons use.
    web = state["add"].editor.web
    menu = QMenu(web)
    for label in ("Cut", "Copy", "Paste"):
        menu.addAction(label)
    gui_hooks.editor_will_show_context_menu(web, menu)
    menu.popup(web.mapToGlobal(QPoint(150, 215)))
    action = next(a for a in menu.actions() if "color group" in a.text())
    menu.setActiveAction(action)  # opens the submenu
    state["menu"], state["action"] = menu, action
    return 1000


def step_shot_context_menu():
    menu, submenu = state["menu"], state["action"].menu()
    log(f"menu visible={menu.isVisible()} submenu visible={submenu.isVisible()}")
    save_composite(state["add"], [menu, submenu], "context-menu.png")
    menu.close()
    return 300


def step_open_picker():
    dialog = cc().GroupPickerDialog(PICK_WORD, cc().get_groups(), parent=state["add"])
    dialog.resize(320, 300)  # the dialogs are resizable; trim the empty space below the list
    dialog.show()
    state["dialog"] = dialog
    return 700


def step_shot_picker():
    save(state["dialog"], "group-picker.png")
    state["dialog"].close()
    return 300


def step_open_manager():
    config = cc().read_config()
    dialog = cc().GroupManagerDialog(config["groups"], config["entries"], parent=state["add"])
    dialog.resize(480, 330)
    dialog.show()
    state["dialog"] = dialog
    return 700


def step_shot_manager():
    save(state["dialog"], "group-manager.png")
    state["dialog"].close()
    return 300


def step_open_settings():
    cc().complex_settings_gui()
    window = mw.myWidget
    window.resize(760, 500)  # wide enough for "Charles Babbage" in the Word column
    return 1000


def step_shot_settings():
    save(mw.myWidget, "settings.png")
    mw.myWidget.close()
    return 300


def step_finish():
    log("done")
    open(os.path.join(os.path.dirname(LOG), "done"), "w").close()
    state["add"].close()
    mw.close()
    return 0


STEPS = [step_theme, step_open_editor, step_colour, step_shot_editor, step_select, step_context_menu,
         step_shot_context_menu, step_open_picker, step_shot_picker, step_open_manager, step_shot_manager,
         step_open_settings, step_shot_settings, step_finish]


def run(steps):
    if not steps:
        return
    step, rest = steps[0], steps[1:]
    try:
        delay = step() or 0
    except Exception:
        log(f"{step.__name__} failed:\n{traceback.format_exc()}")
        delay = 100  # keep going: one failed shot should not lose the others
    QTimer.singleShot(delay, lambda: run(rest))


def on_profile_open():
    log("profile open")
    QTimer.singleShot(2000, lambda: run(STEPS))


gui_hooks.profile_did_open.append(on_profile_open)
