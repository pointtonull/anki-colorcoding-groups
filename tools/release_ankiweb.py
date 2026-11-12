#!/usr/bin/env python3
"""Release the add-on to AnkiWeb (listing 267803161) and prove AnkiWeb now serves what was meant.

    python3 tools/release_ankiweb.py                 # release HEAD: checks, publish, verify (asks before saving)
    python3 tools/release_ankiweb.py --dry-run       # everything except pressing Save
    python3 tools/release_ankiweb.py --check         # change nothing: is AnkiWeb in sync with HEAD and DESCRIPTION.md?
    python3 tools/release_ankiweb.py --pull-description   # adopt the live description into DESCRIPTION.md

DESCRIPTION.md (repo root, not part of the package) is the source of truth for the listing's description; edit it,
commit, push, release. The package is `git archive HEAD`, so only committed files ship. A release:

  1. refuses unless the tree is clean, on main, and HEAD is pushed (the description's screenshots load from GitHub);
  2. runs the pre-release checks: unit tests, syntax, and tools/check_in_anki.py for both folder names (--skip-checks
     for a description-only fix);
  3. builds the package and inspects it (no personal files, every module the add-on imports is inside);
  4. checks that every image in DESCRIPTION.md answers 200;
  5. opens the AnkiWeb upload form in a background Firefox tab through FoxMCP (your logged-in Firefox, see
     ~/Documents/Projects/Automate/docs/foxmcp.md), shows what would change against the live listing and asks
     "Publish? [y/N]" (--yes answers it; a non-interactive run needs --yes: confirm with the owner first, it changes
     a public listing);
  6. attaches the package, sets the description, presses Save (the version range is left as it is);
  7. verifies: AnkiWeb serves the same files as HEAD (compared file by file), the description reads back equal, and
     the listing page's images render.
Every wait is bounded and says what never appeared; the tab is closed on every way out. If nothing differs from the
live listing it says so and stops (--force publishes anyway).

Needs Firefox open with the FoxMCP extension and the FoxMCP server (it re-runs itself on FoxMCP's venv Python,
which has the `mcp` client). Exit status: 0 done / in sync, 1 failed or out of sync, 2 refused before touching
anything, 3 needs a confirmation it cannot ask for (pass --yes).
"""
import argparse
import asyncio
import contextlib
import difflib
import hashlib
import io
import json
import os
import re
import shutil
import ssl
import subprocess
import sys
import tempfile
import time
import urllib.error
import urllib.request
import zipfile
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent
ADDON_ID = "267803161"
UPLOAD_URL = f"https://ankiweb.net/shared/upload?id={ADDON_ID}"
INFO_URL = f"https://ankiweb.net/shared/info/{ADDON_ID}"
DOWNLOAD_URL = f"https://ankiweb.net/shared/download/{ADDON_ID}?v=2.1&p={{build}}"
BRANCH = "main"
DESCRIPTION_FILE = "DESCRIPTION.md"
PACKAGE_NAME = "colorcoding-groups.ankiaddon"
ADDON_FOLDERS = ("colorcoding_groups", ADDON_ID)  # check_in_anki.py runs both: AnkiWeb installs under the number
IMAGE_HOST = "raw.githubusercontent.com"
POLL_S = 0.5
FORM_TIMEOUT_S = 40  # the upload form to render
SAVE_TIMEOUT_S = 60  # AnkiWeb to accept the upload
SERVED_TIMEOUT_S = 90  # AnkiWeb to serve the new package
IMAGES_TIMEOUT_S = 30  # the listing page's images to render
FOXMCP_PYTHON = Path(os.environ.get("FOXMCP_PYTHON") or Path.home() / ".local/share/foxmcp/venv/bin/python")
FOXMCP_CLIENT_DIR = Path(os.environ.get("FOXMCP_CLIENT_DIR") or Path.home() / "bin")

# Never in the package (`git archive` honours .gitattributes, this is the second line of defence).
FORBIDDEN_DIRS = ("docs/", "tools/", "tests/", ".git", ".claude", "__pycache__/")
FORBIDDEN_NAMES = {"meta.json", "CLAUDE.md", DESCRIPTION_FILE, "user_files/config.json", "user_files/settings.json"}


class ReleaseError(RuntimeError):
    """Something stopped the release; the message says what, and nothing public was changed unless it says so."""


