"""Driver add-on for tools/check_in_anki.py. NOT part of the shipped add-on.

Runs inside a throwaway Anki (see sandbox.py) and checks, against the real thing, what unit tests cannot: the shared
stylesheet, bulk colouring with undo, the editor, the deck scope and what the reviewer actually shows. Results go to
$COLORCODING_SHOTS_LOG as PASS/FAIL lines; every wait is bounded (see driver_kit.py).
"""
import importlib
import os
import types

from aqt import dialogs, mw
from PyQt6.QtWidgets import QMenu

from . import driver_kit as kit

# What an editor field holds / how its coloured spans look to the browser engine.
FIELDS_JS = ("(function(){var out=[];document.querySelectorAll('*').forEach(function(e){if(e.shadowRoot){"
             "var ed=e.shadowRoot.querySelector('anki-editable');if(ed)out.push(ed.innerHTML);}});return out;})()")
EDITOR_SPANS_JS = ("(function(){var out=[];document.querySelectorAll('*').forEach(function(e){if(e.shadowRoot){"
                   "e.shadowRoot.querySelectorAll('.ColorCoding').forEach(function(s){var c=getComputedStyle(s);"
                   "out.push([s.dataset.group,c.color,c.fontWeight]);});}});return out;})()")
REVIEWER_SPANS_JS = ("(function(){var out=[];document.querySelectorAll('.ColorCoding').forEach(function(s){"
                     "var c=getComputedStyle(s);out.push([s.dataset.group,c.color,c.fontWeight]);});return out;})()")
FIELDS_READY_JS = ("(function(){var n=0;document.querySelectorAll('*').forEach(function(e){if(e.shadowRoot){"
                   "var ed=e.shadowRoot.querySelector('anki-editable');if(ed)n++;}});return n>=2;})()")


