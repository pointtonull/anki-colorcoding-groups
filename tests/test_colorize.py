"""Unit tests for colorize.py (no Anki needed): python3 -m unittest discover -s tests"""
import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import colorize  # noqa: E402

CONFIG = {
    "groups": {
        "Places": {"color": "#798aff", "bold": False},
        "People": {"color": "#ff44a1", "bold": True},
        "Code": {"color": "#3ad459", "bold": False},
        "NoColour": {"color": "", "bold": True},
    },
    "entries": [
        {"word": "Paris", "group": "Places"},
        {"word": "Europe", "group": "Places"},
        {"word": "south-east Europe", "group": "Places"},
        {"word": "Ada Lovelace", "group": "People"},
        {"word": "async", "group": "Code"},
        {"word": "C++", "group": "Code"},
        {"word": "->", "group": "Code"},
        {"word": "Q&A", "group": "People"},
        {"word": "café", "group": "Places"},
        {"word": "ghost", "group": "NoColour"},
        {"word": "orphan", "group": "Deleted"},
    ],
}
MATCHER = colorize.make_matcher(CONFIG)


def span(text, group, name=None):
    return f'<span class="ColorCoding" data-name="{name or text}" data-group="{group}">{text}</span>'


def paint(source):
    return colorize.colorize_html(source, MATCHER)


class ColorizeTests(unittest.TestCase):
    def test_wraps_a_word_with_class_and_group_only(self):
        self.assertEqual(paint("Visit Paris."), "Visit " + span("Paris", "Places") + ".")

    def test_no_colour_is_stored_in_the_note(self):
        self.assertNotIn("style", paint("Paris and Ada Lovelace"))

    def test_case_insensitive_and_keeps_the_written_case(self):
        self.assertEqual(paint("PARIS"), span("PARIS", "Places"))

    def test_longest_term_wins(self):
        self.assertEqual(paint("south-east Europe"), span("south-east Europe", "Places"))
        self.assertEqual(paint("Europe"), span("Europe", "Places"))

    def test_phrases(self):
        self.assertEqual(paint("Ada Lovelace"), span("Ada Lovelace", "People"))

    def test_word_boundaries(self):
        self.assertEqual(paint("asynchronous"), "asynchronous")
        self.assertEqual(paint("async"), span("async", "Code"))
        self.assertEqual(paint("(async)"), "(" + span("async", "Code") + ")")

    def test_terms_made_of_punctuation(self):
        self.assertEqual(paint("C++ is"), span("C++", "Code") + " is")
        # Anki stores a literal ">" as "&gt;": the raw text is kept, the data-name is the decoded word.
        self.assertEqual(paint("a -&gt; b"), "a " + span("-&gt;", "Code", name="-&gt;") + " b")

    def test_accented_term(self):
        self.assertEqual(paint("un café."), "un " + span("café", "Places") + ".")
        self.assertEqual(paint("un cafe."), "un cafe.")

    def test_entities_inside_and_around_words(self):
        self.assertEqual(paint("Paris&nbsp;Texas"), span("Paris", "Places") + "&nbsp;Texas")
        self.assertEqual(paint("Q&amp;A"), span("Q&amp;A", "People", name="Q&amp;A"))
        self.assertEqual(paint("caf&eacute;"), span("caf&eacute;", "Places", name="café"))

    def test_attribute_values_are_escaped(self):
        config = {"groups": {'A "quoted" & group': {"color": "red"}}, "entries": [{"word": "x", "group": 'A "quoted" & group'}]}
        out = colorize.colorize_html("x", colorize.make_matcher(config))
        self.assertIn('data-group="A &quot;quoted&quot; &amp; group"', out)

    def test_tags_and_attributes_are_not_touched(self):
        source = '<img src="Paris.jpg" alt="Paris"><a href="Paris">Paris</a>'
        self.assertEqual(paint(source), '<img src="Paris.jpg" alt="Paris"><a href="Paris">' + span("Paris", "Places") + "</a>")

    def test_words_inside_formatting_are_coloured_inside_it(self):
        self.assertEqual(paint("<b>Paris</b>"), "<b>" + span("Paris", "Places") + "</b>")

    def test_a_phrase_split_by_a_tag_is_not_matched_but_its_words_stay_plain(self):
        self.assertEqual(paint("Ada <b>Lovelace</b>"), "Ada <b>Lovelace</b>")

    def test_script_style_and_comments_are_left_alone(self):
        source = "<style>.x{content:'Paris'}</style><script>var Paris=1</script><!-- Paris -->"
        self.assertEqual(paint(source), source)

    def test_groups_without_a_colour_or_that_do_not_exist_are_not_applied(self):
        self.assertEqual(paint("ghost orphan"), "ghost orphan")

    def test_a_repeated_word_takes_its_last_group(self):
        config = {"groups": {"A": {"color": "red"}, "B": {"color": "blue"}},
                  "entries": [{"word": "x", "group": "A"}, {"word": "X", "group": "B"}]}
        self.assertEqual(colorize.colorize_html("x", colorize.make_matcher(config)), span("x", "B"))

    # --- re-running -------------------------------------------------------------------------------------------

    def test_idempotent(self):
        once = paint("Paris, Ada Lovelace and async")
        self.assertEqual(paint(once), once)

    def test_replaces_spans_written_by_the_old_version(self):
        old = '<span style="color: #798aff; font-weight: normal;" class="ColorCoding" data-name="Paris" data-group="Places">Paris</span>'
        self.assertEqual(paint(old), span("Paris", "Places"))

    def test_spans_of_a_word_no_longer_configured_are_stripped(self):
        self.assertEqual(paint(span("Rome", "Places")), "Rome")

    def test_other_spans_are_kept_and_balanced(self):
        source = '<span class="note">x <span class="ColorCoding" data-group="Places">Paris</span> y</span>'
        self.assertEqual(paint(source), '<span class="note">x ' + span("Paris", "Places") + " y</span>")

    def test_strip(self):
        coloured = "see " + span("Paris", "Places") + " and <b>" + span("Ada Lovelace", "People") + "</b>"
        self.assertEqual(colorize.strip_html(coloured), "see Paris and <b>Ada Lovelace</b>")

    def test_strip_keeps_markup_inside_one_of_our_spans(self):
        self.assertEqual(colorize.strip_html('<span class="ColorCoding"><i>Paris</i></span>!'), "<i>Paris</i>!")

    def test_nothing_configured_only_strips(self):
        empty = colorize.make_matcher({"groups": {}, "entries": []})
        self.assertEqual(colorize.colorize_html("a " + span("Paris", "Places"), empty), "a Paris")

    # --- text that is not prose -------------------------------------------------------------------------------

    def test_cloze_text_is_coloured_but_its_syntax_and_hint_are_not(self):
        self.assertEqual(paint("{{c1::Paris}}"), "{{c1::" + span("Paris", "Places") + "}}")
        self.assertEqual(paint("{{c1::Paris::Paris}}"), "{{c1::" + span("Paris", "Places") + "::Paris}}")

    def test_mathjax_sound_and_latex_are_untouched(self):
        for source in (r"\(Paris\)", r"\[Paris\]", "[sound:Paris.mp3]", "[latex]Paris[/latex]", "[$]Paris[/$]", "[$$]Paris[/$$]"):
            self.assertEqual(paint(source), source, source)

    def test_text_next_to_mathjax_is_still_coloured(self):
        self.assertEqual(paint(r"Paris \(Paris\) Paris"), span("Paris", "Places") + r" \(Paris\) " + span("Paris", "Places"))

    def test_empty_and_plain_inputs(self):
        self.assertEqual(paint(""), "")
        self.assertEqual(paint("nothing to see"), "nothing to see")