def say(*parts):
    print(*parts, flush=True)


# --- git: is this commit releasable? ----------------------------------------------------------------------------------

def git(repo, *args):
    done = subprocess.run(["git", "-C", str(repo), *args], capture_output=True, text=True)
    if done.returncode:
        raise ReleaseError(f"git {' '.join(args)} failed: {done.stderr.strip() or done.stdout.strip()}")
    return done.stdout.rstrip("\n")  # not strip(): the first line of `status --porcelain` starts with a space


def preflight(repo):
    """Reasons this tree must not be released (empty list: fine)."""
    problems = []
    branch = git(repo, "rev-parse", "--abbrev-ref", "HEAD")
    if branch != BRANCH:
        problems.append(f"on branch {branch!r}, not {BRANCH!r}")
    dirty = [line for line in git(repo, "status", "--porcelain", "--untracked-files=no").splitlines() if line]
    if dirty:
        problems.append("uncommitted changes to tracked files (commit them, the package is built from HEAD):\n    "
                        + "\n    ".join(dirty))
    try:
        remote = git(repo, "ls-remote", "origin", f"refs/heads/{BRANCH}").split()
    except ReleaseError as error:
        problems.append(f"could not ask origin where {BRANCH} is: {error}")
    else:
        head = git(repo, "rev-parse", "HEAD")
        if not remote or remote[0] != head:
            problems.append(f"HEAD {head[:7]} is not what origin/{BRANCH} has "
                            f"({remote[0][:7] if remote else 'nothing'}): push first")
    return problems


# --- the package ------------------------------------------------------------------------------------------------------

def build_package(repo, dest):
    """`git archive HEAD` as a zip at `dest` (so only committed, not export-ignored files ship)."""
    git(repo, "archive", "--format=zip", "-o", str(dest), "HEAD")
    return Path(dest)


def read_package(data):
    """{file name: bytes} of a zip, directories left out."""
    with zipfile.ZipFile(io.BytesIO(data)) as archive:
        return {info.filename: archive.read(info) for info in archive.infolist() if not info.is_dir()}


def hashes(files):
    return {name: hashlib.sha256(content).hexdigest() for name, content in files.items()}


def diff_files(old, new):
    """(added, removed, changed) file names going from `old` to `new` ({name: hash} dicts)."""
    return (sorted(set(new) - set(old)), sorted(set(old) - set(new)),
            sorted(name for name in set(old) & set(new) if old[name] != new[name]))


def relative_imports(source):
    """Module names an add-on source file imports from its own package (`from . import a, b`, `from .a import x`)."""
    names = set()
    for match in re.finditer(r"^from \. import ([\w, ]+)", source, re.MULTILINE):
        names.update(part.strip() for part in match.group(1).split(",") if part.strip())
    names.update(re.findall(r"^from \.(\w+) import", source, re.MULTILINE))
    return names


def package_problems(files):
    """What is wrong with a package ({name: bytes}); empty list: fine."""
    problems = []
    for name in sorted(files):
        if name in FORBIDDEN_NAMES or name.endswith((".pyc", ".DS_Store")) or name.startswith(FORBIDDEN_DIRS) \
                or "__pycache__" in name:
            problems.append(f"{name} must not be in the package")
    if "__init__.py" not in files or "manifest.json" not in files:
        problems.append("__init__.py and manifest.json must be at the root of the zip "
                        f"(found: {', '.join(sorted(files)[:6])}...)")
        return problems
    try:
        manifest = json.loads(files["manifest.json"])
        if not manifest.get("package") or not manifest.get("name"):
            problems.append("manifest.json needs a name and a package")
    except ValueError as error:
        problems.append(f"manifest.json is not valid JSON: {error}")
    source = files["__init__.py"].decode("utf-8", "replace")
    for module in sorted(relative_imports(source)):
        if f"{module}.py" not in files and f"{module}/__init__.py" not in files:
            problems.append(f"__init__.py imports .{module} but the package has no {module}.py "
                            "(a new module that is export-ignored or untracked?)")
    return problems


# --- the description --------------------------------------------------------------------------------------------------

def read_description(repo):
    """DESCRIPTION.md as committed at HEAD."""
    try:
        return git(repo, "show", f"HEAD:{DESCRIPTION_FILE}") + "\n"
    except ReleaseError:
        raise ReleaseError(f"{DESCRIPTION_FILE} is not committed at HEAD") from None


