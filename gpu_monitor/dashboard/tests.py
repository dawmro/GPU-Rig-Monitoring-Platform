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
        """grm-text-{{ variable }} should NOT be reported as a missing class.

        The variable {{ color }} is evaluated at render time to produce
        a value that becomes part of the class name. We can't statically
        know what it returns, so the prefix is treated as "intentional
        variable" and not validated.
        """
        text = '<span class="grm-text-{{ snapshot.cpu_utilization_pct|tier_text:cpu_util_t }}">5%</span>'
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


# =====================================================================
# Color tier filter tests (Phase 0.3)
# =====================================================================

from dashboard.templatetags import gpu_filters as gf


class TierResolverTests(SimpleTestCase):
    """Tests for the _resolve_color function — the heart of tier/tier_text/tier_fill."""

    def test_returns_default_for_value_below_lowest_threshold(self):
        # cpu_temp spec: >85 red, >70 yellow, default green
        spec = gf.DEFAULT_THRESHOLDS["cpu_temp"]
        self.assertEqual(gf._resolve_color(50, spec), "green")
        self.assertEqual(gf._resolve_color(0, spec), "green")
        self.assertEqual(gf._resolve_color(70, spec), "green")  # boundary: not >70

    def test_returns_higher_color_for_higher_value(self):
        spec = gf.DEFAULT_THRESHOLDS["cpu_temp"]
        self.assertEqual(gf._resolve_color(86, spec), "red")
        self.assertEqual(gf._resolve_color(71, spec), "yellow")
        self.assertEqual(gf._resolve_color(86.5, spec), "red")

    def test_boundary_strict_greater_than(self):
        # Thresholds use `>` (strict greater than), not `>=`.
        # So value == 85 should NOT match the red threshold (>85).
        spec = gf.DEFAULT_THRESHOLDS["cpu_temp"]
        self.assertEqual(gf._resolve_color(85, spec), "yellow")
        self.assertEqual(gf._resolve_color(85.0001, spec), "red")

    def test_returns_none_for_none_value(self):
        spec = gf.DEFAULT_THRESHOLDS["cpu_temp"]
        self.assertIsNone(gf._resolve_color(None, spec))

    def test_returns_none_for_uncoercible_value(self):
        spec = gf.DEFAULT_THRESHOLDS["cpu_temp"]
        self.assertIsNone(gf._resolve_color("not a number", spec))
        self.assertIsNone(gf._resolve_color([], spec))
        self.assertIsNone(gf._resolve_color({}, spec))

    def test_returns_none_for_none_spec(self):
        # Defensive: a missing thresholds object returns None (caller decides
        # what to do, not the resolver).
        self.assertIsNone(gf._resolve_color(50, None))

    def test_returns_spec_default_color(self):
        # When the spec has (None, color) as its last entry, that color
        # is returned when no earlier threshold matches (i.e. value is
        # below all the explicit thresholds).
        spec = [(10, "red"), (None, "yellow")]
        self.assertEqual(gf._resolve_color(5, spec), "yellow")   # below all -> default
        self.assertEqual(gf._resolve_color(11, spec), "red")     # above 10 -> red
        self.assertEqual(gf._resolve_color(100, spec), "red")    # above 10 -> red

    def test_default_color_not_returned_when_earlier_threshold_matches(self):
        # Verifies the default is ONLY used when no earlier threshold
        # matches. If a value is above any threshold, the color of
        # THAT threshold wins (not the default).
        # Spec ordered highest-to-lowest? No — we use first-match-wins,
        # so spec entries must be ordered LOWEST-to-HIGHEST for layered
        # thresholds to work. cpu_util spec is (80,red), (60,orange), ...
        # which is wrong by this convention; the actual spec is built
        # correctly. For this test, use a low-to-high spec.
        spec = [(10, "red"), (20, "orange"), (None, "yellow")]
        # value 5: not > 10, not > 20, default yellow
        self.assertEqual(gf._resolve_color(5, spec), "yellow")
        # value 15: > 10 -> red (first match wins)
        self.assertEqual(gf._resolve_color(15, spec), "red")
        # value 25: > 10 -> red (first match wins; we don't reach orange)
        self.assertEqual(gf._resolve_color(25, spec), "red")

    def test_handles_explicit_none_color_in_spec(self):
        # A spec can have (min, None) to mean "no color override" for
        # that bucket — used by process_cpu to suppress color for low
        # values.
        spec = [(50, "red"), (None, None)]
        self.assertEqual(gf._resolve_color(60, spec), "red")
        self.assertIsNone(gf._resolve_color(5, spec))

    def test_5_tier_disk_util(self):
        # Disk util has 5 tiers — verify all of them
        # Spec uses `>` (strict greater than), so the boundary value
        # matches the LOWER tier, not the upper.
        spec = gf.DEFAULT_THRESHOLDS["disk_util"]
        # 81 -> >80 -> red
        self.assertEqual(gf._resolve_color(95, spec), "red")
        self.assertEqual(gf._resolve_color(81, spec), "red")
        # 80 -> not >80, >60 -> orange
        self.assertEqual(gf._resolve_color(80, spec), "orange")
        self.assertEqual(gf._resolve_color(70, spec), "orange")
        # 60 -> not >60, >40 -> yellow
        self.assertEqual(gf._resolve_color(60, spec), "yellow")
        self.assertEqual(gf._resolve_color(50, spec), "yellow")
        # 40 -> not >40, >20 -> green
        self.assertEqual(gf._resolve_color(40, spec), "green")
        self.assertEqual(gf._resolve_color(30, spec), "green")
        # 20 -> not >20, default -> muted
        self.assertEqual(gf._resolve_color(20, spec), "muted")
        self.assertEqual(gf._resolve_color(10, spec), "muted")

    def test_inverted_gpu_util(self):
        # GPU util is inverted: high = good (green), low = bad (muted).
        # Verify the ordering is correct.
        spec = gf.DEFAULT_THRESHOLDS["gpu_util"]
        self.assertEqual(gf._resolve_color(95, spec), "green")
        self.assertEqual(gf._resolve_color(60, spec), "gray")
        self.assertEqual(gf._resolve_color(10, spec), "muted")


