"""Driver add-on for tools/check_in_anki.py. NOT part of the shipped add-on.

Runs inside a throwaway Anki (see sandbox.py) and checks, against the real thing, what unit tests cannot: the shared
stylesheet, bulk colouring with undo, the editor, the deck scope and what the reviewer actually shows. Results go to
$COLORCODING_SHOTS_LOG as PASS/FAIL lines; every wait is bounded (see driver_kit.py).
"""
import importlib
import os
import threading
import types

from aqt import dialogs, mw
from PyQt6.QtWidgets import QDialog, QMenu

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

    # --- colour suggestions: the dialog, the OpenAI-compatible request, the privacy gate, failures --------------
    kit.step("colour suggestions")
    from . import fake_embeddings
    fake = fake_embeddings.FakeEmbeddings()
    base_url = fake.start()
    groups = {name: {"color": "#cccccc", "bold": False} for name in ("Places", "Cities", "People", "Scientists", "Code")}
    entries = [{"word": "Paris", "group": "Places"}, {"word": "London", "group": "Cities"},
               {"word": "Ada", "group": "People"}, {"word": "Turing", "group": "Scientists"},
               {"word": "async", "group": "Code"}]
    cc.write_settings({"somethingElse": 1})
    settings_path = os.path.join(cc.addon_folder, "user_files", "settings.json")

    dialog = cc.SuggestColorsDialog(groups, entries)
    dialog.show()
    yield kit.wait(lambda: kit.exposed(dialog), "the Suggest Colors dialog to appear on screen")
    check("suggest dialog: starts on OpenAI with its preset",
          dialog.providerCombo.currentData() == "openai" and dialog.addressEdit.text() == "https://api.openai.com/v1"
          and dialog.modelEdit.text() == "text-embedding-3-small", dialog.addressEdit.text())
    check("suggest dialog: says what would leave the computer, and where to",
          "5 group names" in dialog.privacyLabel.text() and "api.openai.com" in dialog.privacyLabel.text(),
          dialog.privacyLabel.text())

    dialog.providerCombo.setCurrentIndex(dialog.providerCombo.findData("ollama"))
    check("suggest dialog: the Ollama preset is a local address and says so",
          dialog.addressEdit.text() == "http://localhost:11434/v1" and "stays on this computer" in dialog.privacyLabel.text(),
          (dialog.addressEdit.text(), dialog.privacyLabel.text()))
    dialog.providerCombo.setCurrentIndex(dialog.providerCombo.findData("custom"))
    dialog.addressEdit.setText(base_url)
    dialog.modelEdit.setText("fake-model")
    dialog.keyEdit.setText("sk-fake-123")
    dialog.providerCombo.setCurrentIndex(dialog.providerCombo.findData("ollama"))
    dialog.providerCombo.setCurrentIndex(dialog.providerCombo.findData("custom"))
    check("suggest dialog: switching service back keeps what was typed for it",
          (dialog.addressEdit.text(), dialog.modelEdit.text(), dialog.keyEdit.text()) == (base_url, "fake-model", "sk-fake-123"))

    kit.step("suggest: a successful request")
    dialog.suggest()
    yield kit.wait(lambda: dialog.btnApply.isEnabled(), "the colour suggestions to arrive", 20000)
    request = fake.requests[-1]
    check("suggest: posts to /v1/embeddings with the model", request["path"] == "/v1/embeddings"
          and request["body"]["model"] == "fake-model", request)
    check("suggest: sends the key as a bearer token", request["authorization"] == "Bearer sk-fake-123")
    check("suggest: sends each group's name and words, sorted by name",
          request["body"]["input"] == ["Cities: London", "Code: async", "People: Ada", "Places: Paris", "Scientists: Turing"],
          request["body"]["input"])
    order = [name for name, _color in dialog.suggested]
    adjacent = lambda a, b: (order.index(a) - order.index(b)) % 5 in (1, 4)  # noqa: E731
    check("suggest: groups with a similar meaning end up next to each other on the wheel",
          adjacent("Places", "Cities") and adjacent("People", "Scientists"), order)
    check("suggest: every group gets its own valid colour",
          len({color for _name, color in dialog.suggested}) == 5 and all(len(c) == 7 and c[0] == "#" for _n, c in dialog.suggested),
          dialog.suggested)
    check("suggest: the preview table lists them all, with the colours",
          dialog.table.rowCount() == 5 and [dialog.table.item(r, 3).text() for r in range(5)] == [c for _n, c in dialog.suggested])
    dialog.checks["Code"].setChecked(False)
    check("suggest: an unticked group keeps its colour", "Code" not in dialog.get_colors() and len(dialog.get_colors()) == 4)
    saved = cc.read_settings()
    check("suggest: the service is remembered, other settings kept",
          saved.get("colorSuggestions") == {"provider": "custom", "baseUrl": base_url, "model": "fake-model",
                                            "apiKey": "sk-fake-123"} and saved.get("somethingElse") == 1, saved)
    if os.name == "posix":
        check("suggest: settings.json is readable by the user only (it may hold a key)",
              os.stat(settings_path).st_mode & 0o777 == 0o600, oct(os.stat(settings_path).st_mode & 0o777))
    again = cc.SuggestColorsDialog(groups, entries)
    check("suggest dialog: reopens on the remembered service", (again.providerCombo.currentData(), again.addressEdit.text(),
          again.modelEdit.text(), again.keyEdit.text()) == ("custom", base_url, "fake-model", "sk-fake-123"))
    again.close()

    kit.step("suggest: failures say what is wrong")
    fake.mode = "unauthorized"
    dialog.suggest()
    yield kit.wait(lambda: dialog.btnSuggest.isEnabled(), "the rejected request to come back", 20000)
    status = dialog.statusLabel.text()
    check("suggest: a rejected key is explained and the key is not shown",
          "HTTP 401" in status and "Incorrect API key" in status and "sk-fake-123" not in status, status)
    fake.mode = "ok"

    asked = []
    cc.askUser = lambda text, *args, **kwargs: (asked.append(text), False)[1]  # declines: sends nothing
    dialog.addressEdit.setText("https://api.example.com/v1")
    sent_before, saved_before = len(fake.requests), cc.read_settings()
    dialog.suggest()
    check("privacy: a remote service is asked about first, naming the host and the words",
          len(asked) == 1 and "api.example.com" in asked[0] and "Cities: London" in asked[0], asked)
    check("privacy: declining sends nothing, starts nothing and saves nothing",
          dialog.btnSuggest.isEnabled() and len(fake.requests) == sent_before and cc.read_settings() == saved_before)
    dialog.addressEdit.setText(base_url)
    asked.clear()
    cc.askUser = lambda *args, **kwargs: True
    sent_before = len(fake.requests)
    dialog.suggest()
    yield kit.wait(lambda: len(fake.requests) > sent_before and dialog.btnSuggest.isEnabled(), "the local request to finish", 20000)
    check("privacy: a service on this computer is not asked about", asked == [])

    fake.stop()
    dialog.suggest()
    yield kit.wait(lambda: dialog.btnSuggest.isEnabled(), "the unreachable service to be reported", 20000)
    check("suggest: an unreachable service is reported", "Could not reach 127.0.0.1" in dialog.statusLabel.text(),
          dialog.statusLabel.text())

    kit.step("suggest: input problems are caught before any request")
    dialog.addressEdit.setText("ftp://nope")
    dialog.suggest()
    check("suggest: an address that is not http(s) is refused", "not a web address" in dialog.statusLabel.text(), dialog.statusLabel.text())
    dialog.addressEdit.setText(base_url)
    dialog.modelEdit.setText("")
    dialog.suggest()
    check("suggest: a missing model is asked for", "name of an embedding model" in dialog.statusLabel.text(), dialog.statusLabel.text())
    alone = cc.SuggestColorsDialog({"Only": {"color": "#cccccc", "bold": False}}, [])
    alone.suggest()
    check("suggest: one group is not enough", "at least two groups" in alone.statusLabel.text(), alone.statusLabel.text())
    alone.close()
    dialog.close()

    kit.step("suggest: applying in the group manager")

    class Accepting:  # stands in for the dialog: exec() "presses Apply" with these colours
        def __init__(self, *args, **kwargs):
            pass

        def exec(self):
            return QDialog.DialogCode.Accepted

        def get_colors(self):
            return {"Places": "#123456"}

    real_dialog = cc.SuggestColorsDialog
    cc.SuggestColorsDialog = Accepting
    manager = cc.GroupManagerDialog({"Places": {"color": "#000000", "bold": True}, "Code": {"color": "#111111", "bold": False}}, [])
    manager.suggest_colors()
    cc.SuggestColorsDialog = real_dialog
    check("manager: applying changes only the chosen colours and keeps bold",
          manager.groups == {"Places": {"color": "#123456", "bold": True}, "Code": {"color": "#111111", "bold": False}}, manager.groups)
    check("manager: the table shows the new colour", manager.table.item(1, 1).text() == "#123456", manager.table.item(1, 1).text())
    manager.close()

    if os.environ.get("COLORCODING_CHECK_TLS"):  # opt-in: needs the internet (and sends nothing but a dummy word)
        kit.step("TLS from Anki's own Python")
        embeddings = importlib.import_module(addon + ".embeddings")
        outcome = []

        def probe():
            try:
                embeddings.request_embeddings(["x"], "https://api.openai.com/v1", "text-embedding-3-small", timeout=10)
                outcome.append("answered without a key (unexpected)")
            except embeddings.EmbeddingError as error:
                outcome.append(str(error))

        threading.Thread(target=probe, daemon=True).start()
        yield kit.wait(lambda: outcome, "the TLS probe to finish", 20000)
        check("TLS: api.openai.com is reached over https and answers 401 (no key)", "HTTP 401" in outcome[0], outcome[0])


kit.start(scenario)
