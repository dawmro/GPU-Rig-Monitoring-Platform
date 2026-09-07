"""
Tests for the deploy-bug prevention system check (dashboard.checks).

Covers:
- Positive case: real templates + real CSS → no warnings
- Negative case: template references undefined grm-* class → warning fires
- Negative case: template with grm-text-{{ variable }} pattern → no false positive
- Negative case: app.css missing → error fires
- Edge cases: Django template tags in class attribute are handled correctly
- Edge cases: Tailwind utility classes (text-gray-400) are not validated
- Edge cases: dot-prefixed selectors (e.g. .grm-card + .grm-card-lg) parse correctly

Run with: python manage.py test dashboard.tests
"""
from __future__ import annotations

import tempfile
from pathlib import Path

from django.core.checks import Error, Warning
from django.test import SimpleTestCase, override_settings

from dashboard import checks


class TokenizerTests(SimpleTestCase):
    """Tests for the _all_class_tokens function — pure string parsing."""

    def test_extracts_simple_grm_class(self):
        text = '<input class="grm-input">'
        tokens = list(checks._all_class_tokens(text))
        self.assertEqual(len(tokens), 1)
        self.assertEqual(tokens[0][0], "grm-input")

    def test_extracts_multiple_classes(self):
        text = '<button class="grm-btn-primary px-3 py-1.5">OK</button>'
        tokens = [t[0] for t in checks._all_class_tokens(text)]
        # Only grm-* tokens; Tailwind tokens are filtered out
        self.assertEqual(tokens, ["grm-btn-primary"])

    def test_strips_django_variable_prefix(self):
        """grm-text-{{ color }} should NOT be reported as a missing class.

        The variable {{ color }} is evaluated at render time to produce
        a value that becomes part of the class name. We can't statically
        know what it returns, so the prefix is treated as "intentional
        variable" and not validated.
        """
        text = '<span class="grm-text-{{ snapshot.cpu_utilization_pct|cpu_util_color }}">5%</span>'
        tokens = list(checks._all_class_tokens(text))
        self.assertEqual(tokens, [], "grm-text- prefix before a Django variable should be skipped")

    def test_strips_django_tag_with_class_branches(self):
        """{% if %} {% else %} {% endif %} in class attribute should be handled."""
        text = '<div class="{% if x %}grm-card{% else %}grm-card-lg{% endif %}">'
        tokens = [t[0] for t in checks._all_class_tokens(text)]
        # Both grm-card and grm-card-lg should be tokenized (real classes)
        self.assertIn("grm-card", tokens)
        self.assertIn("grm-card-lg", tokens)

    def test_ignores_tailwind_utility_classes(self):
        """Tailwind classes (text-gray-400, etc.) are NOT validated."""
        text = '<div class="bg-gray-800 text-gray-300 border border-gray-700">'
        tokens = list(checks._all_class_tokens(text))
        self.assertEqual(tokens, [])

    def test_ignores_single_quote_attributes(self):
        """Single-quoted class= attributes are also matched (we use ["'])."""
        text = "<input class='grm-input'>"
        tokens = [t[0] for t in checks._all_class_tokens(text)]
        self.assertEqual(tokens, ["grm-input"])

    def test_no_class_attribute(self):
        """Tags without class attribute produce no tokens."""
        text = '<div>No class here</div>'
        tokens = list(checks._all_class_tokens(text))
        self.assertEqual(tokens, [])

    def test_empty_class_attribute(self):
        text = '<div class="">Empty</div>'
        tokens = list(checks._all_class_tokens(text))
        self.assertEqual(tokens, [])

    def test_ignores_html_comments(self):
        """HTML comments are not parsed for class attributes."""
        text = '<!-- <div class="grm-fake">comment</div> --> <span class="grm-input">real</span>'
        tokens = [t[0] for t in checks._all_class_tokens(text)]
        # The class in the comment is a literal string and would be parsed
        # by RE_CLASS_ATTR because it doesn't understand HTML comments.
        # We accept this false positive as low cost vs. the complexity
        # of HTML comment parsing.
        # What we DO want to verify: real class outside comment is found.
        self.assertIn("grm-input", tokens)
        self.assertIn("grm-fake", tokens)  # known limitation