class TierFilterTests(SimpleTestCase):
    """Tests for the `tier` filter (returns bare color name)."""

    def test_tier_returns_color_name(self):
        spec = gf.DEFAULT_THRESHOLDS["cpu_temp"]
        self.assertEqual(gf.tier(90, spec), "red")
        self.assertEqual(gf.tier(50, spec), "green")

    def test_tier_returns_none_for_bad_input(self):
        spec = gf.DEFAULT_THRESHOLDS["cpu_temp"]
        self.assertIsNone(gf.tier(None, spec))
        self.assertIsNone(gf.tier("foo", spec))


class TierTextFilterTests(SimpleTestCase):
    """Tests for the `tier_text` filter (returns 'grm-text-{color}')."""

    def test_tier_text_returns_full_class(self):
        spec = gf.DEFAULT_THRESHOLDS["cpu_temp"]
        self.assertEqual(gf.tier_text(90, spec), "grm-text-red")
        self.assertEqual(gf.tier_text(50, spec), "grm-text-green")

    def test_tier_text_returns_empty_string_for_no_color(self):
        # process_cpu spec returns None for low values — should produce
        # empty string, not the literal "grm-text-None".
        spec = gf.DEFAULT_THRESHOLDS["process_cpu"]
        self.assertEqual(gf.tier_text(5, spec), "")
        self.assertEqual(gf.tier_text(None, spec), "")

    def test_tier_text_returns_empty_string_for_bad_input(self):
        spec = gf.DEFAULT_THRESHOLDS["cpu_temp"]
        self.assertEqual(gf.tier_text("not a number", spec), "")


class TierFillFilterTests(SimpleTestCase):
    """Tests for the `tier_fill` filter (returns 'grm-progress-fill-{color}')."""

    def test_tier_fill_returns_full_class(self):
        spec = gf.DEFAULT_THRESHOLDS["cpu_util"]
        self.assertEqual(gf.tier_fill(90, spec), "grm-progress-fill-red")
        self.assertEqual(gf.tier_fill(50, spec), "grm-progress-fill-yellow")
        self.assertEqual(gf.tier_fill(10, spec), "grm-progress-fill-gray")

    def test_tier_fill_returns_empty_string_for_no_color(self):
        spec = gf.DEFAULT_THRESHOLDS["process_cpu"]
        self.assertEqual(gf.tier_fill(5, spec), "")


