"""The Anki-free half of the add-on: colouring stored note HTML and building the shared stylesheet.

Colouring a field means wrapping each configured word in

    <span class="ColorCoding" data-name="Paris" data-group="Places">Paris</span>

The span carries no colour of its own. Colours live in one stylesheet in the collection's media folder
(`build_css`, saved as `CSS_FILENAME`), which every note type pulls in with a single `@import` line
(`add_import`). Changing a group's colour rewrites that one file and every note follows, on every device.

Nothing here imports Anki, so it is unit-tested directly (tests/test_colorize.py). The matching rules mirror
changer_static.js, which colours the editor's live DOM; keep the two in step.
"""
import html
import re

CSS_FILENAME = "_colorcoding_groups.css"  # a leading underscore keeps Anki's "Check Media" from deleting it
IMPORT_LINE = f'@import url("{CSS_FILENAME}");'
IMPORT_MARK = "/* colorcoding-groups */"
SPAN_CLASS = "ColorCoding"

_WORD = "A-Za-z0-9_"  # JavaScript's \w; the editor script uses it, so the two stay consistent


# --- the shared stylesheet -------------------------------------------------------------------------------------

def _css_string(value):
    return value.replace("\\", "\\\\").replace('"', '\\"').replace("\n", "\\A ")


def _css_color(value):
    """The colour if it is plain colour syntax (#rrggbb, a name, rgb(...)), else '' so it can't inject CSS."""
    value = (value or "").strip()
    return value if re.fullmatch(r"[#\w(),.%\s-]+", value) else ""


def build_css(groups):
    """The stylesheet for {group: {"color", "bold"}}: one rule per group, matched on the span's data-group."""
    lines = ["/* Written by the Color Coding Groups add-on from its group list. Edits here are overwritten. */"]
    for name in sorted(groups, key=str.lower):
        group = groups[name] if isinstance(groups[name], dict) else {"color": groups[name]}
        color = _css_color(group.get("color"))
        if not color:
            continue  # such a group is never applied (see make_matcher), so it needs no rule
        weight = "bold" if group.get("bold") else "normal"
        lines.append(f'.{SPAN_CLASS}[data-group="{_css_string(name)}"] {{ color: {color}; font-weight: {weight}; }}')
    return "\n".join(lines) + "\n"


_IMPORT_RE = re.compile(r"""^[ \t]*@import[ \t]+url\(\s*["']?""" + re.escape(CSS_FILENAME) + r"""["']?\s*\)\s*;[^\n]*\n?""",
                        re.M)
_CHARSET_RE = re.compile(r"""^\s*@charset\s+"[^"]*"\s*;[ \t]*\n?""")


def has_import(css):
    return bool(_IMPORT_RE.search(css))


def add_import(css):
    """`css` with our @import first (after an @charset if there is one): CSS only honours imports before any rule."""
    if has_import(css):
        return css
    line = f"{IMPORT_LINE} {IMPORT_MARK}\n"
    charset = _CHARSET_RE.match(css)
    if charset:
        return css[:charset.end()] + line + css[charset.end():]
    return line + css


def remove_import(css):
    return _IMPORT_RE.sub("", css)


# --- which decks -----------------------------------------------------------------------------------------------

def deck_in_scope(deck_name, scope):
    """Whether `deck_name` is covered by `scope`, a list of deck names (each also covers its subdecks).
    An empty scope means every deck. Deck names compare case-insensitively, as Anki's do."""
    if not scope:
        return True
    name = (deck_name or "").lower()
    return any(name == chosen.lower() or name.startswith(chosen.lower() + "::") for chosen in scope)


# --- colouring stored HTML -------------------------------------------------------------------------------------

_MARKUP = re.compile(r"<!--.*?-->|<[^>]*>", re.S)
_ENTITY = re.compile(r"&(?:#[0-9]+|#[xX][0-9a-fA-F]+|[A-Za-z][A-Za-z0-9]*);")
_TAG_NAME = re.compile(r"<(/?)([A-Za-z][A-Za-z0-9-]*)")
_CLASS_ATTR = re.compile(r"""\bclass\s*=\s*(?:"([^"]*)"|'([^']*)'|([^\s>]+))""", re.I)

# Stored text that is not prose and must never get a span inside it. Matched ahead of the words, left to right.
_PROTECTED = r"""
    \{\{c\d+::                 # cloze opener
  | ::[^{}]*?\}\}              # cloze hint and closer
  | \}\}                       # cloze closer
  | \\\(.*?\\\)                # MathJax, inline
  | \\\[.*?\\\]                # MathJax, display
  | \[sound:[^\]]*\]
  | \[anki:[^\]]*\]
  | \[latex\].*?\[/latex\]
  | \[\$\$?\].*?\[/\$\$?\]
"""


