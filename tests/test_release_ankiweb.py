import asyncio
import contextlib
import io
import json
import os
import shutil
import subprocess
import sys
import tempfile
import unittest
import zipfile
from pathlib import Path
from unittest import mock

sys.path.insert(0, os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "tools"))

import release_ankiweb as release  # noqa: E402

GIT_ENV = {"GIT_AUTHOR_NAME": "t", "GIT_AUTHOR_EMAIL": "t@example.com", "GIT_COMMITTER_NAME": "t",
           "GIT_COMMITTER_EMAIL": "t@example.com", "GIT_CONFIG_GLOBAL": os.devnull, "GIT_CONFIG_SYSTEM": os.devnull}


def zip_of(files):
    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, "w") as archive:
        for name, content in files.items():
            archive.writestr(name, content)
    return buffer.getvalue()


GOOD_FILES = {"__init__.py": b"from . import colorize, palette\nfrom .embeddings import x\n", "colorize.py": b"",
              "palette.py": b"", "embeddings.py": b"", "manifest.json": b'{"name": "N", "package": "p"}',
              "user_files/.gitkeep": b""}


class TempRepoCase(unittest.TestCase):
    """A real git repository with a real (bare, local) origin."""

    def setUp(self):
        patcher = mock.patch.dict(os.environ, GIT_ENV)
        patcher.start()
        self.addCleanup(patcher.stop)
        self.tmp = Path(tempfile.mkdtemp(prefix="release-test-"))
        self.addCleanup(shutil.rmtree, self.tmp, ignore_errors=True)
        self.origin, self.repo = self.tmp / "origin.git", self.tmp / "repo"
        self.git(self.tmp, "init", "-q", "--bare", "-b", "main", str(self.origin))
        self.repo.mkdir()
        self.git(self.repo, "init", "-q", "-b", "main")
        self.git(self.repo, "remote", "add", "origin", str(self.origin))

    def git(self, cwd, *args):
        done = subprocess.run(["git", "-C", str(cwd), *args], capture_output=True, text=True, check=True)
        return done.stdout.strip()

    def write(self, name, content):
        path = self.repo / name
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(content) if isinstance(content, str) else path.write_bytes(content)

    def commit(self, message="c", push=True):
        self.git(self.repo, "add", "-A")
        self.git(self.repo, "commit", "-q", "-m", message)
        if push:
            self.git(self.repo, "push", "-q", "origin", "main")


class PreflightTests(TempRepoCase):
    def setUp(self):
        super().setUp()
        self.write("a.txt", "a")
        self.commit()

    def test_clean_pushed_main_is_fine(self):
        self.assertEqual(release.preflight(self.repo), [])

    def test_uncommitted_changes_to_tracked_files_are_refused_but_untracked_files_are_not(self):
        self.write("new-untracked.txt", "x")
        self.assertEqual(release.preflight(self.repo), [])
        self.write("a.txt", "changed")
        (problem,) = release.preflight(self.repo)
        self.assertIn("uncommitted changes", problem)
        self.assertIn("a.txt", problem)

    def test_another_branch_is_refused(self):
        self.git(self.repo, "checkout", "-q", "-b", "feature")
        self.assertTrue(any("branch 'feature'" in p for p in release.preflight(self.repo)))

    def test_an_unpushed_commit_is_refused(self):
        self.write("a.txt", "b")
        self.commit(push=False)
        (problem,) = release.preflight(self.repo)
        self.assertIn("push first", problem)

    def test_no_origin_is_reported_not_crashed_on(self):
        self.git(self.repo, "remote", "remove", "origin")
        (problem,) = release.preflight(self.repo)
        self.assertIn("could not ask origin", problem)