def normalize(text):
    """What counts as 'the same description': line endings and surrounding blank space do not."""
    return text.replace("\r\n", "\n").strip()


def image_urls(text):
    """Every image a Markdown/HTML description refers to, in order, without repeats."""
    urls = re.findall(r"""<img\b[^>]*?\bsrc=["']([^"']+)["']""", text) + re.findall(r"!\[[^\]]*\]\(([^)\s]+)", text)
    return list(dict.fromkeys(urls))


def head_status(url):
    request = urllib.request.Request(url, method="HEAD", headers={"User-Agent": "release_ankiweb"})
    try:
        with urllib.request.urlopen(request, timeout=20, context=_ssl_context()) as response:
            return response.status
    except urllib.error.HTTPError as error:
        return error.code
    except (urllib.error.URLError, OSError) as error:
        return str(getattr(error, "reason", error))


def broken_images(urls, status=head_status):
    """[(url, what it answered)] for every image that is not a plain 200."""
    answers = [(url, status(url)) for url in urls]
    return [(url, answer) for url, answer in answers if answer != 200]


def unified(old, new, old_name, new_name, limit=60):
    lines = list(difflib.unified_diff(normalize(old).splitlines(), normalize(new).splitlines(),
                                      old_name, new_name, lineterm="", n=1))
    if len(lines) > limit:
        lines = lines[:limit] + [f"... ({len(lines) - limit} more diff lines)"]
    return "\n".join(lines)


# --- what AnkiWeb serves ----------------------------------------------------------------------------------------------

def _ssl_context():
    try:
        import certifi
        return ssl.create_default_context(cafile=certifi.where())
    except Exception:
        return ssl.create_default_context()


def build_number(version):
    """Anki's build number for a version like '26.05.0' (or '-26.05.0'): 260500. AnkiWeb's download URL wants it."""
    parts = [int(part) for part in version.lstrip("-").split(".")]
    parts += [0] * (3 - len(parts))
    return parts[0] * 10000 + parts[1] * 100 + parts[2]


def fetch_served(build, timeout=30):
    """The files AnkiWeb serves to an Anki of this build, {name: bytes}, or None if it serves nothing readable."""
    request = urllib.request.Request(DOWNLOAD_URL.format(build=build), headers={"User-Agent": "release_ankiweb"})
    try:
        with urllib.request.urlopen(request, timeout=timeout, context=_ssl_context()) as response:
            return read_package(response.read())
    except (urllib.error.URLError, OSError, zipfile.BadZipFile):
        return None


# --- FoxMCP: driving the AnkiWeb form ---------------------------------------------------------------------------------

# Each script starts with a tag comment so tests can recognise it. They avoid Array.prototype.x.call(NodeList, ...):
# FoxMCP's content-script sandbox rejects it.
# The version inputs have no `type` attribute, so `input[type=text]` misses them (found on the live page).
JS_READ_FORM = """/*ankiweb:read-form*/(function(){
  var texts = [], inputs = document.querySelectorAll('input[type=text], input:not([type])');
  for (var i = 0; i < inputs.length; i++) { texts.push(inputs[i].value); }
  var area = document.querySelector('textarea'), file = document.querySelector('input[type=file]');
  return JSON.stringify({url: location.href, ready: !!(area && file) && document.readyState === 'complete',
                         texts: texts, description: area ? area.value : null,
                         page: document.body ? document.body.innerText.slice(0, 300) : ''});
})()"""

JS_FILL = """/*ankiweb:fill*/(async function(packageId, description){
  var input = document.querySelector('input[type=file]'), area = document.querySelector('textarea');
  var pkg = globalThis.foxmcpFiles.get(packageId);
  if (!pkg) { return JSON.stringify({error: 'the loaded package is gone (FoxMCP forgets files after 5 minutes)'}); }
  var transfer = new window.wrappedJSObject.DataTransfer();
  transfer.items.add(cloneInto(pkg, window));
  input.wrappedJSObject.files = transfer.files;
  input.dispatchEvent(new Event('change', {bubbles: true}));
  area.value = description;
  area.dispatchEvent(new Event('input', {bubbles: true}));
  return JSON.stringify({file: input.files.length ? [input.files[0].name, input.files[0].size] : null,
                         descriptionUnits: area.value.length});
})(%s, %s)"""