def scenario():
    addon = os.environ.get("COLORCODING_ADDON", "colorcoding_groups")
    cc = importlib.import_module(addon)
    colorize = importlib.import_module(addon + ".colorize")
    col = mw.col
    tips = []
    cc.tooltip = lambda message, *args, **kwargs: tips.append(message)
    cc.askUser = lambda *args, **kwargs: True
    import aqt.operations
    aqt.operations.show_exception = lambda *args, **kwargs: (  # a failed operation would pop up a dialog
        kit.log("OPERATION ERROR: " + repr(kwargs.get("exception"))), tips.append("error"))
    config = cc.read_config()
    matcher = colorize.make_matcher(config)
    paint = lambda text: colorize.colorize_html(text, matcher)  # noqa: E731
    css_path = os.path.join(col.media.dir(), colorize.CSS_FILENAME)
    rgb = {"Places": "rgb(121, 138, 255)", "People": "rgb(255, 68, 161)", "Dates": "rgb(255, 203, 118)",
           "Code": "rgb(58, 212, 89)"}
    check = kit.check

    # --- the shared stylesheet ---------------------------------------------------------------------------
    kit.step("stylesheet at profile open")
    with open(css_path, encoding="utf-8") as file:
        check("stylesheet written when the profile opens", file.read() == colorize.build_css(config["groups"]))

    # --- demo notes ----------------------------------------------------------------------------------------
    kit.step("demo notes")
    basic = col.models.by_name("Basic")
    check("note type starts without the @import", not colorize.has_import(basic["css"]))
    demo, sub, other = col.decks.id("Demo"), col.decks.id("Demo::Sub"), col.decks.id("Other")

    def add(front, deck):
        note = col.new_note(basic)
        note["Front"], note["Back"] = front, ""
        col.add_note(note, deck)
        return note.id

    legacy = ('<span style="color: #798aff; font-weight: normal;" class="ColorCoding" data-name="Paris" '
              'data-group="Places">Paris</span> in 1843')
    n1 = add("Paris and async, but not asynchronous", demo)
    n2 = add(legacy, sub)
    n3 = add("Paris", other)
    n4 = add("{{c1::Ada Lovelace::person}} wrote in Paris \\(Paris\\)", demo)
    front = lambda nid: col.get_note(nid)["Front"]  # noqa: E731
    original = {nid: front(nid) for nid in (n1, n2, n3, n4)}

    def bulk(remove):
        tips.clear()
        cc.bulk_for_deck(demo, remove=remove)
        return kit.wait(lambda: tips, f"the bulk {'removal' if remove else 'colouring'} of the Demo deck to finish", 20000)

    # --- bulk: colour a deck and its subdecks, undo, redo, remove ------------------------------------------
    kit.step("bulk colouring")
    yield bulk(False)
    check("bulk: reports 3 of 3 notes", "Colored 3 of 3 notes" in tips[-1], tips)
    for nid in (n1, n2, n4):
        check(f"bulk: note {nid} equals the Python output", front(nid) == paint(original[nid]), front(nid))
    check("bulk: the old inline style is gone", "style=" not in front(n2), front(n2))
    check("bulk: cloze syntax and MathJax left alone",
          front(n4).startswith("{{c1::<span") and "::person}}" in front(n4) and "\\(Paris\\)" in front(n4), front(n4))
    check("bulk: a note outside the deck is untouched", front(n3) == "Paris", front(n3))
    check("bulk: the note type now imports the stylesheet", colorize.has_import(col.models.get(basic["id"])["css"]))
    check("bulk: one undo step named Color Coding", col.undo_status().undo == "Color Coding", col.undo_status().undo)

    kit.step("undo and redo")
    col.undo()
    check("undo restores every note", all(front(nid) == original[nid] for nid in original))
    kit.log("info  note type still imports after undo: " + str(colorize.has_import(col.models.get(basic["id"])["css"])))
    col.redo()
    check("redo colours them again", front(n1) == paint(original[n1]))

    kit.step("bulk removal")
    yield bulk(True)
    check("remove: no spans left", all("ColorCoding" not in front(nid) for nid in (n1, n2, n4)))
    check("remove: the words themselves are kept", front(n1) == original[n1] and front(n2) == "Paris in 1843")
    yield bulk(False)
    check("colouring again after removing works", front(n1) == paint(original[n1]))

    # --- menus ---------------------------------------------------------------------------------------------
    kit.step("menus")
    menu = QMenu()
    cc.on_deck_options_menu(menu, demo)
    texts = [action.text() for action in menu.actions()]
    check("deck options menu has both entries", sum("Color Coding" in text for text in texts) == 2, texts)
    browser = dialogs.open("Browser", mw)
    yield kit.wait(lambda: kit.exposed(browser), "the Browser to appear on screen")
    texts = [action.text() for action in browser.form.menu_Notes.actions()]
    check("browser Notes menu has both entries", sum("Color Coding" in text for text in texts) == 2, texts)
    browser.close()

    # --- the settings dialog keeps the chosen decks ----------------------------------------------------------
    kit.step("settings dialog")
    cc.write_settings({"autoColorAllFields": False, "somethingElse": 1})
    dialog = cc.SettingsDialog()
    for row in range(dialog.deckList.count()):
        item = dialog.deckList.item(row)
        item.setSelected(item.text() in ("Demo", "Other"))
    dialog.autoColorCheckBox.setChecked(True)
    dialog.save_settings()
    saved = cc.read_settings()
    check("settings dialog: saves the switch, the chosen decks and keeps other keys",
          saved.get("autoColorAllFields") is True and sorted(saved.get("autoColorDecks", [])) == ["Demo", "Other"]
          and saved.get("somethingElse") == 1, saved)
    reopened = cc.SettingsDialog()
    check("settings dialog: reopens with the same decks highlighted",
          sorted(item.text() for item in reopened.deckList.selectedItems()) == ["Demo", "Other"])

    # --- editor: output matches the Python colouring, and the colours really show ----------------------------
    kit.step("editor")
    add_dialog = dialogs.open("AddCards", mw)
    yield kit.wait(lambda: kit.exposed(add_dialog), "the Add window to appear on screen")
    web = add_dialog.editor.web

    def load(text, deck_id):
        note = col.new_note(basic)
        note["Front"] = text
        add_dialog.set_note(note, deck_id)

    sample = "Ada Lovelace used Paris and async in 1843; asynchronous stays plain."
    cc.write_settings({"autoColorAllFields": True, "autoColorDecks": []})
    load(sample, demo)
    yield kit.js_until(web, FIELDS_READY_JS, "the editor fields to render", 20000)
    fields = kit.js_until(web, "(function(){var o=%s;return o[0].indexOf('ColorCoding')>=0?o:null;})()" % FIELDS_JS,
                          "the editor to colour the sample note automatically", 15000)
    yield fields
    spans = kit.js_value(web, EDITOR_SPANS_JS, "the colours of the spans in the editor")
    yield spans
    editor_html = fields.value[0].removesuffix("<br>")  # the editor ends a field with a <br> of its own
    check("editor: field HTML equals the Python output", editor_html == paint(sample), fields.value)
    check("editor: no inline style in the field", "style=" not in editor_html)
    got = {(group, color) for group, color, _weight in spans.value}
    check("editor: spans render in their group's colour", bool(got) and all(color == rgb[group] for group, color in got), spans.value)
    bold = {group: weight for group, _color, weight in spans.value}
    check("editor: People bold, Places normal",
          bold.get("People") in ("700", "bold") and bold.get("Places") in ("400", "normal"), bold)

    # --- deck scope for automatic colouring ----------------------------------------------------------------
    kit.step("deck scope")
    cc.write_settings({"autoColorAllFields": True, "autoColorDecks": ["Other"]})
    load(sample, demo)
    yield kit.js_until(web, FIELDS_READY_JS, "the editor fields to render again", 20000)
    yield 2500  # give automatic colouring (which should not happen here) time to happen
    fields = kit.js_value(web, FIELDS_JS, "the editor field HTML")
    yield fields
    check("scope: a deck outside the scope is not auto-coloured", "ColorCoding" not in fields.value[0], fields.value)
    cc.write_settings({"autoColorAllFields": True, "autoColorDecks": ["Demo"]})
    load(sample, sub)
    yield kit.js_until(web, "(function(){var o=%s;return o[0].indexOf('ColorCoding')>=0;})()" % FIELDS_JS,
                       "the subdeck note to be auto-coloured (its parent deck is in the scope)", 15000)
    check("scope: a subdeck of a chosen deck is auto-coloured", True)  # the wait above is the check: it times out otherwise
    load("", demo)  # a blank note, so closing the window does not ask "Discard current input?"
    add_dialog.close()

    stub = types.SimpleNamespace(card=col.get_note(n2).cards()[0], note=col.get_note(n2), parentWindow=None)
    check("scope: an existing note's deck is found from its card", cc._editor_deck_name(stub) == "Demo::Sub",
          cc._editor_deck_name(stub))

    # --- reviewer: the @import from media works, and a colour change reaches the card ------------------------
    kit.step("reviewer")
    cc.write_settings({"autoColorAllFields": True, "autoColorDecks": []})
    col.decks.select(demo)

    def review():
        """Load the reviewer from scratch: leave it first, so a card from before cannot be mistaken for the new one."""
        mw.moveToState("overview")
        yield kit.js_until(mw.web, "document.querySelectorAll('.ColorCoding').length === 0",
                           "the previous card to leave the screen", 15000)
        yield 500
        mw.moveToState("review")
        yield kit.js_until(mw.web, "document.querySelectorAll('.ColorCoding').length > 0",
                           "the reviewer to show a card with coloured words", 20000)

    yield from review()
    spans = kit.js_value(mw.web, REVIEWER_SPANS_JS, "the colours of the spans in the reviewer")
    yield spans
    check("reviewer: spans render in their group's colour (via @import)",
          bool(spans.value) and all(color == rgb[group] for group, color, _weight in spans.value), spans.value)

    kit.step("change a group's colour")
    config = cc.read_config()
    config["groups"]["Places"]["color"] = "#112233"
    cc.write_config(config)
    with open(css_path, encoding="utf-8") as file:
        check("a group edit rewrites the stylesheet", "#112233" in file.read())
    yield 11000  # Anki's media server lets the webview cache a stylesheet for 10 s
    yield from review()
    spans = kit.js_value(mw.web, REVIEWER_SPANS_JS, "the colours of the spans in the reviewer, after the edit")
    yield spans
    places = [color for group, color, _weight in spans.value if group == "Places"]
    check("reviewer: the new colour shows without touching a note", bool(places) and set(places) == {"rgb(17, 34, 51)"}, spans.value)


kit.start(scenario)