class PackageTests(TempRepoCase):
    def test_the_package_is_the_committed_tree_without_export_ignored_files(self):
        self.write(".gitattributes", "tools export-ignore\nCLAUDE.md export-ignore\nDESCRIPTION.md export-ignore\n")
        for name in ("__init__.py", "manifest.json", "tools/x.py", "CLAUDE.md", "DESCRIPTION.md", "user_files/.gitkeep"):
            self.write(name, "{}" if name.endswith(".json") else "")
        self.commit()
        self.write("uncommitted.py", "x")
        files = release.read_package(release.build_package(self.repo, self.tmp / "p.zip").read_bytes())
        self.assertEqual(sorted(files), [".gitattributes", "__init__.py", "manifest.json", "user_files/.gitkeep"])

    def test_description_is_read_from_head_not_from_the_working_tree(self):
        self.write("DESCRIPTION.md", "committed text")
        self.commit()
        self.write("DESCRIPTION.md", "edited, not committed")
        self.assertEqual(release.read_description(self.repo), "committed text\n")

    def test_a_missing_description_is_an_error(self):
        self.write("a", "a")
        self.commit()
        with self.assertRaisesRegex(release.ReleaseError, "not committed at HEAD"):
            release.read_description(self.repo)


class PackageCheckTests(unittest.TestCase):
    def problems(self, **changes):
        files = dict(GOOD_FILES)
        for name, content in changes.items():
            if content is None:
                files.pop(name.replace("__", "/"), None)
            else:
                files[name] = content
        return release.package_problems(files)

    def test_a_good_package(self):
        self.assertEqual(release.package_problems(GOOD_FILES), [])

    def test_personal_and_dev_files_are_flagged(self):
        files = dict(GOOD_FILES)
        for name in ("user_files/config.json", "user_files/settings.json", "meta.json", "CLAUDE.md", "DESCRIPTION.md",
                     "tools/x.py", "tests/t.py", "docs/a.png", "__pycache__/a.pyc", ".gitignore", ".DS_Store",
                     "sub/__pycache__/b.pyc"):
            files[name] = b""
        flagged = {line.split(" must not")[0] for line in release.package_problems(files)}
        self.assertEqual(flagged, {"user_files/config.json", "user_files/settings.json", "meta.json", "CLAUDE.md",
                                   "DESCRIPTION.md", "tools/x.py", "tests/t.py", "docs/a.png", "__pycache__/a.pyc",
                                   ".gitignore", ".DS_Store", "sub/__pycache__/b.pyc"})

    def test_the_files_must_be_at_the_root_of_the_zip(self):
        problems = release.package_problems({f"colorcoding/{name}": content for name, content in GOOD_FILES.items()})
        self.assertTrue(any("at the root" in p for p in problems), problems)

    def test_an_imported_module_that_is_not_shipped_is_caught(self):
        files = dict(GOOD_FILES)
        del files["palette.py"]
        (problem,) = release.package_problems(files)
        self.assertIn(".palette", problem)

    def test_a_package_directory_counts_as_a_module(self):
        files = dict(GOOD_FILES)
        del files["palette.py"]
        files["palette/__init__.py"] = b""
        self.assertEqual(release.package_problems(files), [])

    def test_a_broken_manifest(self):
        self.assertTrue(any("not valid JSON" in p for p in self.problems(**{"manifest.json": b"{"})))
        self.assertTrue(any("name and a package" in p for p in self.problems(**{"manifest.json": b"{}"})))

    def test_relative_import_forms(self):
        source = "from . import a, b\nfrom .c import d\nimport os\nfrom os import path\n    from . import indented\n"
        self.assertEqual(release.relative_imports(source), {"a", "b", "c"})

    def test_diff_files(self):
        old, new = {"a": "1", "b": "2", "c": "3"}, {"b": "2", "c": "9", "d": "4"}
        self.assertEqual(release.diff_files(old, new), (["d"], ["a"], ["c"]))
        self.assertEqual(release.diff_files(old, old), ([], [], []))


