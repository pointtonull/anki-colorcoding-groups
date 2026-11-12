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
- `palette.py` — Anki-free colour-suggestion maths: cosine distances, the travelling-salesman tour (exact ≤ 12 groups, 2-opt
  / Or-opt beyond), hue steps proportional to the tour's edges (closing edge included, 16° floor), OKLCH → hex. Stdlib only
  (Anki ships no numpy). Unit-tested.
- `embeddings.py` — Anki-free OpenAI-compatible `/embeddings` client (OpenAI, Ollama's `/v1`, anything alike), provider
  presets, `group_texts` (exactly what gets sent). Never sends a key over plain http to a remote host; the key is not in
  any error message. Unit-tested against a real localhost HTTP server. The dialog using both is `SuggestColorsDialog`
  (`__init__.py`), opened from the group manager; the service lives under `colorSuggestions` in `settings.json`.
- `changer_static.js` — runs in the editor webview; colours text nodes in every field and injects the stylesheet there.
- `manifest.json` — add-on identity (`package: colorcoding_groups`, distinct from upstream).
- `user_files/` — runtime data. `config.example.json` is the only tracked file besides `.gitkeep`; it is also the
  demo vocabulary of the screenshots and of the sandbox checks.
- `tests/` — unit tests (`colorize`, `palette`, `embeddings`, the release script): `python3 -m unittest discover -s tests`.
- `justfile` — the [just](https://github.com/casey/just) task runner (`brew install just`; plain `just` lists the recipes). It
  only names and chains the commands below, with no logic of its own, so every command also works without just. Add a
  recipe when you add a script; keep the logic in the script.
- `docs/screenshots/` — the README/AnkiWeb images.
- `tools/` — the throwaway-Anki harness: `sandbox.py` (launcher), `driver_kit.py` (waits, watchdogs; runs inside Anki),
  `make_screenshots.py` + `screenshot_driver.py`, `check_in_anki.py` + `integration_driver.py`, `fake_embeddings.py` (a
  fake OpenAI-compatible server the drivers run in-process: no key, no network), `check_harness.py` (proves the harness
  fails fast and clean), `release_ankiweb.py` (the release script, see *Releasing to AnkiWeb*; unit-tested in
  `tests/test_release_ankiweb.py` against a fake FoxMCP). `DESCRIPTION.md` is the listing's description.
  `docs/`, `tools/`, `tests/`, `DESCRIPTION.md`, `justfile` and this file are `export-ignore`d: the AnkiWeb package is
  `__init__.py`, `colorize.py`, `palette.py`, `embeddings.py`, `changer_static.js`, `manifest.json`, icon, README,
  LICENSE, NOTICE, `user_files/`.

## Testing

Recipes: `just verify` (1 and syntax), `just check` (2), `just shots` (3), `just harness`. Before any release
(`tools/release_ankiweb.py` runs 1 and 2 for you), run all three; none needs you to touch anything, and each ends in a
clear verdict:
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
- `COLORCODING_CHECK_TLS=1 python3 tools/check_in_anki.py` adds one opt-in check that needs the internet: Anki's own
  Python reaches `api.openai.com` over https and gets 401 (no key). Run it when touching `embeddings.py`'s TLS code.
- `python3 tools/make_screenshots.py` re-renders every PNG, so most differ byte-wise without a visible change: commit only
  the images that really changed (`git checkout` the rest).

## Releasing to AnkiWeb

Listed at https://ankiweb.net/shared/info/267803161 (code `267803161`, Carlos's AnkiWeb account). One script does it:
`python3 tools/release_ankiweb.py` (see its `--help`), or `just release` (and `just release-dry-run`, `just release-check`,
`just pull-description`). It needs Firefox open with the FoxMCP extension, and re-runs itself on FoxMCP's venv Python.
1. Edit **`DESCRIPTION.md`** (repo root, export-ignored) when the listing text should change: it is the source of truth for
   the AnkiWeb description (Markdown + basic HTML). `python3 tools/release_ankiweb.py --pull-description` adopts edits made
   on the website. The listing hosts no images: it points at the screenshots on GitHub
   (`https://raw.githubusercontent.com/pointtonull/anki-colorcoding-groups/main/docs/screenshots/<name>.png`), so a new
   screenshot must be on `main` first (the script checks every image answers 200).
2. Commit and push (the script refuses a dirty tree, another branch or an unpushed HEAD).
3. `python3 tools/release_ankiweb.py --dry-run` rehearses everything except *Save*; `--check` only reports whether AnkiWeb
   matches HEAD and `DESCRIPTION.md`.
4. `python3 tools/release_ankiweb.py` releases: unit tests, syntax and both in-Anki checks, then the package
   (`git archive HEAD`, inspected: no personal files, every imported module inside), then the form, a summary of what
   changes against the live listing, and a prompt. **Confirm with Carlos before pressing `y` or passing `--yes`: it changes
   a public listing.** A non-interactive run (how Claude runs it) needs `--yes` and exits 3 without it. Afterwards it
   verifies that AnkiWeb serves the same files as HEAD, that the description reads back equal and that the images render.
   `--skip-checks` is for a description-only fix; `--force` publishes when nothing differs.
5. What it learnt, in case AnkiWeb or FoxMCP change under it: the form's text inputs take plain `el.value = …` plus an
   `input` event; version fields are normalised to `x.y.0` and left as they are (23.10 → 26.05, no `-` cap); the download URL
   `https://ankiweb.net/shared/download/267803161?v=2.1&p=<build>` needs an integer build number (`260500` for 26.05.0) and no
   login; the `content_execute_script` sandbox rejects `Array.prototype.find.call(NodeList, fn)` (use `for` loops); a
   package is attached with `content_load_file` + `DataTransfer`. Every publish uploads the package, even for a
   description-only change.

## Ground rules

- **Never commit personal data.** `user_files/config.json` / `settings.json` are Carlos's real vocabulary
  and are git-ignored; keep it that way (`settings.json` can also hold an embedding-service API key: never print or
  log it). Examples in the repo use invented words.
- Keep Marc Schubert's copyright lines in `__init__.py` and `changer_static.js`, and the credit in `README.md`/`NOTICE`.
- Keep the span markup `<span class="ColorCoding" data-name data-group>`: existing Anki notes depend on it. Colours are no
  longer written into the span (`style=`): they come from the shared stylesheet. Notes from older versions still carry a
  `style=` that overrides it until they are coloured again (the bulk tool does that).
- The installed copy Carlos actually uses is `~/Library/Application Support/Anki2/addons21/267803161/`
  (installed from AnkiWeb; the original ColorCoding `2113325087/` next to it is disabled by `conflicts`). This repo
  is the published source; don't overwrite his `user_files/` there. Never point the harness at his profile.
- Check syntax with `python3 -m py_compile __init__.py colorize.py` and `node --check changer_static.js`.
