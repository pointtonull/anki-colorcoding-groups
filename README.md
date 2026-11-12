# Color Coding Groups for Anki

An Anki add-on that **colours words automatically, by group**. Put "Paris" and "Kyoto" in a *Places* group
(blue), "Ada Lovelace" in *People* (pink, bold), and every occurrence in your notes is coloured as you edit.
Change a group's colour once and all its words follow.

It builds on [**ColorCoding** by Marc Schubert](https://ankiweb.net/shared/info/2113325087)
([source](https://github.com/schubertmc/ColorCoding)) — see [Credits](#credits-and-licence).

## What it adds

- **Groups with a colour and a bold flag.** Words belong to a group; the group decides how they look.
- **Group manager** (Tools → 🎨 Color Coding 🌈 → *Manage Groups…*): create, recolour, rename, delete groups.
- **Assign from the editor.** Select text and either right-click → *🎨 Set color group*, or press
  `Meta+Shift+G` (the physical **Control** key on macOS) for a keyboard-first group picker.
- **Colours every field** of the note, not just the one with the cursor, and re-colours when a note is loaded.
  The **C** button (`Meta+Shift+H`) forces a re-colour.
- **Config migration:** the flat `[{"word", "group", "color"}]` list of the original and groups stored as bare
  colour strings are upgraded automatically.

Coloured text is written as `<span class="ColorCoding" data-name="…" data-group="…" style="color:…">`, the same
markup as the original, so notes coloured by either version keep working.

## Install

Anki 23.10 or newer (the add-on imports PyQt6 directly).

1. Install from AnkiWeb (*Tools → Add-ons → Get Add-ons…*, code in the add-on's AnkiWeb page), or from a package
   built from this repo: `git archive --format=zip -o colorcoding-groups.ankiaddon HEAD`, then double-click the file.
   Or copy this folder into Anki's add-ons folder (*Tools → Add-ons → View Files*) as `colorcoding_groups/`.
2. Restart Anki. A **🎨 Color Coding 🌈** entry appears under *Tools*.

The original ColorCoding add-on adds the same menu entry and hooks, so only one can run: installing this one
**disables the original automatically** (its `manifest.json` lists it under `conflicts`). Your words and groups live in
each add-on's own `user_files/config.json`, so to carry them over copy the original's file into this add-on's
`user_files/` (*Tools → Add-ons → select → View Files*). The old flat format is upgraded on first load.

## Your data

Words and groups live in `user_files/config.json` inside the add-on folder (created on first save, ignored by git).
`user_files/config.example.json` shows the format:

```json
{
  "groups":  {"Places": {"color": "#798aff", "bold": false}},
  "entries": [{"word": "Paris", "group": "Places"}]
}
```

*Tools → Color Coding → Modify Manually* edits this JSON in a text box. Entries without a group, or whose group no
longer exists, are not coloured.

## Credits and licence

This add-on started from **ColorCoding** by **Marc Schubert** (© 2022–2024), published on
[AnkiWeb](https://ankiweb.net/shared/info/2113325087) and [GitHub](https://github.com/schubertmc/ColorCoding),
and still contains parts of his code (notably in `changer_static.js` and the settings window). The original
project does **not publish a licence**, so those parts remain his; his copyright lines are kept in the source
files. Everything added here (groups, group manager and picker, context menu, all-fields colouring, config migration)
is under the [MIT licence](LICENSE).

If you are the original author and would like something changed or removed, please open an issue.