JS_SAVE = """/*ankiweb:save*/(function(){
  var buttons = document.querySelectorAll('button');
  for (var i = 0; i < buttons.length; i++) {
    if (buttons[i].textContent.trim() === 'Save') { buttons[i].click(); return JSON.stringify({clicked: true}); }
  }
  return JSON.stringify({clicked: false});
})()"""

JS_WHERE = """/*ankiweb:where*/JSON.stringify({url: location.href, page: document.body ? document.body.innerText.slice(0, 600) : ''})"""

JS_IMAGES = """/*ankiweb:images*/(function(){
  var out = [], images = document.querySelectorAll('img');
  for (var i = 0; i < images.length; i++) {
    if (images[i].src.indexOf('%s') >= 0) { out.push([images[i].src, images[i].complete && images[i].naturalWidth > 0]); }
  }
  return JSON.stringify({images: out});
})()""" % IMAGE_HOST

RE_CREATED_TAB = re.compile(r"^Created tab: ID (\d+)", re.MULTILINE)


def script_value(text):
    """The JSON a content_execute_script call returned (FoxMCP prefixes one 'Script result from tab...' line)."""
    body = text.split("\n", 1)[1] if text.startswith("Script result") and "\n" in text else text
    try:
        return json.loads(body)
    except ValueError:
        raise ReleaseError(f"a page script answered something unexpected: {text[:200]!r}") from None


@contextlib.asynccontextmanager
async def background_tab(call, url):
    """A new background tab on `url`, closed on every way out."""
    created = await call("tabs_create", url=url, active=False)
    match = RE_CREATED_TAB.search(created)
    if not match:
        raise ReleaseError(f"could not open a tab on {url}: {created[:200]}")
    tab = int(match.group(1))
    try:
        yield tab
    finally:
        with contextlib.suppress(Exception):
            await call("tabs_close", tab_id=tab)


async def run_script(call, tab, code):
    return script_value(await call("content_execute_script", tab_id=tab, code=code))


async def wait_until(what, probe, accept, timeout):
    """Poll `probe()` until `accept(value)`; give up after `timeout` seconds naming what never happened."""
    deadline = time.monotonic() + timeout
    while True:
        value = await probe()
        if accept(value):
            return value
        if time.monotonic() > deadline:
            raise ReleaseError(f"gave up after {timeout:.0f}s waiting for {what} (last answer: {json.dumps(value)[:300]})")
        await asyncio.sleep(POLL_S)


async def read_form(call, tab):
    """The upload form's state, once it has rendered: {title, url, description, branch: (min, max)}."""
    def populated(value):  # rendered, and filled with the listing's current title and versions
        texts = value["texts"]
        return value["ready"] and len(texts) >= 5 and all(text.strip() for text in texts[:1] + texts[3:])

    form = await wait_until("the AnkiWeb upload form to appear (is Firefox logged in to AnkiWeb?)",
                            lambda: run_script(call, tab, JS_READ_FORM), populated, FORM_TIMEOUT_S)
    texts = [text.strip() for text in form["texts"]]
    if len(texts) < 5 or (len(texts) - 3) % 2:
        raise ReleaseError(f"the upload form has {len(texts)} text fields; expected title, tags, support and "
                           "min/max version pairs: AnkiWeb changed its form?")
    branches = [(texts[i], texts[i + 1]) for i in range(3, len(texts), 2)]
    if len(branches) != 1:
        raise ReleaseError(f"the listing has {len(branches)} version branches; this script handles one")
    return {"title": texts[0], "url": form["url"], "description": form["description"], "branch": branches[0]}


async def fill_form(call, tab, package_path, description):
    loaded = json.loads(await call("content_load_file", tab_id=tab, path=str(package_path),
                                   mime_type="application/octet-stream"))
    filled = await run_script(call, tab, JS_FILL % (json.dumps(loaded["file_id"]), json.dumps(description)))
    if filled.get("error"):
        raise ReleaseError(filled["error"])
    expected = Path(package_path).stat().st_size
    if not filled["file"] or filled["file"][1] != expected:
        raise ReleaseError(f"the package did not attach to the form (form says {filled['file']}, expected {expected} bytes)")
    if filled["descriptionUnits"] != len(description.encode("utf-16-le")) // 2:
        raise ReleaseError("the description did not land in the form intact")


