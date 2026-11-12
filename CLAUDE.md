# anki-colorcoding-groups

## Purpose

Public Anki add-on (GitHub `pointtonull/anki-colorcoding-groups`) that colours words by group. Derived
from Marc Schubert's AnkiWeb "ColorCoding" (no upstream licence — see `NOTICE`). Layers on the global
`~/CLAUDE.md`.

## Layout

- `__init__.py` — add-on entry: config (`_normalize_config`), dialogs, editor buttons/context menu, hooks.
- `changer_static.js` — runs in the editor webview; colours text nodes in every field.
- `manifest.json` — add-on identity (`package: colorcoding_groups`, distinct from upstream).
- `user_files/` — runtime data. `config.example.json` is the only tracked file besides `.gitkeep`.

## Releasing to AnkiWeb

Listed at https://ankiweb.net/shared/info/267803161 (code `267803161`, Carlos's AnkiWeb account). To ship a new version:
1. Commit and push, then build from the committed tree: `git archive --format=zip -o <scratchpad>/colorcoding-groups.ankiaddon HEAD`
   (`.gitattributes` keeps CLAUDE.md etc. out; the zip must have files at its root and no `__pycache__`/`meta.json`).
2. Open https://ankiweb.net/shared/upload?id=267803161 in Carlos's Firefox via FoxMCP (background tab), replace the
   branch file (`content_load_file` + assign to the file input), keep the version range (23.10 → 26.05, no `-` cap) and press *Save*.
   Confirm with Carlos before saving: it changes a public listing.
3. The form's text inputs take plain `el.value = …` plus an `input` event; version fields get normalised to `x.y.0`.

## Ground rules

- **Never commit personal data.** `user_files/config.json` / `settings.json` are Carlos's real vocabulary
  and are git-ignored; keep it that way. Examples in the repo use invented words.
- Keep Marc Schubert's copyright lines in `__init__.py` and `changer_static.js`, and the credit in `README.md`/`NOTICE`.
- Keep the output markup `<span class="ColorCoding" data-name data-group style>`: existing Anki notes depend on it.
- The installed copy Carlos actually uses is `~/Library/Application Support/Anki2/addons21/2113325087/`
  (the AnkiWeb-installed folder). This repo is the published source; don't overwrite his `user_files/` there.
- Check syntax with `python3 -m py_compile __init__.py` and `node --check changer_static.js` (the module needs
  Anki's `aqt`, so it can't be imported or unit-tested outside Anki).