class DescriptionTests(unittest.TestCase):
    def test_images_from_html_and_markdown_without_repeats(self):
        text = ('Intro <img src="https://h/a.png" width="1" alt="x"> and <img alt="y" src=\'https://h/b.png\'>\n'
                "![c](https://h/c.png) ![again](https://h/a.png)\n[link](https://h/not-an-image)")
        self.assertEqual(release.image_urls(text), ["https://h/a.png", "https://h/b.png", "https://h/c.png"])

    def test_broken_images(self):
        answers = {"https://h/ok.png": 200, "https://h/gone.png": 404, "https://h/down.png": "timed out"}
        self.assertEqual(release.broken_images(list(answers), answers.get),
                         [("https://h/gone.png", 404), ("https://h/down.png", "timed out")])

    def test_normalize_ignores_line_endings_and_surrounding_space(self):
        self.assertEqual(release.normalize("a\r\nb\r\n\r\n"), release.normalize("\na\nb"))
        self.assertNotEqual(release.normalize("a b"), release.normalize("a  b"))

    def test_unified_diff_is_short(self):
        diff = release.unified("\n".join(f"old {i}" for i in range(100)), "\n".join(f"new {i}" for i in range(100)),
                               "old", "new", limit=10)
        self.assertEqual(len(diff.splitlines()), 11)
        self.assertIn("more diff lines", diff)
        short = release.unified("a\nb", "a\nc", "old", "new", limit=10)
        self.assertNotIn("more diff lines", short)
        self.assertIn("-b", short)
        self.assertIn("+c", short)

    def test_build_numbers(self):
        self.assertEqual(release.build_number("26.05.0"), 260500)
        self.assertEqual(release.build_number("23.10.0"), 231000)
        self.assertEqual(release.build_number("-25.9.1"), 250901)
        self.assertEqual(release.build_number("24.11"), 241100)


class JavaScriptSyntaxTests(unittest.TestCase):
    @unittest.skipUnless(shutil.which("node"), "node is not installed")
    def test_every_page_script_is_valid_javascript(self):
        scripts = {"read": release.JS_READ_FORM, "save": release.JS_SAVE, "where": release.JS_WHERE,
                   "images": release.JS_IMAGES,
                   "fill": release.JS_FILL % (json.dumps("id-1"), json.dumps('a "quoted" 🎨 line\nnext \\ back'))}
        with tempfile.TemporaryDirectory() as folder:
            for name, code in scripts.items():
                path = Path(folder) / f"{name}.js"
                path.write_text(code, encoding="utf-8")
                done = subprocess.run(["node", "--check", str(path)], capture_output=True, text=True)
                self.assertEqual(done.returncode, 0, f"{name}: {done.stderr}")

    def test_script_results_are_parsed(self):
        self.assertEqual(release.script_value('Script result from tab 5 (https://x/y):\n{"a": 1}'), {"a": 1})
        self.assertEqual(release.script_value('{"a": 1}'), {"a": 1})
        with self.assertRaisesRegex(release.ReleaseError, "unexpected"):
            release.script_value("Script result from tab 5 (https://x):\nnot json")


# --- the AnkiWeb form, against a fake FoxMCP -------------------------------------------------------------------------

FILES_OLD = {"__init__.py": b"old", "manifest.json": b"{}"}
FILES_NEW = {"__init__.py": b"new", "manifest.json": b"{}"}
DESCRIPTION_NEW = "New description 🎨 with an emoji\nand two lines\n"
N_IMAGES = 3