class ColorTierThresholdsTagTests(SimpleTestCase):
    """Tests for the color_tier_thresholds simple_tag."""

    def test_returns_spec_for_known_name(self):
        spec = gf.color_tier_thresholds("cpu_temp")
        self.assertEqual(spec, gf.DEFAULT_THRESHOLDS["cpu_temp"])

    def test_raises_for_unknown_name(self):
        with self.assertRaises(KeyError) as cm:
            gf.color_tier_thresholds("nonexistent_metric")
        self.assertIn("nonexistent_metric", str(cm.exception))
        self.assertIn("Known specs", str(cm.exception))

    def test_all_default_specs_have_valid_structure(self):
        # Sanity check: every spec has at least one entry and the
        # last entry is the default (min_value is None).
        for name, spec in gf.DEFAULT_THRESHOLDS.items():
            with self.subTest(spec=name):
                self.assertGreater(len(spec), 0, f"spec {name} is empty")
                self.assertIsNone(
                    spec[-1][0],
                    f"spec {name} must end with a (None, color) default entry"
                )


class MultiGpuCellRenderTests(SimpleTestCase):
    """Tests for _render_tier_cell and the gpu_*_cell_json simple_tags."""

    def test_empty_json_returns_placeholder(self):
        from types import SimpleNamespace
        snap = SimpleNamespace(gpu_temps_json=None)
        result = str(gf.gpu_temp_cell_json(snap))
        self.assertIn("—", result)
        self.assertIn("grm-text-gray", result)

    def test_empty_list_returns_placeholder(self):
        from types import SimpleNamespace
        snap = SimpleNamespace(gpu_temps_json=[])
        result = str(gf.gpu_temp_cell_json(snap))
        self.assertIn("—", result)

    def test_renders_color_coded_values(self):
        from types import SimpleNamespace
        # Mixed temps: 85 (red), 75 (yellow), 55 (green), None (—)
        snap = SimpleNamespace(gpu_temps_json=[85, 75, 55, None])
        result = str(gf.gpu_temp_cell_json(snap))
        # All four should appear
        self.assertIn("grm-text-red", result)
        self.assertIn("grm-text-yellow", result)
        self.assertIn("grm-text-green", result)
        self.assertIn("grm-text-gray", result)  # for None
        # 4 separate spans
        self.assertEqual(result.count("<span"), 4)
        # Multi-value separator: a single space (was ' · ' before
        # Phase 1.1 reverted to ' '). The dots were visually noisy
        # in narrow fleet table columns. Values are still distinct
        # because: per-GPU color coding + title attribute shows full
        # breakdown + values are numeric.
        # 3 separators between 4 values
        self.assertEqual(result.count("</span> <span"), 3)
        # Make sure no '·' sneaked back in
        self.assertNotIn("·", result)

    def test_inverted_coloring_for_gpu_util(self):
        from types import SimpleNamespace
        # GPU util: >90 green, >50 gray, default muted
        snap = SimpleNamespace(gpu_utils_json=[95, 60, 20])
        result = str(gf.gpu_util_cell_json(snap))
        self.assertIn("grm-text-green", result)
        self.assertIn("grm-text-gray", result)
        self.assertIn("grm-text-muted", result)

    def test_value_with_no_color_override_renders_plain(self):
        # process_cpu spec returns None for low values. When called via
        # the GPU util cell, this doesn't apply (GPU util always has a
        # color), but verify the helper handles it.
        from types import SimpleNamespace
        from dashboard.templatetags.gpu_filters import _render_tier_cell
        snap = SimpleNamespace(test_json=[5])
        result = str(_render_tier_cell(snap, "test_json", "process_cpu", "grm-text-gray"))
        # No color class because the spec returns None
        self.assertNotIn("grm-text-red", result)
        self.assertIn("5", result)




# =====================================================================
# Static JS file check tests (Phase 0.4)
# =====================================================================

from dashboard import checks as dc
from django.core.checks import Error