class DeckScopeTests(unittest.TestCase):
    def test_empty_scope_means_every_deck(self):
        self.assertTrue(colorize.deck_in_scope("Anything::Goes", []))
        self.assertTrue(colorize.deck_in_scope("Anything", None))

    def test_a_deck_covers_itself_and_its_subdecks(self):
        scope = ["Medicine"]
        self.assertTrue(colorize.deck_in_scope("Medicine", scope))
        self.assertTrue(colorize.deck_in_scope("medicine::Drugs::VEGF", scope))
        self.assertFalse(colorize.deck_in_scope("Medicine2", scope))
        self.assertFalse(colorize.deck_in_scope("Languages::Medicine", scope))

    def test_a_subdeck_does_not_cover_its_parent(self):
        self.assertFalse(colorize.deck_in_scope("Medicine", ["Medicine::Drugs"]))

    def test_unknown_deck(self):
        self.assertFalse(colorize.deck_in_scope(None, ["Medicine"]))


class StylesheetTests(unittest.TestCase):
    def test_one_rule_per_coloured_group_in_name_order(self):
        css = colorize.build_css(CONFIG["groups"])
        rules = [line for line in css.splitlines() if line.startswith(".ColorCoding")]
        self.assertEqual(rules, [
            '.ColorCoding[data-group="Code"] { color: #3ad459; font-weight: normal; }',
            '.ColorCoding[data-group="People"] { color: #ff44a1; font-weight: bold; }',
            '.ColorCoding[data-group="Places"] { color: #798aff; font-weight: normal; }',
        ])

    def test_group_names_are_escaped_for_css(self):
        css = colorize.build_css({'say "hi" \\ now': {"color": "red"}})
        self.assertIn(r'[data-group="say \"hi\" \\ now"]', css)

    def test_a_colour_cannot_inject_css(self):
        css = colorize.build_css({"g": {"color": "red;} body{display:none"}})
        self.assertNotIn("display", css)
        self.assertNotIn(".ColorCoding[", css)

    def test_legacy_bare_colour_strings_work(self):
        self.assertIn("color: MediumTurquoise", colorize.build_css({"g": "MediumTurquoise"}))

    def test_stylesheet_is_stable(self):
        self.assertEqual(colorize.build_css(CONFIG["groups"]), colorize.build_css(dict(reversed(list(CONFIG["groups"].items())))))


class ImportLineTests(unittest.TestCase):
    CSS = ".card {\n  font-family: arial;\n}\n"

    def test_add_puts_the_import_first_and_is_idempotent(self):
        added = colorize.add_import(self.CSS)
        self.assertTrue(added.startswith('@import url("_colorcoding_groups.css");'))
        self.assertTrue(colorize.has_import(added))
        self.assertEqual(colorize.add_import(added), added)

    def test_remove_restores_the_original(self):
        self.assertEqual(colorize.remove_import(colorize.add_import(self.CSS)), self.CSS)

    def test_charset_stays_first(self):
        added = colorize.add_import('@charset "utf-8";\n' + self.CSS)
        self.assertTrue(added.startswith('@charset "utf-8";\n@import'))

    def test_recognises_other_spellings_of_the_import(self):
        for line in ("@import url('_colorcoding_groups.css');", "@import url(_colorcoding_groups.css);"):
            self.assertTrue(colorize.has_import(line + "\n" + self.CSS), line)

    def test_not_fooled_by_other_imports(self):
        self.assertFalse(colorize.has_import('@import url("_other.css");\n' + self.CSS))
        self.assertFalse(colorize.has_import(self.CSS))


if __name__ == "__main__":
    unittest.main()
