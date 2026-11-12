"""Driver add-on for tools/make_screenshots.py. NOT part of the shipped add-on.

make_screenshots.py copies this file to <sandbox>/addons21/zz_driver/__init__.py (it sorts after the add-on, so that is
already loaded). Once the sandbox profile is open it builds a demo note from invented words, drives the add-on's own
dialogs and menus, and writes PNGs to $COLORCODING_SHOTS_OUT. Every step waits for the thing it needs (see
driver_kit.py), and a wait that never ends stops the run with a TIMEOUT that says what was missing.
"""
import importlib
import os

from aqt import dialogs, gui_hooks, mw
from PyQt6.QtCore import QPoint, QRect
from PyQt6.QtGui import QImage, QPainter
from PyQt6.QtWidgets import QApplication, QMenu, QPushButton

from . import driver_kit as kit

OUT = os.environ["COLORCODING_SHOTS_OUT"]

FRONT = "Who published the first computer algorithm in 1843?"
# "Manchester" is deliberately not in the example config: it is the word we select and assign.
BACK = ("Ada Lovelace, working with Charles Babbage in London. "
        "Alan Turing studied in Cambridge and later worked in Manchester.")
PICK_WORD = "Manchester"
EXPECTED_SPANS = 6  # 1843, Ada Lovelace, Charles Babbage, London, Alan Turing, Cambridge

FIELDS_READY_JS = ("(function(){var n=0;document.querySelectorAll('*').forEach(function(e){if(e.shadowRoot){"
                   "var ed=e.shadowRoot.querySelector('anki-editable');if(ed&&ed.textContent.length>0)n++;}});"
                   "return n>=2;})()")