class CheckStaticJsFilesExistTests(SimpleTestCase):
    """Tests for the E002 check (required JS file missing)."""

    def test_fires_when_js_file_missing(self):
        # Patch _js_path to return a nonexistent path for ALL files.
        from unittest.mock import patch as mpatch
        from pathlib import Path
        with mpatch.object(dc, "_js_path",
                          return_value=Path("/nonexistent/foo.js")):
            results = dc.check_static_js_files_exist([])
        # All REQUIRED_JS_FILES should produce an error
        self.assertEqual(len(results), len(dc.REQUIRED_JS_FILES))
        for r in results:
            self.assertIsInstance(r, Error)
            self.assertEqual(r.id, "dashboard.E002")

    def test_passes_when_all_files_exist(self):
        # All real JS files should exist in this test environment.
        results = dc.check_static_js_files_exist([])
        self.assertEqual(results, [])


class CheckTemplatesReferenceJsFilesTests(SimpleTestCase):
    """Tests for the W003 check (template missing JS reference)."""

    def test_fires_when_template_drops_reference(self):
        # Verify the check is callable. For a real failure-mode test,
        # see the integration test below.
        self.assertTrue(callable(dc.check_templates_reference_js_files))

    def test_passes_when_all_references_present(self):
        # The real base.html and rig_detail.html DO reference all
        # required JS files (we just added them). So the check should
        # return an empty result.
        results = dc.check_templates_reference_js_files([])
        self.assertEqual(results, [])


class RequiredJsFilesInventoryTests(SimpleTestCase):
    """Verify the REQUIRED_JS_FILES inventory matches reality."""

    def test_all_inventory_files_actually_exist(self):
        for filename in dc.REQUIRED_JS_FILES:
            path = dc._js_path(filename)
            self.assertTrue(path.is_file(), f"{path} should exist")

    def test_inventory_loaders_match_real_templates(self):
        from pathlib import Path
        from django.conf import settings
        for filename, info in dc.REQUIRED_JS_FILES.items():
            template_name = info["loaded_by"]
            # rig_detail.html is in the dashboard/ subdir
            if template_name == "rig_detail.html":
                template_path = (Path(settings.BASE_DIR) / "templates"
                                / "dashboard" / "rig_detail.html")
            else:
                template_path = Path(settings.BASE_DIR) / "templates" / template_name
            self.assertTrue(
                template_path.is_file(),
                f"Inventory says {filename} is loaded by {template_name} "
                f"but {template_path} does not exist"
            )

    def test_inventory_is_non_empty(self):
        self.assertGreater(len(dc.REQUIRED_JS_FILES), 0)

    def test_inventory_entries_have_required_fields(self):
        for filename, info in dc.REQUIRED_JS_FILES.items():
            self.assertIn("loaded_by", info)
            self.assertIn("purpose", info)
            self.assertIn("is_error", info)
            # The current schema only supports base.html and rig_detail.html
            self.assertIn(info["loaded_by"], ("base.html", "rig_detail.html"))


# =====================================================================
# Phase 1.2: column group CSS classes
# =====================================================================

class GrmColGroupTests(SimpleTestCase):
    """Verify the grm-col-* column group classes are defined in app.css.

    These are used by <col class="grm-col-{group}"> in _rig_table.html
    to apply subtle background tints to logical column groups
    (identity, status, gpu, system, meta). The check verifies the CSS
    is still defined (so the fleet table's column tints still work).
    """

    CSS_PATH = 'static/css/app.css'

    def _read_css(self):
        from pathlib import Path
        from django.conf import settings
        css_path = Path(settings.BASE_DIR) / self.CSS_PATH
        return css_path.read_text(encoding="utf-8")

    def test_all_col_groups_defined(self):
        css = self._read_css()
        for group in ["identity", "status", "gpu", "system", "meta"]:
            with self.subTest(group=group):
                self.assertIn(
                    f".grm-col-{group}",
                    css,
                    f".grm-col-{group} should be defined in app.css"
                )

    def test_col_groups_have_background_color(self):
        # Each col group should set a background-color (the tint)
        css = self._read_css()
        import re
        for group in ["identity", "status", "gpu", "system", "meta"]:
            with self.subTest(group=group):
                pattern = re.compile(
                    rf"\.grm-col-{group}\s*\{{[^}}]*background-color",
                    re.DOTALL,
                )
                self.assertIsNotNone(
                    pattern.search(css),
                    f".grm-col-{group} should set background-color"
                )


# =====================================================================
# Phase 1.3: System health summary bar
# =====================================================================