async def press_save(call, tab):
    if not (await run_script(call, tab, JS_SAVE))["clicked"]:
        raise ReleaseError("no Save button on the form: AnkiWeb changed its form?")

    async def where():
        return await run_script(call, tab, JS_WHERE)

    try:
        await wait_until("AnkiWeb to accept the upload (the page should move to the listing)", where,
                         lambda value: "/shared/info/" in value["url"], SAVE_TIMEOUT_S)
    except ReleaseError as error:
        raise ReleaseError(f"Save did not go through. {error}") from None


async def verify(call, files, description, build, fetch=fetch_served):
    """Prove the listing now matches. Returns a list of problems (empty: all good)."""
    problems = []
    want = hashes(files)
    deadline = time.monotonic() + SERVED_TIMEOUT_S
    while True:
        served = await asyncio.to_thread(fetch, build)
        got = hashes(served) if served else None
        if got == want or time.monotonic() > deadline:
            break
        await asyncio.sleep(POLL_S * 4)
    if got is None:
        problems.append("AnkiWeb serves no readable package")
    elif got != want:
        added, removed, changed = diff_files(got, want)
        problems.append(f"AnkiWeb's package differs from HEAD (missing {added}, extra {removed}, changed {changed})")
    async with background_tab(call, UPLOAD_URL) as tab:
        form = await read_form(call, tab)
    if normalize(form["description"]) != normalize(description):
        problems.append("the description read back from AnkiWeb differs from DESCRIPTION.md:\n"
                        + unified(description, form["description"], DESCRIPTION_FILE, "AnkiWeb"))
    async with background_tab(call, f"{INFO_URL}?cb={int(time.time())}") as tab:
        wanted = len(image_urls(description))

        async def images():
            return await run_script(call, tab, JS_IMAGES)

        try:
            shown = await wait_until("the listing's images to render", images,
                                     lambda value: len(value["images"]) >= wanted and all(ok for _, ok in value["images"]),
                                     IMAGES_TIMEOUT_S)
        except ReleaseError:
            shown = await images()
        bad = [src for src, ok in shown["images"] if not ok]
        if bad or len(shown["images"]) < wanted:
            problems.append(f"{len(shown['images'])} of {wanted} images on the listing page, not rendering: {bad}")
    return problems


def describe_changes(files, served, description, live_description):
    """Human-readable differences between what would be published and what is live."""
    lines = []
    if served is None:
        lines.append("package: could not read what AnkiWeb serves, so it will be replaced")
    else:
        added, removed, changed = diff_files(hashes(served), hashes(files))
        if added or removed or changed:
            lines.append(f"package: +{len(added)} new, -{len(removed)} removed, ~{len(changed)} changed file(s)")
            lines += [f"    + {name}" for name in added] + [f"    - {name}" for name in removed]
            lines += [f"    ~ {name}" for name in changed]
        else:
            lines.append("package: identical to what AnkiWeb serves")
    if normalize(description) == normalize(live_description):
        lines.append("description: identical to the live one")
    else:
        lines.append("description changes:")
        lines.append(unified(live_description, description, "AnkiWeb (live)", DESCRIPTION_FILE))
    return lines


async def publish(call, package_path, files, description, *, dry_run=False, force=False, confirm=lambda: True,
                  fetch=fetch_served):
    """Fill the form and (unless dry_run) save it. Returns 'unchanged', 'dry-run' or 'published'."""
    async with background_tab(call, UPLOAD_URL) as tab:
        form = await read_form(call, tab)
        build = build_number(form["branch"][1])
        say(f"Listing: {form['title']!r}, versions {form['branch'][0]} to {form['branch'][1]} (left as they are)")
        served = await asyncio.to_thread(fetch, build)
        for line in describe_changes(files, served, description, form["description"]):
            say("  " + line)
        same = served is not None and hashes(served) == hashes(files) \
            and normalize(form["description"]) == normalize(description)
        if same and not force:
            say("Nothing to release: AnkiWeb already serves this commit's package and description (--force to publish anyway).")
            return "unchanged"
        await fill_form(call, tab, package_path, description)
        say("Form filled: package attached, description set.")
        if dry_run:
            say("Dry run: not pressing Save.")
            return "dry-run"
        if not confirm():
            raise ReleaseError("not confirmed; nothing was published")
        await press_save(call, tab)
        say("Saved.")
    problems = await verify(call, files, description, build, fetch)
    if problems:
        raise ReleaseError("published, but verification failed:\n  - " + "\n  - ".join(problems))
    say("Verified: AnkiWeb serves exactly this commit, the description reads back equal, the images render.")
    return "published"