class Matcher:
    """The words to colour, compiled once: use `colorize_html(text, matcher)` for each field."""

    def __init__(self, config):
        groups = config.get("groups", {})
        self.group_of = {}
        for entry in config.get("entries", []):
            word, group = entry.get("word", ""), entry.get("group", "")
            group_value = groups.get(group)
            color = group_value.get("color") if isinstance(group_value, dict) else group_value
            if word and _css_color(color):
                self.group_of[word.lower()] = group  # a repeated word: the later entry wins, as in the editor
        terms = sorted(self.group_of, key=len, reverse=True)  # "south-east Europe" beats "Europe"
        if not terms:
            self.pattern = None
            return
        # A word-boundary guard goes only on a side that ends in a word character: terms like "C++" or "->"
        # start or end with punctuation, which delimits itself.
        alternatives = []
        for term in terms:
            lead = f"(?<![{_WORD}])" if re.match(f"[{_WORD}]", term[0]) else ""
            trail = f"(?![{_WORD}])" if re.match(f"[{_WORD}]", term[-1]) else ""
            alternatives.append(lead + re.escape(term) + trail)
        self.pattern = re.compile(f"(?P<skip>{_PROTECTED})|(?P<hit>{'|'.join(alternatives)})", re.I | re.X | re.S)
        # re.X ignores whitespace and '#' inside the pattern, so re.escape'd terms stay literal: escape() covers both


def make_matcher(config):
    """Matcher for a normalised config {"groups": {...}, "entries": [...]}. Entries whose group is missing or
    has no colour are left out: those words stay uncoloured, as in the editor."""
    return Matcher(config)


def _decode(raw):
    """Decode an HTML text segment, remembering for each character where it came from in `raw`."""
    chars, starts, ends = [], [], []
    i = 0
    while i < len(raw):
        if raw[i] == "&":
            entity = _ENTITY.match(raw, i)
            if entity and html.unescape(entity.group()) != entity.group():
                for char in html.unescape(entity.group()):
                    chars.append(char)
                    starts.append(i)
                    ends.append(entity.end())
                i = entity.end()
                continue
        chars.append(raw[i])
        starts.append(i)
        ends.append(i + 1)
        i += 1
    return "".join(chars), starts, ends


def _wrap(raw, matcher):
    text, starts, ends = _decode(raw)
    out, last_raw = [], 0
    for match in matcher.pattern.finditer(text):
        if match.lastgroup != "hit" or match.end() == match.start():
            continue
        raw_start, raw_end = starts[match.start()], ends[match.end() - 1]
        group = matcher.group_of[match.group().lower()]
        out.append(raw[last_raw:raw_start])
        out.append(f'<span class="{SPAN_CLASS}" data-name="{html.escape(match.group())}" '
                   f'data-group="{html.escape(group)}">{raw[raw_start:raw_end]}</span>')
        last_raw = raw_end
    out.append(raw[last_raw:])
    return "".join(out)


def _is_our_span(tag):
    attr = _CLASS_ATTR.search(tag)
    classes = (attr.group(1) or attr.group(2) or attr.group(3) or "").split() if attr else []
    return SPAN_CLASS in classes


def _walk(source, text_handler):
    """Rebuild `source`, dropping our spans (tags only: their text stays) and passing the text segments between
    tags through `text_handler`. Script and style contents, and comments, are left alone."""
    out, spans, raw_text_until, pos = [], [], None, 0
    for markup in _MARKUP.finditer(source):
        segment = source[pos:markup.start()]
        out.append(segment if raw_text_until else text_handler(segment))
        pos = markup.end()
        tag = markup.group()
        name = _TAG_NAME.match(tag)
        tag_name = name.group(2).lower() if name else ""
        closing = bool(name and name.group(1))
        if raw_text_until:
            if closing and tag_name == raw_text_until:
                raw_text_until = None
            out.append(tag)
        elif tag_name == "span":
            if closing:
                ours = spans.pop() if spans else False
                if not ours:
                    out.append(tag)
            elif tag.endswith("/>"):
                out.append(tag)
            else:
                ours = _is_our_span(tag)
                spans.append(ours)
                if not ours:
                    out.append(tag)
        else:
            if tag_name in ("script", "style") and not closing and not tag.endswith("/>"):
                raw_text_until = tag_name
            out.append(tag)
    tail = source[pos:]
    out.append(tail if raw_text_until else text_handler(tail))
    return "".join(out)


def strip_html(source):
    """`source` without any ColorCoding spans (their text is kept)."""
    return _walk(source, lambda text: text)


def colorize_html(source, matcher):
    """`source` with every configured word wrapped in a ColorCoding span. Any spans already there are replaced,
    so this is idempotent and also refreshes notes coloured by an older version (inline style and all)."""
    if matcher.pattern is None:
        return strip_html(source)
    return _walk(source, lambda text: _wrap(text, matcher) if text else text)