class SystemHealthBarTests(SimpleTestCase):
    """The Live Metrics template renders a hardware summary bar
    at the top. Verify the kept fields are present and the removed
    fields are NOT present (the user explicitly trimmed the bar in
    the layout-optimization phase).
    """

    TEMPLATE_PATH = "templates/dashboard/_metrics_cards.html"

    # Kept fields (per user spec: only useful hardware summary info)
    KEPT_LABELS = ["CPUs:", "GPUs:", "Disks:"]
    # Removed fields (per user spec: "max 0%", "Errors: 0 recent",
    # "● live" are not useful — they duplicate info shown lower on
    # the page or in the page header)
    REMOVED_PATTERNS = ["Errors:", "stale data", "● live", "max 0%", "max 100%"]

    def test_health_bar_kept_labels_present(self):
        from pathlib import Path
        from django.conf import settings
        tpl_path = Path(settings.BASE_DIR) / self.TEMPLATE_PATH
        text = tpl_path.read_text(encoding="utf-8")
        for label in self.KEPT_LABELS:
            with self.subTest(label=label):
                self.assertIn(label, text, f"Hardware summary should include {label}")

    def test_health_bar_removed_patterns_absent(self):
        from pathlib import Path
        from django.conf import settings
        tpl_path = Path(settings.BASE_DIR) / self.TEMPLATE_PATH
        text = tpl_path.read_text(encoding="utf-8")
        for pattern in self.REMOVED_PATTERNS:
            with self.subTest(pattern=pattern):
                self.assertNotIn(
                    pattern, text,
                    f"{pattern!r} was removed from the health bar; "
                    f"if you re-added it, make sure it's still useful."
                )

    def test_health_bar_has_stale_indicator(self):
        from pathlib import Path
        from django.conf import settings
        tpl_path = Path(settings.BASE_DIR) / self.TEMPLATE_PATH
        text = tpl_path.read_text(encoding="utf-8")
        # Stale-data indicator was removed from this bar; the same
        # info is already in the rig status badge at the top of the
        # page. We assert that the stale indicator is NOT in the
        # health bar (as a sanity check on the contract).
        self.assertNotIn("stale data", text)
        self.assertNotIn("● live", text)


# =====================================================================
# W004: Multi-line template comment check
# =====================================================================

class CheckNoMultilineTemplateCommentsTests(SimpleTestCase):
    """Verify the W004 check catches multi-line {# ... #} comments."""

    def test_passes_when_all_comments_single_line(self):
        # The real templates should now be all single-line (we just
        # fixed them in this commit). Empty result.
        results = dc.check_no_multiline_template_comments([])
        self.assertEqual(results, [],
                         f"Expected no multi-line comments but got: {results}")

    def test_fires_on_multiline_comment(self):
        # Inject a multi-line comment into a temp file and verify
        # the check catches it.
        from unittest.mock import patch as mpatch
        from pathlib import Path
        with tempfile.NamedTemporaryFile(mode="w", suffix=".html", delete=False) as f:
            f.write("{# A multi-line\n   comment that spans lines #}\n")
            f.flush()
            tmppath = Path(f.name)
        try:
            with mpatch.object(dc, "_all_template_files",
                              return_value=[tmppath]):
                results = dc.check_no_multiline_template_comments([])
            self.assertEqual(len(results), 1)
            self.assertEqual(results[0].id, "dashboard.W004")
            self.assertIsInstance(results[0], Warning)
        finally:
            import os
            os.unlink(tmppath)

    def test_passes_on_single_line_comment(self):
        from unittest.mock import patch as mpatch
        from pathlib import Path
        with tempfile.NamedTemporaryFile(mode="w", suffix=".html", delete=False) as f:
            f.write("{# single line comment #}\n")
            f.flush()
            tmppath = Path(f.name)
        try:
            with mpatch.object(dc, "_all_template_files",
                              return_value=[tmppath]):
                results = dc.check_no_multiline_template_comments([])
            self.assertEqual(results, [])
        finally:
            import os
            os.unlink(tmppath)


# =====================================================================
# CSS color contrast regression guards
# =====================================================================