async def check_sync(call, files, description, fetch=fetch_served):
    """Compare AnkiWeb with HEAD and DESCRIPTION.md without changing anything. Returns the problems."""
    async with background_tab(call, UPLOAD_URL) as tab:
        form = await read_form(call, tab)
    served = await asyncio.to_thread(fetch, build_number(form["branch"][1]))
    lines = describe_changes(files, served, description, form["description"])
    for line in lines:
        say("  " + line)
    in_sync = served is not None and hashes(served) == hashes(files) \
        and normalize(form["description"]) == normalize(description)
    return [] if in_sync else ["AnkiWeb is not in sync with HEAD and DESCRIPTION.md"]


async def pull_description(call):
    async with background_tab(call, UPLOAD_URL) as tab:
        return (await read_form(call, tab))["description"]


# --- running it -------------------------------------------------------------------------------------------------------

def run_checks(repo, full=True):
    """The pre-release checks; raises ReleaseError naming the one that failed."""
    steps = [("unit tests", [sys.executable, "-m", "unittest", "discover", "-s", "tests"], 120)]
    steps.append(("syntax", [sys.executable, "-m", "py_compile", *[p.name for p in sorted(Path(repo).glob("*.py"))]], 30))
    if shutil.which("node"):
        steps.append(("javascript syntax", ["node", "--check", "changer_static.js"], 30))
    if full:
        for folder in ADDON_FOLDERS:
            steps.append((f"in Anki ({folder})", [sys.executable, "tools/check_in_anki.py", "--addon-folder", folder], 400))
    for name, command, timeout in steps:
        started = time.monotonic()
        try:
            done = subprocess.run(command, cwd=repo, capture_output=True, text=True, timeout=timeout)
        except subprocess.TimeoutExpired:
            raise ReleaseError(f"check '{name}' did not finish within {timeout}s") from None
        output = done.stdout + done.stderr
        if done.returncode:
            tail = "\n".join(line for line in output.splitlines() if "autoenv_init" not in line)[-1500:]
            raise ReleaseError(f"check '{name}' failed (exit {done.returncode}):\n{tail}")
        say(f"  ok   {name} ({time.monotonic() - started:.0f}s)")


def ensure_foxmcp_python():
    """The FoxMCP client needs the `mcp` package, which lives in FoxMCP's venv: re-run there if this Python lacks it."""
    try:
        import mcp  # noqa: F401
        return
    except ImportError:
        pass
    if os.environ.get("RELEASE_ANKIWEB_REEXEC") or not FOXMCP_PYTHON.exists():
        raise ReleaseError(f"the `mcp` package is missing and FoxMCP's Python ({FOXMCP_PYTHON}) was not found; "
                           "set FOXMCP_PYTHON (see ~/Documents/Projects/Automate/docs/foxmcp.md)")
    os.environ["RELEASE_ANKIWEB_REEXEC"] = "1"
    os.execv(str(FOXMCP_PYTHON), [str(FOXMCP_PYTHON), *sys.argv])


def load_foxmcp_client():
    ensure_foxmcp_python()
    sys.path.insert(0, str(FOXMCP_CLIENT_DIR))
    try:
        import foxmcp_client
    except ImportError:
        raise ReleaseError(f"foxmcp_client.py not found in {FOXMCP_CLIENT_DIR} (set FOXMCP_CLIENT_DIR)") from None
    return foxmcp_client


def with_browser(fx, work, timeout):
    """Run `work(call)` against FoxMCP; `call(tool, **args)` returns the tool's text."""
    async def main():
        async with fx.open_session() as session:
            async def call(tool, **args):
                return await fx.call(session, tool, args)
            return await work(call)
    try:
        return fx.run(main(), timeout)
    except fx.FoxMCPError as error:
        raise ReleaseError(f"FoxMCP: {error}") from None
    except (TimeoutError, asyncio.TimeoutError):
        raise ReleaseError(f"FoxMCP did not finish within {timeout}s (is Firefox open with the extension?)") from None