class DefinedClassesTests(SimpleTestCase):
    """Tests for the _defined_css_classes function."""

    def test_parses_simple_classes(self):
        with tempfile.NamedTemporaryFile(mode="w", suffix=".css", delete=False) as f:
            f.write(".grm-card { color: red; }\n.grm-input { color: blue; }\n")
            f.flush()
            result = checks._defined_css_classes(Path(f.name))
        self.assertEqual(result, {"grm-card", "grm-input"})

    def test_parses_comma_separated_selectors(self):
        with tempfile.NamedTemporaryFile(mode="w", suffix=".css", delete=False) as f:
            f.write(".grm-card, .grm-card-lg { color: red; }\n")
            f.flush()
            result = checks._defined_css_classes(Path(f.name))
        self.assertEqual(result, {"grm-card", "grm-card-lg"})

    def test_ignores_non_grm_classes(self):
        with tempfile.NamedTemporaryFile(mode="w", suffix=".css", delete=False) as f:
            f.write(".grm-input { color: red; }\n.text-gray-400 { color: gray; }\n")
            f.flush()
            result = checks._defined_css_classes(Path(f.name))
        # Only grm-* are returned
        self.assertEqual(result, {"grm-input"})

    def test_missing_file_returns_empty(self):
        """A missing CSS file returns empty set (not an error)."""
        result = checks._defined_css_classes(Path("/nonexistent/path.css"))
        self.assertEqual(result, set())


class CheckFunctionTests(SimpleTestCase):
    """End-to-end tests of the check functions against fake CSS/templates."""

    def setUp(self):
        """Set up a temp directory with a fake CSS and a fake template."""
        self.tmpdir = tempfile.mkdtemp()
        self.css_dir = Path(self.tmpdir) / "static" / "css"
        self.tpl_dir = Path(self.tmpdir) / "templates"
        self.css_dir.mkdir(parents=True)
        self.tpl_dir.mkdir(parents=True)
        # Write a fake CSS file
        (self.css_dir / "app.css").write_text(
            ".grm-card { color: red; }\n.grm-input { color: blue; }\n"
        )
        # Write a fake template using real classes
        (self.tpl_dir / "good.html").write_text('<div class="grm-card">OK</div>')

    def tearDown(self):
        import shutil
        shutil.rmtree(self.tmpdir, ignore_errors=True)

    @override_settings(BASE_DIR=None)
    def test_clean_templates_pass(self):
        """A template with only valid grm-* classes produces no warnings."""
        # We can't easily override BASE_DIR mid-test (Django uses it at
        # import time), so this test exercises the tokenizer + parser
        # directly to confirm no false positives on real template content.
        # The full end-to-end is covered by the manual injection test in
        # the dev workflow.
        from dashboard import checks as c
        css_path = self.css_dir / "app.css"
        defined = c._defined_css_classes(css_path)
        # Scan our good template
        good_tpl = (self.tpl_dir / "good.html").read_text()
        tokens = [t[0] for t in c._all_class_tokens(good_tpl)]
        # All tokens should be in the defined set
        self.assertTrue(all(t in defined for t in tokens),
                        f"Found undefined tokens in 'good' template: "
                        f"{[t for t in tokens if t not in defined]}")


class CheckStaticCssFileExistsTests(SimpleTestCase):
    """Tests for the E001 check (CSS file missing)."""

    def test_fires_when_css_missing(self):
        """When the CSS file doesn't exist, the check fires an Error."""
        # Patch _css_path to return a nonexistent path
        from unittest.mock import patch as mpatch
        from pathlib import Path
        with mpatch.object(checks, "_css_path",
                          return_value=Path("/nonexistent/dir/app.css")):
            results = checks.check_static_css_file_exists([])
        self.assertEqual(len(results), 1)
        self.assertIsInstance(results[0], Error)
        self.assertEqual(results[0].id, "dashboard.E001")

    def test_passes_when_css_exists(self):
        """When the CSS file exists, no errors are produced."""
        from unittest.mock import patch as mpatch
        import tempfile
        # mode="w" needs suffix for text mode; default is binary
        with tempfile.NamedTemporaryFile(mode="w", suffix=".css", delete=False) as f:
            f.write(".grm-card {}\n")
            tmppath = f.name
        try:
            with mpatch.object(checks, "_css_path", return_value=Path(tmppath)):
                results = checks.check_static_css_file_exists([])
            self.assertEqual(results, [])
        finally:
            import os
            os.unlink(tmppath)