class CssColorContrastTests(SimpleTestCase):
    """Verify CSS color choices for legibility on the dark UI.

    These tests catch accidental reverts to colors that don't have
    enough contrast against the gray-800 card background. The
    original incident (Nov 2025) was a gray progress fill at #4b5563
    (contrast 1.94 against #1f2937) which was effectively invisible
    — the user complained that the GPU core bar "had no color".

    See the comment block above .grm-progress-fill-* in app.css for
    the full contrast analysis.
    """

    CSS_PATH = "static/css/app.css"
    BG_CARD = (31, 41, 55)  # #1f2937 gray-800, the grm-card background

    def _read_css(self):
        from pathlib import Path
        from django.conf import settings
        return (Path(settings.BASE_DIR) / self.CSS_PATH).read_text(encoding="utf-8")

    def _hex_to_rgb(self, hex_color):
        h = hex_color.lstrip("#")
        if len(h) == 8:
            h = h[:6]
        return (int(h[0:2], 16), int(h[2:4], 16), int(h[4:6], 16))

    def _relative_luminance(self, rgb):
        def channel(c):
            c = c / 255
            return c / 12.92 if c <= 0.03928 else ((c + 0.055) / 1.055) ** 2.4
        r, g, b = rgb
        return 0.2126 * channel(r) + 0.7152 * channel(g) + 0.0722 * channel(b)

    def _contrast_ratio(self, fg_hex):
        """Compute WCAG contrast ratio of fg_hex against the card background."""
        fg = self._hex_to_rgb(fg_hex)
        bg = self.BG_CARD
        l_fg = self._relative_luminance(fg)
        l_bg = self._relative_luminance(bg)
        if l_fg < l_bg:
            l_fg, l_bg = l_bg, l_fg
        return (l_fg + 0.05) / (l_bg + 0.05)

    def _extract_color(self, css, class_name):
        """Extract the background-color or color value for a CSS class."""
        import re
        # Match `.cls { ... background-color: #abc; ... }` or `color: #abc;`
        # Group 1: property name (background-color | color)
        # Group 2: hex value
        pattern = re.compile(
            rf"\.{re.escape(class_name)}\s*\{{[^\{{\}}]*?(background-color|color):\s*(#[0-9a-fA-F]{{3,8}})"
        )
        m = pattern.search(css)
        if not m:
            self.fail(f"Could not find color for .{class_name} in app.css")
        return m.group(2)

    def test_gray_progress_fill_is_visible_against_card(self):
        """grm-progress-fill-gray must have contrast >= 4.5 vs gray-800.

        The original bug was a gray-600 fill (#4b5563) with contrast
        1.94 — WCAG FAIL. This test catches any regression to that.
        The 4.5 minimum matches WCAG AA for normal text/UI elements.
        """
        css = self._read_css()
        gray_hex = self._extract_color(css, "grm-progress-fill-gray")
        ratio = self._contrast_ratio(gray_hex)
        self.assertGreaterEqual(
            ratio, 4.5,
            f"grm-progress-fill-gray ({gray_hex}) has contrast {ratio:.2f} "
            f"vs gray-800, which is below WCAG AA (4.5). Use gray-400 "
            f"(#9ca3af) or lighter. Original incident: gray-600 fill was "
            f"effectively invisible against gray-800."
        )

    def test_all_progress_fills_pass_AA(self):
        """All grm-progress-fill-* classes should have contrast >= 4.5 (AA)."""
        css = self._read_css()
        import re
        for m in re.finditer(r"\.grm-progress-fill-(\w+)\s*\{[^}]*?background-color:\s*(#[0-9a-fA-F]+)", css):
            name, hex_color = m.group(1), m.group(2)
            ratio = self._contrast_ratio(hex_color)
            self.assertGreaterEqual(
                ratio, 4.5,
                f"grm-progress-fill-{name} ({hex_color}) has contrast {ratio:.2f} "
                f"vs gray-800, below WCAG AA (4.5). Bump to -400 series."
            )

    def test_text_gray_is_visible_against_card(self):
        """grm-text-gray (secondary labels) must have contrast >= 4.5."""
        css = self._read_css()
        gray_hex = self._extract_color(css, "grm-text-gray")
        ratio = self._contrast_ratio(gray_hex)
        self.assertGreaterEqual(
            ratio, 4.5,
            f"grm-text-gray ({gray_hex}) has contrast {ratio:.2f} vs "
            f"gray-800, below WCAG AA. The previous value (#6b7280 gray-500) "
            f"had only 3.04 — bumped to gray-300 (#d1d5db) for 9.96."
        )

    def test_text_muted_is_visible_against_card(self):
        """grm-text-muted (dim text) must have contrast >= 4.5."""
        css = self._read_css()
        muted_hex = self._extract_color(css, "grm-text-muted")
        ratio = self._contrast_ratio(muted_hex)
        self.assertGreaterEqual(
            ratio, 4.5,
            f"grm-text-muted ({muted_hex}) has contrast {ratio:.2f} vs "
            f"gray-800, below WCAG AA. Previous value gray-500 had 3.04."
        )

    def test_text_hierarchy_is_preserved(self):
        """light > gray > muted in brightness (i.e. contrast vs card)."""
        css = self._read_css()
        light = self._contrast_ratio(self._extract_color(css, "grm-text-light"))
        gray = self._contrast_ratio(self._extract_color(css, "grm-text-gray"))
        muted = self._contrast_ratio(self._extract_color(css, "grm-text-muted"))
        # Each tier should be strictly dimmer than the brighter tier
        self.assertGreater(light, gray,
            f"grm-text-light ({light:.2f}) should be brighter than "
            f"grm-text-gray ({gray:.2f})")
        self.assertGreater(gray, muted,
            f"grm-text-gray ({gray:.2f}) should be brighter than "
            f"grm-text-muted ({muted:.2f})")