def confirm_prompt():
    answer = input("Publish this to AnkiWeb (it changes a public listing)? [y/N] ")
    return answer.strip().lower() in ("y", "yes")


def parse_args(argv=None):
    parser = argparse.ArgumentParser(description=(__doc__ or "").split("\n\n")[0],
                                     formatter_class=argparse.RawDescriptionHelpFormatter,
                                     epilog="\n".join((__doc__ or "").split("\n\n")[1:]))
    mode = parser.add_mutually_exclusive_group()
    mode.add_argument("--dry-run", action="store_true", help="do everything except press Save")
    mode.add_argument("--check", action="store_true", help="change nothing: is AnkiWeb in sync with HEAD + DESCRIPTION.md?")
    mode.add_argument("--pull-description", action="store_true", help="write the live description into DESCRIPTION.md")
    parser.add_argument("--yes", action="store_true", help="publish without asking (the owner must have said so)")
    parser.add_argument("--force", action="store_true", help="publish even if AnkiWeb already matches")
    parser.add_argument("--skip-checks", action="store_true", help="skip the in-Anki checks (unit tests and syntax still run)")
    parser.add_argument("--timeout", type=int, default=420, help="seconds for the whole browser part (default: %(default)s)")
    return parser.parse_args(argv)


def main(argv=None):
    args = parse_args(argv)
    try:
        return _main(args)
    except ReleaseError as error:
        say(f"\nrelease_ankiweb: {error}")
        return 1


def _main(args):
    fx = load_foxmcp_client()  # first: it may re-run this script under FoxMCP's Python, before anything slow
    if args.pull_description:
        live = normalize(with_browser(fx, pull_description, args.timeout)) + "\n"
        path = REPO / DESCRIPTION_FILE
        old = path.read_text(encoding="utf-8") if path.exists() else ""
        if normalize(old) == normalize(live):
            say(f"{DESCRIPTION_FILE} already equals the live description.")
        else:
            path.write_text(live, encoding="utf-8")
            say(unified(old, live, DESCRIPTION_FILE + " (before)", DESCRIPTION_FILE + " (now)"))
            say(f"Wrote {DESCRIPTION_FILE}: review and commit it.")
        return 0

    publishing = not args.check
    if publishing and not (args.dry_run or args.yes or sys.stdin.isatty()):
        say("This publishes to a public listing and cannot ask here: run it in a terminal, or pass --yes once the owner agreed.")
        return 3
    if publishing:
        say("Preflight")
        problems = preflight(REPO)
        if problems:
            say("Refusing to release:\n  - " + "\n  - ".join(problems))
            return 2
        say(f"  ok   clean tree on {BRANCH}, HEAD {git(REPO, 'rev-parse', '--short', 'HEAD')} is pushed")
        quick = args.skip_checks or args.dry_run  # a dry run publishes nothing, so it skips the slow in-Anki checks
        say("Checks" + (" (the in-Anki checks are skipped)" if quick else ""))
        run_checks(REPO, full=not quick)

    with tempfile.TemporaryDirectory(prefix="release-ankiweb-") as scratch:
        package = build_package(REPO, Path(scratch) / PACKAGE_NAME)
        files = read_package(package.read_bytes())
        problems = package_problems(files)
        if problems:
            say("The package is wrong:\n  - " + "\n  - ".join(problems))
            return 2
        description = read_description(REPO)
        say(f"Package from HEAD: {len(files)} files, {package.stat().st_size} bytes. "
            f"Description: {len(description)} characters, {len(image_urls(description))} images.")
        bad = broken_images(image_urls(description))
        if bad:
            say("These description images do not answer 200 (pushed to GitHub?):\n  - "
                + "\n  - ".join(f"{url}: {answer}" for url, answer in bad))
            return 2

        if args.check:
            problems = with_browser(fx, lambda call: check_sync(call, files, description), args.timeout)
            say("In sync." if not problems else "\n".join(problems))
            return 1 if problems else 0

        confirm = (lambda: True) if args.yes else confirm_prompt
        outcome = with_browser(fx, lambda call: publish(call, package, files, description, dry_run=args.dry_run,
                                                        force=args.force, confirm=confirm), args.timeout)
        say(f"Result: {outcome}")
        return 0


if __name__ == "__main__":
    sys.exit(main())
