# anki-colorcoding-groups

## Purpose

Public Anki add-on (GitHub `pointtonull/anki-colorcoding-groups`) that colours words by group. Derived
from Marc Schubert's AnkiWeb "ColorCoding" (no upstream licence — see `NOTICE`). Layers on the global
`~/CLAUDE.md`.

## Layout

- `__init__.py` — add-on entry: config (`_normalize_config`), dialogs, editor buttons/context menu, hooks, the shared
  stylesheet (`sync_stylesheet`, `ensure_stylesheet_import`) and the bulk/deck-wise colouring (`run_bulk`).
- `colorize.py` — the Anki-free logic: HTML-aware word wrapping (`colorize_html`, `strip_html`), `build_css`, the
  `@import` helpers, `deck_in_scope`. Unit-tested; keep it free of `aqt`/`anki` imports. Its matching rules mirror
  `changer_static.js` (which does the same on the editor's live DOM): change one, change the other.
- `changer_static.js` — runs in the editor webview; colours text nodes in every field and injects the stylesheet there.
- `manifest.json` — add-on identity (`package: colorcoding_groups`, distinct from upstream).
- `user_files/` — runtime data. `config.example.json` is the only tracked file besides `.gitkeep`; it is also the
  demo vocabulary of the screenshots and of the sandbox checks.
- `tests/` — unit tests for `colorize.py`: `python3 -m unittest discover -s tests`.
- `docs/screenshots/` — the README/AnkiWeb images.
- `tools/` — the throwaway-Anki harness: `sandbox.py` (launcher), `driver_kit.py` (waits, watchdogs; runs inside Anki),
  `make_screenshots.py` + `screenshot_driver.py`, `check_in_anki.py` + `integration_driver.py`, `check_harness.py`
  (proves the harness fails fast and clean). `docs/`, `tools/`, `tests/` and this file are `export-ignore`d: the
  AnkiWeb package is `__init__.py`, `colorize.py`, `changer_static.js`, `manifest.json`, icon, README, LICENSE, NOTICE,
  `user_files/`.

## Testing

Before any release, run all three; none needs you to touch anything, and each ends in a clear verdict:
1. `python3 -m unittest discover -s tests` (instant).
2. `python3 tools/check_in_anki.py` and again with `--addon-folder 267803161` (AnkiWeb installs use the number, which
   matters for relative imports). ~1 min each; windows of a second Anki flash up.
3. `python3 tools/make_screenshots.py` after any UI change (all-or-nothing: it only replaces `docs/screenshots/` when the
   whole run succeeded). ~20 s.
Harness rules learnt the hard way — keep them:
- The sandbox must start from a seeded `prefs21.db` (`sandbox.seed_preferences`). Without it Anki shows its first-run
  **language picker** before any add-on loads, which no driver can dismiss: the run stalls until someone clicks. The seed
  also switches off update checks and sync.
- Every wait in a driver is bounded and named (`kit.wait`, `kit.js_until`...); unexpected modals are closed and fail the
  run. Don't add `yield <ms>` sleeps to wait for state, only to let a paint settle.
- A numeric sandbox folder (an AnkiWeb id) needs `meta.json` with `update_enabled: false`, or Anki replaces the copy under
  test with the published package.
- Never `screencapture` the desktop to debug: it records whatever else is open. The driver saves PNGs of Anki's own windows
  on a timeout (`<sandbox>/debug/`), which is enough.
- Anki's media server lets a webview cache a stylesheet for 10 s; tests that re-render after a colour change wait it out.
- After touching `tools/`, run `python3 tools/check_harness.py` (~2 min).

## Releasing to AnkiWeb

Listed at https://ankiweb.net/shared/info/267803161 (code `267803161`, Carlos's AnkiWeb account). To ship a new version:
1. Commit and push, then build from the committed tree: `git archive --format=zip -o <scratchpad>/colorcoding-groups.ankiaddon HEAD`
   (`.gitattributes` keeps CLAUDE.md etc. out; the zip must have files at its root and no `__pycache__`/`meta.json`).
2. Open https://ankiweb.net/shared/upload?id=267803161 in Carlos's Firefox via FoxMCP (background tab; `profile_id` 1),
   replace the branch file (`content_load_file` + assign to the file input), keep the version range (23.10 → 26.05, no `-`
   cap) and press *Save*. Confirm with Carlos before saving: it changes a public listing.
3. The form's text inputs take plain `el.value = …` plus an `input` event; version fields get normalised to `x.y.0`.
4. AnkiWeb hosts no images: the listing's description (Markdown + basic HTML, `<img width=…>` works) points at the
   screenshots on GitHub (`https://raw.githubusercontent.com/pointtonull/anki-colorcoding-groups/main/docs/screenshots/<name>.png`),
   so push a screenshot change to `main` before (re)saving the description.
5. To verify what AnkiWeb really serves, run a numeric-folder sandbox *without* `update_enabled: false`: Anki downloads
   the published package and you can hash it against the commit (this confirmed 1641976 byte for byte).

## Ground rules

- **Never commit personal data.** `user_files/config.json` / `settings.json` are Carlos's real vocabulary
  and are git-ignored; keep it that way. Examples in the repo use invented words.
- Keep Marc Schubert's copyright lines in `__init__.py` and `changer_static.js`, and the credit in `README.md`/`NOTICE`.
- Keep the span markup `<span class="ColorCoding" data-name data-group>`: existing Anki notes depend on it. Colours are no
  longer written into the span (`style=`): they come from the shared stylesheet. Notes from older versions still carry a
  `style=` that overrides it until they are coloured again (the bulk tool does that).
- The installed copy Carlos actually uses is `~/Library/Application Support/Anki2/addons21/267803161/`
  (installed from AnkiWeb; the original ColorCoding `2113325087/` next to it is disabled by `conflicts`). This repo
  is the published source; don't overwrite his `user_files/` there. Never point the harness at his profile.
- Check syntax with `python3 -m py_compile __init__.py colorize.py` and `node --check changer_static.js`.