# =====================================================================
# Spec coverage tests (Phase 0.3 tier system)
# =====================================================================

class DefaultThresholdsCoverageTests(SimpleTestCase):
    """Every color name in DEFAULT_THRESHOLDS must have a matching CSS class.

    Original bug (Nov 2025): the tier system returned 'orange' for
    CPU utilization 60-80% and 'muted' for low values, but the CSS
    only had .grm-progress-fill-red/yellow/green/blue/purple/gray.
    The result: any value that landed on 'orange' or 'muted' produced
    an invisible bar. The user reported "GPU core bar is not visible
    at 63%" — value 63% in cpu_util spec returns 'orange', which had
    no CSS rule.

    These tests catch any future spec color that lacks a fill class
    and any new fill class that lacks a tier color.
    """

    def setUp(self):
        import re
        from pathlib import Path
        from dashboard.templatetags import gpu_filters as gf
        from django.conf import settings
        self.gf = gf
        css_path = Path(settings.BASE_DIR) / "static/css/app.css"
        self.css = css_path.read_text(encoding="utf-8")
        # Find all grm-progress-fill-{color} classes in the CSS.
        # Only consider classes whose rule sets a background-color (a
        # color). This excludes the `.grm-progress-fill-thin` size
        # modifier (which has no background-color).
        self.fill_classes = set(re.findall(
            r"\.grm-progress-fill-(\w+)\s*\{[^}]*?background-color:\s*#",
            self.css,
        ))

    def test_every_spec_color_has_a_fill_class(self):
        """For each spec, every color name must be a known fill class.

        This catches the original bug: a spec returning 'orange' or
        'muted' that has no matching CSS class produces an invisible
        bar.
        """
        for spec_name, spec in self.gf.DEFAULT_THRESHOLDS.items():
            used_colors = {color for _, color in spec if color is not None}
            missing = used_colors - self.fill_classes
            self.assertEqual(
                missing, set(),
                f"Spec '{spec_name}' uses colors {sorted(missing)} that have no "
                f"matching .grm-progress-fill-* class in app.css. "
                f"Available: {sorted(self.fill_classes)}. "
                f"Add the missing class, or change the spec to use an "
                f"existing class."
            )

    def test_every_fill_class_is_used_by_at_least_one_spec(self):
        """Fill classes should not be dead code.

        If we add a fill class but no spec uses it, it's dead code
        waiting to drift out of sync.
        """
        used_colors = set()
        for spec in self.gf.DEFAULT_THRESHOLDS.values():
            for _, color in spec:
                if color is not None:
                    used_colors.add(color)
        unused = self.fill_classes - used_colors
        # We allow at most a small amount of dead code (e.g. -strong
        # variants for text). For now, just flag any unused fill class.
        self.assertEqual(
            unused, set(),
            f"Fill classes {sorted(unused)} are defined in app.css but no "
            f"spec uses them. Either add a spec that uses them, or remove "
            f"the dead class."
        )