SPANS_READY_JS = ("(function(){var n=0;document.querySelectorAll('*').forEach(function(e){if(e.shadowRoot)"
                  "n+=e.shadowRoot.querySelectorAll('.ColorCoding').length;});return n>=%d;})()" % EXPECTED_SPANS)
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
            return String(sel).indexOf(word) >= 0;
        }
    }
    return false;
})(%r)
"""


# --- saving ----------------------------------------------------------------------------------------------------

def varied(image, minimum=3):
    """A capture that went wrong is flat (all one colour); a real window has several in any sample."""
    width, height = image.width(), image.height()
    sample = {image.pixel(x * (width - 1) // 29, y * (height - 1) // 29) for x in range(30) for y in range(30)}
    return len(sample) >= minimum


def store(image, name):
    kit.check(f"{name}: the capture is not blank", varied(image), f"{image.width()}x{image.height()}")
    kit.check(f"{name}: saved", image.save(os.path.join(OUT, name)))
    kit.log(f"saved {name}: {image.width()}x{image.height()}px")


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
    store(widget.grab().toImage(), name)


def save_popup(menu, name):
    # widget.grab() leaves a popup's background out; grabbing the popup's own window (only that window, never the
    # desktop around it) keeps it.
    store(menu.screen().grabWindow(int(menu.winId())).toImage(), name)


def save_composite(base, menus, name):
    """The window with its popup menus laid over it where they sit on screen (popups are separate windows)."""
    parts = [(base.grab().toImage(), QRect(base.mapToGlobal(QPoint(0, 0)), base.size()))]
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
    store(canvas, name)


def shoot_dialog(dialog, name, what, size=None):
    """Show a dialog, wait until it is really on screen, photograph it, close it. The dialogs are resizable, so
    `size` trims the empty space the default size leaves."""
    kit.step(what)
    if size:
        dialog.resize(*size)
    dialog.show()
    yield kit.wait(lambda: kit.exposed(dialog), f"{what} to appear on screen")
    yield 400  # let it finish painting
    save(dialog, name)
    dialog.close()
    yield 200


# --- the scenario ----------------------------------------------------------------------------------------------

def scenario():
    cc = importlib.import_module(os.environ.get("COLORCODING_ADDON", "colorcoding_groups"))

    kit.step("dark theme")
    from aqt.theme import Theme, theme_manager
    mw.pm.set_theme(Theme.DARK)  # the example colours are tuned for a dark background
    theme_manager.apply_style()
    yield 800

    kit.step("open the Add window with the demo note")
    add = dialogs.open("AddCards", mw)
    add.resize(1000, 460)
    add.move(80, 80)
    note = mw.col.new_note(mw.col.models.by_name("Basic"))
    note["Front"], note["Back"] = FRONT, BACK
    add.set_note(note, mw.col.decks.id("Default"))
    yield kit.wait(lambda: kit.exposed(add), "the Add window to appear on screen")
    yield kit.js_until(add.editor.web, FIELDS_READY_JS, "both note fields to render in the editor", 20000)

    kit.step("colour the words (what the C button does)")
    cc.performConversion(add.editor)
    yield kit.js_until(add.editor.web, SPANS_READY_JS, f"{EXPECTED_SPANS} coloured words to appear in the editor")
    yield 500  # let the colours paint
    save(add, "editor.png")

    kit.step("select a word and open the right-click menu")
    selected = kit.js_until(add.editor.web, SELECT_JS % PICK_WORD, f"the word {PICK_WORD} to be selected in the editor")
    yield selected
    yield 500
    web = add.editor.web
    menu = QMenu(web)
    # Anki's own contextMenuEvent needs a real Chromium click, so build the menu the way it does: its standard
    # entries (stand-ins here), then the editor_will_show_context_menu hook that add-ons use.
    for label in ("Cut", "Copy", "Paste"):
        menu.addAction(label)
    gui_hooks.editor_will_show_context_menu(web, menu)
    menu.popup(web.mapToGlobal(QPoint(150, 215)))
    yield kit.wait(menu.isVisible, "the right-click menu to open")
    action = next((a for a in menu.actions() if "color group" in a.text()), None)
    kit.check("the add-on's 'Set color group' entry is in the menu", action is not None, [a.text() for a in menu.actions()])
    if action is not None:
        menu.setActiveAction(action)  # opens the submenu
        submenu = action.menu()
        yield kit.wait(submenu.isVisible, "the 'Set color group' submenu to open")
        yield 300
        save_composite(add, [menu, submenu], "context-menu.png")
    menu.close()
    yield 300

    yield from shoot_dialog(cc.GroupPickerDialog(PICK_WORD, cc.get_groups(), parent=add), "group-picker.png",
                            "the group picker", size=(320, 300))
    config = cc.read_config()
    yield from shoot_dialog(cc.GroupManagerDialog(config["groups"], config["entries"], parent=add),
                            "group-manager.png", "the group manager", size=(480, 330))

    kit.step("the settings window")
    cc.complex_settings_gui()
    window = mw.myWidget
    window.resize(760, 500)  # wide enough for "Charles Babbage" in the Word column
    yield kit.wait(lambda: kit.exposed(window), "the settings window to appear on screen")
    yield 400
    save(window, "settings.png")
    window.close()
    yield 200

    for name in ("Languages", "Medicine", "Medicine::Drugs", "Programming", "Travel"):
        mw.col.decks.id(name)  # decks to choose from
    cc.write_settings({"autoColorAllFields": True, "autoColorDecks": ["Medicine", "Programming"]})
    yield from shoot_dialog(cc.SettingsDialog(add), "settings-decks.png", "the settings dialog with the deck scope",
                            size=(440, 380))

    kit.step("the deck gear menu")
    menu = QMenu(mw)
    # The deck list's gear menu: Anki's own entries (stand-ins here), then the hook add-ons use.
    for label in ("Rename", "Options", "Export", "Delete"):
        menu.addAction(label)
    gui_hooks.deck_browser_will_show_options_menu(menu, int(mw.col.decks.id("Medicine")))
    menu.popup(QPoint(240, 240))
    yield kit.wait(menu.isVisible, "the deck gear menu to open")
    yield 300
    save_popup(menu, "deck-menu.png")
    menu.close()

    kit.step("done")
    add.set_note(mw.col.new_note(mw.col.models.by_name("Basic")), mw.col.decks.id("Default"))  # blank: no "Discard?" prompt
    add.close()


kit.start(scenario)