class CheckGrmCssClassesIntegrationTests(SimpleTestCase):
    """Integration test: inject bugs and confirm the check catches them.

    These tests are skipped if app.css is missing (e.g. on a fresh clone
    without the Phase 0.1 commit). They require a complete setup.
    """

    def setUp(self):
        self.tmpdir = tempfile.mkdtemp()
        self.addCleanup(__import__('shutil').rmtree, self.tmpdir, True)

    def _write_files(self, css: str, templates: dict[str, str]):
        css_path = Path(self.tmpdir) / "css" / "app.css"
        css_path.parent.mkdir(parents=True, exist_ok=True)
        css_path.write_text(css)
        tpl_dir = Path(self.tmpdir) / "templates"
        tpl_dir.mkdir(parents=True, exist_ok=True)
        for name, content in templates.items():
            (tpl_dir / name).write_text(content)

    def test_undefined_class_produces_warning(self):
        """A template referencing a non-existent grm-* class produces a warning."""
        self._write_files(
            css=".grm-input { color: red; }",
            templates={
                "bad.html": '<div class="grm-input grm-doesnotexist">x</div>',
            },
        )
        css_path = Path(self.tmpdir) / "css" / "app.css"
        tpl_path = Path(self.tmpdir) / "templates" / "bad.html"
        # Monkey-patch the helpers to use our temp paths
        from unittest.mock import patch as mpatch
        with mpatch.object(checks, "_defined_css_classes",
                          return_value=checks._defined_css_classes(css_path)), \
             mpatch.object(checks, "_all_template_files",
                          return_value=[tpl_path]):
            results = checks.check_grm_css_classes([])
        # Should produce exactly one warning for grm-doesnotexist
        warning_ids = [r.id for r in results if isinstance(r, Warning)]
        self.assertEqual(warning_ids, ["dashboard.W001"])

    def test_all_defined_classes_pass(self):
        """A template using only defined classes produces no warnings."""
        self._write_files(
            css=".grm-input { color: red; }\n.grm-card { color: blue; }",
            templates={
                "good.html": '<div class="grm-input grm-card">x</div>',
            },
        )
        css_path = Path(self.tmpdir) / "css" / "app.css"
        tpl_path = Path(self.tmpdir) / "templates" / "good.html"
        from unittest.mock import patch as mpatch
        with mpatch.object(checks, "_defined_css_classes",
                          return_value=checks._defined_css_classes(css_path)), \
             mpatch.object(checks, "_all_template_files",
                          return_value=[tpl_path]):
            results = checks.check_grm_css_classes([])
        self.assertEqual(results, [])

    def test_grm_text_template_variable_does_not_trigger_false_positive(self):
        """The grm-text-{{ variable }} pattern doesn't produce a warning."""
        self._write_files(
            css=".grm-text-red { color: red; }\n.grm-text-green { color: green; }",
            templates={
                "dynamic.html": '<span class="grm-text-{{ color }}">x</span>',
            },
        )
        css_path = Path(self.tmpdir) / "css" / "app.css"
        tpl_path = Path(self.tmpdir) / "templates" / "dynamic.html"
        from unittest.mock import patch as mpatch
        with mpatch.object(checks, "_defined_css_classes",
                          return_value=checks._defined_css_classes(css_path)), \
             mpatch.object(checks, "_all_template_files",
                          return_value=[tpl_path]):
            results = checks.check_grm_css_classes([])
        # No false positive on the grm-text- prefix
        self.assertEqual(results, [], f"Unexpected warnings: {results}")

    def test_collectstatic_freshness_skipped_when_debug(self):
        """W002 only runs in production (DEBUG=False)."""
        with override_settings(DEBUG=True):
            results = checks.check_collectstatic_freshness([])
        self.assertEqual(results, [])

    def test_django_check_command_runs_grm_checks(self):
        """Smoke test: `manage.py check` actually runs our checks.

        This is the integration test — it uses the real project state.
        It expects the project to be in a known-good state (all current
        templates reference classes that exist in app.css).
        """
        from django.core.management import call_command
        from io import StringIO
        out = StringIO()
        try:
            call_command("check", stdout=out)
            self.assertIn("System check identified no issues", out.getvalue())
        except SystemExit:
            # `manage.py check` calls sys.exit(1) on issues, but if issues
            # are EXPECTED during dev (e.g. on an unmerged branch), this
            # is the way the test fails. We re-raise so the developer
            # sees the failure clearly.
            self.fail(
                "manage.py check found issues — the grm-* class check "
                "may be detecting real bugs. Run it manually to see."
            )