class FakeAnkiWeb:
    """Answers FoxMCP tool calls the way AnkiWeb's upload form and listing page would."""

    def __init__(self, package_size=111, description="old description", ready_after=0, extra_branches=0,
                 save_works=True, serves_new=True, images_ok=True, fill_error=None, versions=("23.10.0", "26.05.0")):
        self.package_size, self.description, self.ready_after = package_size, description, ready_after
        self.extra_branches, self.save_works, self.serves_new = extra_branches, save_works, serves_new
        self.images_ok, self.fill_error, self.versions = images_ok, fill_error, versions
        self.calls, self.tabs, self.closed, self.reads = [], {}, [], 0
        self.filled, self.saved = None, False

    def tools(self):
        return [tool for tool, _ in self.calls]

    def fetch(self, build):
        self.fetched_build = build
        return dict(FILES_NEW if (self.saved and self.save_works and self.serves_new) else FILES_OLD)

    async def __call__(self, tool, **args):
        self.calls.append((tool, args))
        if tool == "tabs_create":
            tab = 100 + len(self.tabs)
            self.tabs[tab] = args["url"]
            return f"Created tab: ID {tab} - {args['url']} - about:blank [cookie store firefox-default]"
        if tool == "tabs_close":
            self.closed.append(args["tab_id"])
            return "Successfully closed tab"
        if tool == "content_load_file":
            return json.dumps({"file_id": "file-1", "name": "colorcoding-groups.ankiaddon", "size": self.package_size})
        assert tool == "content_execute_script", tool
        tab, code = args["tab_id"], args["code"]
        value = self.script(tab, code)
        return f"Script result from tab {tab} ({self.tabs[tab]}):\n{json.dumps(value)}"

    def script(self, tab, code):
        if code.startswith("/*ankiweb:read-form*/"):
            self.reads += 1
            texts = ["Listing title", "tags", "https://support", *self.versions]
            for _ in range(self.extra_branches):
                texts += ["25.01.0", "26.05.0"]
            return {"url": self.tabs[tab], "ready": self.reads > self.ready_after, "texts": texts,
                    "description": self.description, "page": ""}
        if code.startswith("/*ankiweb:fill*/"):
            if self.fill_error:
                return {"error": self.fill_error}
            package_id, description = json.loads("[" + code.rsplit("})(", 1)[1][:-1] + "]")
            self.filled = {"package": package_id, "description": description}
            return {"file": ["colorcoding-groups.ankiaddon", self.package_size],
                    "descriptionUnits": len(description.encode("utf-16-le")) // 2}
        if code.startswith("/*ankiweb:save*/"):
            self.saved = True
            if self.save_works:
                self.description = self.filled["description"]
                self.tabs[tab] = release.INFO_URL + "?cb=1"
            return {"clicked": True}
        if code.startswith("/*ankiweb:where*/"):
            return {"url": self.tabs[tab], "page": "Error: the file must be a zip"}
        if code.startswith("/*ankiweb:images*/"):
            return {"images": [[f"https://raw.githubusercontent.com/x/{i}.png", self.images_ok] for i in range(N_IMAGES)]}
        raise AssertionError(code[:60])


class BrowserFlowTests(unittest.TestCase):
    def setUp(self):
        for name, value in (("POLL_S", 0), ("FORM_TIMEOUT_S", 0.05), ("SAVE_TIMEOUT_S", 0.05),
                            ("SERVED_TIMEOUT_S", 0.05), ("IMAGES_TIMEOUT_S", 0.05)):
            patcher = mock.patch.object(release, name, value)
            patcher.start()
            self.addCleanup(patcher.stop)
        self.tmp = tempfile.mkdtemp()
        self.addCleanup(shutil.rmtree, self.tmp, ignore_errors=True)
        self.package = Path(self.tmp) / "p.ankiaddon"
        self.package.write_bytes(b"x" * 111)
        self.say = mock.patch.object(release, "say", lambda *a: None)
        self.say.start()
        self.addCleanup(self.say.stop)

    def publish(self, fake, **kwargs):
        kwargs.setdefault("confirm", lambda: True)
        return asyncio.run(release.publish(fake, self.package, FILES_NEW, DESCRIPTION_NEW, fetch=fake.fetch, **kwargs))

    def test_a_release_fills_saves_and_verifies_then_closes_every_tab(self):
        fake = FakeAnkiWeb()
        self.assertEqual(self.publish(fake), "published")
        self.assertTrue(fake.saved)
        self.assertEqual(fake.filled, {"package": "file-1", "description": DESCRIPTION_NEW})
        self.assertEqual(fake.fetched_build, 260500)  # from the form's max version
        self.assertEqual(sorted(fake.closed), sorted(fake.tabs))  # every tab it opened is closed

    def test_a_dry_run_fills_the_form_but_never_presses_save(self):
        fake = FakeAnkiWeb()
        asked = []
        self.assertEqual(self.publish(fake, dry_run=True, confirm=lambda: asked.append(1) or True), "dry-run")
        self.assertIsNotNone(fake.filled)
        self.assertFalse(fake.saved)
        self.assertEqual(asked, [])
        self.assertEqual(sorted(fake.closed), sorted(fake.tabs))

    def test_declining_publishes_nothing(self):
        fake = FakeAnkiWeb()
        with self.assertRaisesRegex(release.ReleaseError, "not confirmed"):
            self.publish(fake, confirm=lambda: False)
        self.assertFalse(fake.saved)
        self.assertEqual(sorted(fake.closed), sorted(fake.tabs))

    def test_nothing_to_release_when_the_listing_already_matches(self):
        fake = FakeAnkiWeb(description=DESCRIPTION_NEW.strip() + "\r\n", serves_new=True)
        fake.saved = True  # the served package is already the new one
        self.assertEqual(self.publish(fake), "unchanged")
        self.assertIsNone(fake.filled)
        self.assertEqual(self.publish(fake, force=True), "published")

    def test_a_form_that_never_renders_is_named_and_its_tab_closed(self):
        fake = FakeAnkiWeb(ready_after=10 ** 9)
        with self.assertRaisesRegex(release.ReleaseError, "upload form to appear.*logged in"):
            self.publish(fake)
        self.assertEqual(sorted(fake.closed), sorted(fake.tabs))

    def test_a_save_that_does_not_go_through_reports_the_page(self):
        fake = FakeAnkiWeb(save_works=False)
        with self.assertRaisesRegex(release.ReleaseError, "Save did not go through.*must be a zip"):
            self.publish(fake)
        self.assertEqual(sorted(fake.closed), sorted(fake.tabs))

    def test_a_package_that_does_not_attach_is_caught_before_saving(self):
        fake = FakeAnkiWeb(package_size=5)
        with self.assertRaisesRegex(release.ReleaseError, "did not attach"):
            self.publish(fake)
        self.assertFalse(fake.saved)

    def test_an_expired_file_handle_is_reported(self):
        fake = FakeAnkiWeb(fill_error="the loaded package is gone")
        with self.assertRaisesRegex(release.ReleaseError, "package is gone"):
            self.publish(fake)
        self.assertFalse(fake.saved)

    def test_a_listing_with_several_version_branches_is_refused(self):
        with self.assertRaisesRegex(release.ReleaseError, "2 version branches"):
            self.publish(FakeAnkiWeb(extra_branches=1))

    def test_verification_fails_when_ankiweb_keeps_serving_the_old_package(self):
        with self.assertRaisesRegex(release.ReleaseError, "(?s)published, but verification failed.*differs from HEAD"):
            self.publish(FakeAnkiWeb(serves_new=False))

    def test_verification_fails_when_the_images_do_not_render(self):
        with self.assertRaisesRegex(release.ReleaseError, f"{N_IMAGES} of {N_IMAGES} images|images on the listing"):
            asyncio.run(release.publish(FakeAnkiWeb(images_ok=False), self.package, FILES_NEW,
                                        DESCRIPTION_NEW + "".join(f'<img src="https://raw.githubusercontent.com/x/{i}.png">\n'
                                                                  for i in range(N_IMAGES)),
                                        fetch=FakeAnkiWeb().fetch, confirm=lambda: True))

    def test_check_sync_reports_drift_and_agreement(self):
        in_sync = FakeAnkiWeb(description=DESCRIPTION_NEW)
        in_sync.saved = True
        self.assertEqual(asyncio.run(release.check_sync(in_sync, FILES_NEW, DESCRIPTION_NEW, fetch=in_sync.fetch)), [])
        drifted = FakeAnkiWeb(description="something else")
        self.assertEqual(len(asyncio.run(release.check_sync(drifted, FILES_NEW, DESCRIPTION_NEW, fetch=drifted.fetch))), 1)
        self.assertFalse(drifted.saved)
        self.assertIsNone(drifted.filled)  # checking never touches the form
        self.assertEqual(set(drifted.tools()), {"tabs_create", "content_execute_script", "tabs_close"})

    def test_pull_description_returns_the_live_text(self):
        fake = FakeAnkiWeb(description="live text")
        self.assertEqual(asyncio.run(release.pull_description(fake)), "live text")
        self.assertEqual(sorted(fake.closed), sorted(fake.tabs))


class CommandLineTests(unittest.TestCase):
    def test_a_non_interactive_publish_needs_yes(self):
        with mock.patch.object(release, "load_foxmcp_client"), mock.patch.object(sys, "stdin", io.StringIO("")), \
                mock.patch.object(release, "say"):
            self.assertEqual(release.main([]), 3)

    def test_modes_are_exclusive(self):
        with self.assertRaises(SystemExit), contextlib.redirect_stderr(io.StringIO()):
            release.parse_args(["--check", "--dry-run"])


if __name__ == "__main__":
    unittest.main()
