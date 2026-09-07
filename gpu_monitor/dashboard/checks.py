"""
Django system checks for the dashboard app.

The deploy-bug prevention check (templates reference only existing CSS classes)
is critical infrastructure added in 2026-09 after a Phase 0.1 commit added
app.css but collectstatic was skipped on prod, causing 404s and missing
styles in production. See docs/DEPLOY.md for the full incident.

This module is registered via dashboard/apps.py. Run with:
    python manage.py check
    python manage.py check --deploy
    python manage.py check --tag grm_css

Disable the check in a specific environment with:
    python manage.py check --skip-checks grm_css
"""
from __future__ import annotations

import os
import re
from collections import defaultdict
from pathlib import Path
from typing import Iterable

from django.conf import settings
from django.core.checks import Error, Warning, register


# Tag for selectively running this check family
TAG_GRM_CSS = "grm_css"


# Path to the app's static dir (relative to settings.BASE_DIR)
# BASE_DIR is the project root (gpu_monitor/), so static files live at
# BASE_DIR/static/css/ (NOT BASE_DIR/gpu_monitor/static/).
APP_STATIC_CSS = "static/css"
APP_TEMPLATES = "templates"

# Class prefix this check validates. Tailwind classes (text-gray-400 etc.)
# are NOT validated because they're provided by the CDN at runtime.
VALIDATED_PREFIX = "grm-"

# Regex matching a class="..." attribute. Allows single or double quotes,
# and matches anything that's not the closing quote. Non-greedy.
#
# Catches both:
#   <input class="grm-input" ...>
#   <button class="grm-btn-primary px-3 py-1.5">...</button>
#   <div class="{% if x %}grm-card{% else %}grm-card-lg{% endif %}">...</div>
#   <span class="grm-text-{{ color }}">...</span>
#
# Skips:
#   <!-- class="grm-input" -->           (HTML comments, not attributes)
#   <a class="don't match" href="...">  (single token in quote, but no class=)
#   <input class='grm-input' ...>        (single-quoted — also matched, but rare)
RE_CLASS_ATTR = re.compile(
    r"""class=["']([^"']+)["']""",
    re.IGNORECASE,
)

# Strip Django template tags and filters to reduce false positives.
# These constructs are not literal class names; they are evaluated at
# render time to produce class names. We tokenize around them.
#
# Examples that should be treated as "variable" placeholders (skipped):
#   {{ x }}                  -> variable
#   {{ x|tier_text:spec }}   -> variable with filter (the FILTER itself
#                               may produce a class name; but we can't
#                               statically know which color it returns.
#                               Conservatively, treat whole thing as placeholder.)
#   {% if foo %}grm-card{% else %}grm-card-lg{% endif %}
#                            -> either branch is valid
RE_DJANGO_VAR = re.compile(r"\{\{[^}]*\}\}")
RE_DJANGO_TAG = re.compile(r"\{%[^%]*%\}")

# When a grm- prefix is immediately followed by a Django variable like
# `grm-text-{{ color }}`, the literal "grm-text-" would otherwise be
# tokenized as a class and reported as undefined. Strip the whole
# construction. The resulting class at render time is the value of
# {{ color }} prefixed with "grm-text-" — we can't validate that
# statically, but we can avoid the false positive on the prefix itself.
#
# Examples:
#   grm-text-{{ color }}                  -> skipped
#   grm-text-{{ x|tier_text:spec }}       -> skipped
#   grm-text-{{ color }}-suffix           -> skipped
#   grm-text-{{ color }}suffix            -> suffix token reported (grm-? no, plain "suffix")
#   grm-card (no variable)                -> not matched, normal token
RE_GRM_VAR = re.compile(r"\bgrm-[a-z]+-(?=\{\{)")

# Build a regex to extract CSS class selectors from a CSS file.
# Matches `.classname {` or `.classname,` or `.classname.other {` etc.
# We are intentionally lenient — anything that LOOKS like a class selector
# in our CSS file is accepted.
RE_CSS_SELECTOR = re.compile(r"\.([A-Za-z_][A-Za-z0-9_-]*)")


def _all_class_tokens(template_text: str) -> Iterable[tuple[str, str]]:
    """Yield (class_token, source_location) for every grm-* class reference in template text.

    Strips Django template syntax before tokenizing to avoid false positives
    on `{{ x }}` placeholders and `{% if %}` branches.
    """
    for m in RE_CLASS_ATTR.finditer(template_text):
        value = m.group(1)
        # Strip Django template tags/vars so they don't pollute the token list.
        # We do NOT try to evaluate them — that's the job of the template
        # engine. We just want to ensure any *literal* class in the value
        # is defined in CSS.
        #
        # Order matters:
        #   1. RE_GRM_VAR runs FIRST so it can find "grm-text-{{" while
        #      the {{ is still present. Otherwise the {{ would be replaced
        #      with a space by step 2, and the lookahead would fail.
        #   2. RE_DJANGO_VAR removes the {{ ... }} placeholders.
        #   3. RE_DJANGO_TAG removes {% ... %} tag blocks.
        cleaned = RE_GRM_VAR.sub("", value)
        cleaned = RE_DJANGO_VAR.sub(" ", cleaned)
        cleaned = RE_DJANGO_TAG.sub(" ", cleaned)
        for token in cleaned.split():
            if token.startswith(VALIDATED_PREFIX):
                # Source location is the 1-based character offset of m.start()
                # in the template file. Adequate for error messages.
                yield token, f"char {m.start()}"


def _defined_css_classes(css_path: Path) -> set[str]:
    """Parse app.css and return the set of grm-* class names defined as selectors."""
    try:
        text = css_path.read_text(encoding="utf-8")
    except FileNotFoundError:
        # If the CSS file doesn't exist, the check will fail at collectstatic
        # time too. Don't double-report; return empty set so other checks fire.
        return set()
    return {
        name
        for name in RE_CSS_SELECTOR.findall(text)
        if name.startswith(VALIDATED_PREFIX)
    }


def _all_template_files() -> list[Path]:
    """Return all .html files under gpu_monitor/templates/."""
    templates_dir = Path(settings.BASE_DIR) / APP_TEMPLATES
    if not templates_dir.is_dir():
        return []
    return sorted(templates_dir.rglob("*.html"))


def _css_path() -> Path:
    return Path(settings.BASE_DIR) / APP_STATIC_CSS / "app.css"


@register(TAG_GRM_CSS)
def check_grm_css_classes(app_configs, **kwargs) -> list:
    """Verify every grm-* class referenced in templates is defined in app.css.

    Walks every template under gpu_monitor/templates/, extracts grm-* class
    tokens from class="..." attributes, and checks each one against the
    set of class selectors defined in gpu_monitor/static/css/app.css.

    Reports a Warning (not Error) for each undefined class. A Warning
    rather than Error because:
      - A missing class doesn't crash the app, it just makes that element
        unstyled (the bug we're trying to prevent)
      - We want CI to surface the issue without blocking unrelated deploys
      - The actual production breakage is a 404 on the CSS file itself,
        which is a deploy step, not a code issue

    Returns:
        List of django.core.checks.Warning instances.
    """
    css_path = _css_path()
    defined = _defined_css_classes(css_path)

    # Group undefined classes by template file for cleaner error output.
    # value -> [(template, location), ...]
    missing_by_file: dict[Path, list[tuple[str, str]]] = defaultdict(list)
    for template_path in _all_template_files():
        try:
            text = template_path.read_text(encoding="utf-8")
        except (FileNotFoundError, UnicodeDecodeError):
            continue
        for token, location in _all_class_tokens(text):
            if token not in defined:
                missing_by_file[template_path].append((token, location))

    warnings = []
    for template_path, missing in sorted(missing_by_file.items()):
        # Group by class name within a file to make the message readable.
        by_class: dict[str, list[str]] = defaultdict(list)
        for token, location in missing:
            by_class[token].append(location)

        # Cap the per-file report to avoid log floods. Show first 5 classes
        # per file, with the total count.
        sample = sorted(by_class.items())[:5]
        sample_str = ", ".join(f"{cls} (at {', '.join(locs[:2])}{'...' if len(locs) > 2 else ''})"
                               for cls, locs in sample)
        more = len(by_class) - len(sample)
        more_str = f" (+{more} more)" if more > 0 else ""

        try:
            rel_path = template_path.relative_to(settings.BASE_DIR)
        except ValueError:
            rel_path = template_path

        warnings.append(
            Warning(
                f"grm-* class referenced in template but not defined in app.css: "
                f"{sample_str}{more_str}",
                hint=(
                    "Add the class to static/css/app.css, or fix the typo in "
                    "the template. Common cause: CSS class was renamed/removed "
                    "but the template was not updated. After fixing templates/CSS, "
                    "run `python manage.py collectstatic --noinput --clear` to "
                    "publish the new CSS to /opt/gpu_monitor/staticfiles/."
                ),
                id="dashboard.W001",
                obj=str(rel_path),
            )
        )
    return warnings


@register(TAG_GRM_CSS)
def check_static_css_file_exists(app_configs, **kwargs) -> list:
    """Verify gpu_monitor/static/css/app.css exists.

    This is the file nginx tries to serve at /static/css/app.css. If it's
    missing, every element styled by a grm-* class loses its formatting
    (404 on the CSS file). A separate deploy-step check (collectstatic)
    ensures the file ends up in /opt/gpu_monitor/staticfiles/, but we
    also want to catch the case where the SOURCE file is missing —
    which would break local dev (runserver) too.
    """
    css_path = _css_path()
    if css_path.is_file():
        return []

    try:
        rel_path = css_path.relative_to(settings.BASE_DIR)
    except ValueError:
        rel_path = css_path

    return [
        Error(
            f"Required CSS file is missing: {rel_path}. "
            f"This file is loaded by base.html and styles every grm-* class. "
            f"Without it, the entire UI loses its formatting (404 on the "
            f"stylesheet, default unstyled white-on-white text).",
            hint=(
                "Restore the file from git, or create it as a stub: "
                "touch static/css/app.css && git add static/css/app.css"
            ),
            id="dashboard.E001",
            obj=str(rel_path),
        )
    ]


@register(TAG_GRM_CSS)
def check_collectstatic_freshness(app_configs, **kwargs) -> list:
    """
    Verify /opt/gpu_monitor/staticfiles/ matches gpu_monitor/static/ on production.

    This check only runs in production (DEBUG=False), since local dev doesn't
    use collectstatic — runserver serves static files directly from the source.

    The check compares the mtime of /opt/gpu_monitor/staticfiles/css/app.css
    (collected copy) against /opt/gpu_monitor/static/css/app.css (source).
    If the source is newer than the collected copy, the deployed CSS is stale
    and the next browser request for /static/css/app.css may return stale CSS
    (or a 404 if the file was added since last collectstatic).

    This is a WARNING (not Error) because:
      - The fix is one command: collectstatic
      - We don't want to block production startup if collectstatic itself
        is failing for some other reason
    """
    if settings.DEBUG:
        return []

    opt_staticfiles = Path("/opt/gpu_monitor/staticfiles/css/app.css")
    opt_source = Path("/opt/gpu_monitor/static/css/app.css")

    # If either path is missing, the other checks will catch it. Don't
    # double-report here.
    if not opt_staticfiles.is_file() or not opt_source.is_file():
        return []

    try:
        if opt_source.stat().st_mtime <= opt_staticfiles.stat().st_mtime:
            return []
    except OSError:
        return []

    return [
        Warning(
            "Source /opt/gpu_monitor/static/css/app.css is newer than "
            "/opt/gpu_monitor/staticfiles/css/app.css. The deployed CSS "
            "is stale. Browsers may receive outdated styles (or 404 on "
            "newly-added files).",
            hint=(
                "Run: cd /opt/gpu_monitor && source venv/bin/activate && "
                "python manage.py collectstatic --noinput --clear. "
                "Or run scripts/sync_to_opt.sh which does this automatically."
            ),
            id="dashboard.W002",
        )
    ]


@register(TAG_GRM_CSS)
def check_no_multiline_template_comments(app_configs, **kwargs) -> list:
    """Verify all Django template comments {# ... #} are single-line.

    Django's template comment syntax {# ... #} is documented to be
    single-line. In practice it tolerates newlines inside a single
    block, but mixing a multi-line comment with surrounding template
    tags is fragile and not portable. The Django docs say:

        "{% comment %}{% endcomment %}"  # multi-line official form
        "{# #}"                           # single-line inline form

    Anything that needs more than one line should use {% comment %}.

    This check catches the pattern of a single {# ... #} that spans
    multiple lines and warns. We don't make it an Error because the
    template still works at runtime, but it's a code-smell that the
    team should fix in follow-up commits.

    Implementation note: this is the regex I tried first; turns out
    Django's parser is permissive enough that the original code
    worked. But the next person to edit those comments might
    accidentally break the template, so we surface the issue here
    rather than waiting for it to break.
    """
    import re

    multiline_pattern = re.compile(r"\{#(.*?)#\}", re.DOTALL)
    warnings = []
    for template_path in _all_template_files():
        try:
            text = template_path.read_text(encoding="utf-8")
        except (FileNotFoundError, UnicodeDecodeError):
            continue
        for m in multiline_pattern.finditer(text):
            body = m.group(1)
            if "\n" not in body:
                continue
            line_no = text[: m.start()].count("\n") + 1
            try:
                rel_path = template_path.relative_to(settings.BASE_DIR)
            except ValueError:
                rel_path = template_path
            warnings.append(
                Warning(
                    f"Multi-line {{# ... #}} comment in {rel_path}:{line_no}. "
                    f"Django template comments should be single-line. "
                    f"Split into multiple single-line {{# #}} comments, or "
                    f"convert to {{% comment %}} ... {{% endcomment %}}.",
                    hint=(
                        "Each line of the comment should be its own "
                        "{# ... #} block. Example:\n"
                        "  {# Line 1 #}\n"
                        "  {# Line 2 #}\n"
                        "  {# Line 3 #}"
                    ),
                    id="dashboard.W004",
                    obj=str(rel_path),
                )
            )
    return warnings


# =====================================================================
# Template class name regression guard (Phase 0.5)
# =====================================================================

# Class names that were REMOVED in Phase 0.5 simplification.
# Templates should use Tailwind utilities (bg-X-400, text-X-400, etc.)
# directly instead of these custom classes.
REMOVED_GRM_CLASSES = {
    # Text color utilities (replaced by text-X-400 / text-X-300)
    "grm-text-red", "grm-text-orange", "grm-text-yellow", "grm-text-green",
    "grm-text-blue", "grm-text-purple", "grm-text-cyan", "grm-text-gray",
    "grm-text-muted", "grm-text-light",
    "grm-text-red-faded", "grm-text-red-light", "grm-text-yellow-light",
    "grm-text-green-light", "grm-text-blue-light",
    "grm-text-red-strong", "grm-text-yellow-strong", "grm-text-green-strong",
    "grm-text-blue-strong", "grm-text-purple-strong", "grm-text-teal-strong",
    "grm-text-orange-strong",
    # Progress fill utilities (replaced by bg-X-400)
    "grm-progress-fill-red", "grm-progress-fill-orange",
    "grm-progress-fill-yellow", "grm-progress-fill-green",
    "grm-progress-fill-blue", "grm-progress-fill-purple",
    "grm-progress-fill-gray", "grm-progress-fill-muted",
}


@register(TAG_GRM_CSS)
def check_no_removed_grm_class_names(app_configs, **kwargs) -> list:
    """Verify templates don't use the class names removed in Phase 0.5.

    In Phase 0.5 we deleted the .grm-text-{color} and .grm-progress-fill-
    {color} CSS classes (they were just Tailwind reimplementations).
    Templates should use Tailwind utilities directly: bg-X-400 for fills,
    text-X-400 for normal text, text-X-300 for "strong" text.

    This check catches any template that still references the old
    classes — those would silently render as unstyled (no Tailwind
    class by that name exists, and our app.css no longer defines
    .grm-text-X / .grm-progress-fill-X).
    """
    import re
    warnings = []
    # Match class="..." or class='...' with one of the removed names.
    # We tokenize on whitespace and look for exact matches.
    pattern = re.compile(r'class=["\']([^"\']+)["\']')
    for template_path in _all_template_files():
        try:
            text = template_path.read_text(encoding="utf-8")
        except (FileNotFoundError, UnicodeDecodeError):
            continue
        for m in pattern.finditer(text):
            classes = m.group(1).split()
            removed_used = [c for c in classes if c in REMOVED_GRM_CLASSES]
            if not removed_used:
                continue
            try:
                rel_path = template_path.relative_to(settings.BASE_DIR)
            except ValueError:
                rel_path = template_path
            line_no = text[: m.start()].count("\n") + 1
            for removed_class in removed_used:
                warnings.append(
                    Warning(
                        f"Template uses removed class '{removed_class}' "
                        f"at {rel_path}:{line_no}. This class was deleted "
                        f"in Phase 0.5 (it was a duplicate of a Tailwind "
                        f"utility). Replace with the Tailwind class: "
                        f"'bg-{removed_class.removeprefix('grm-text-')}-400' "
                        f"for fills, "
                        f"'text-{removed_class.removeprefix('grm-text-').removesuffix('-strong').removesuffix('-light')}-400' "
                        f"for text.",
                        hint=(
                            f"Use 'text-X-400' (or 'text-X-300' for "
                            f"strong/light variants) for text colors, "
                            f"'bg-X-400' for progress fills. See "
                            f"docs/LAYOUT_OPTIMIZATION_PLAN.md for the "
                            f"color mapping."
                        ),
                        id="dashboard.W005",
                        obj=str(rel_path),
                    )
                )
    return warnings


# =====================================================================
# Static JS file checks (Phase 0.4)
# =====================================================================

# Tag for selectively running the JS check family
TAG_GRM_JS = "grm_js"

# Required JS files. Every file in this dict MUST be loaded by either
# base.html (all pages) or rig_detail.html (rig detail only). The
# check verifies the source file exists, the template references it,
# and (in production) the file appears in the collected staticfiles.
#
# The order in this dict matters for documentation only — the check
# does not depend on it.
REQUIRED_JS_FILES = {
    "app-base.js": {
        "loaded_by": "base.html",
        "purpose": "Page-wide utilities: clocks, mobile menu, email toggle",
        "is_error": True,  # app-base is required for the page to work
    },
    "chart-base.js": {
        "loaded_by": "rig_detail.html",
        "purpose": "Shared Chart.js options (scales, ticks, tooltip)",
        "is_error": True,
    },
    "chart-colors.js": {
        "loaded_by": "rig_detail.html",
        "purpose": "Centralized chart color palettes",
        "is_error": True,
    },
    "chart-loaders.js": {
        "loaded_by": "rig_detail.html",
        "purpose": "Chart data loaders (loadChart, loadChartMultiGpu, etc.)",
        "is_error": True,
    },
    "chart-runtime.js": {
        "loaded_by": "rig_detail.html",
        "purpose": "Chart loader orchestrator and global state",
        "is_error": True,
    },
    "rig-detail.js": {
        "loaded_by": "rig_detail.html",
        "purpose": "Rig detail page interactions (tabs, modal, rename)",
        "is_error": True,
    },
}


def _js_path(filename):
    return Path(settings.BASE_DIR) / "static" / "js" / filename


@register(TAG_GRM_JS)
def check_static_js_files_exist(app_configs, **kwargs) -> list:
    """Verify every required gpu_monitor/static/js/*.js file exists.

    Like the CSS check (E001), this catches the case where the
    source file is missing. For JS files, a missing source file
    means the browser will get a 404 on /static/js/foo.js, which
    is even more catastrophic than a missing CSS — many page
    interactions (tabs, charts, modal) silently break.

    A separate deploy-step check (collectstatic) ensures the file
    ends up in /opt/gpu_monitor/staticfiles/, but we also want to
    catch the source missing case here.
    """
    errors = []
    for filename, info in REQUIRED_JS_FILES.items():
        js_path = _js_path(filename)
        if js_path.is_file():
            continue
        try:
            rel_path = js_path.relative_to(settings.BASE_DIR)
        except ValueError:
            rel_path = js_path
        errors.append(
            Error(
                f"Required JS file is missing: {rel_path}. "
                f"Loaded by {info['loaded_by']} for: {info['purpose']}. "
                f"Without it, the page silently breaks (no tab switching, "
                f"no charts, etc.).",
                hint=(
                    f"Restore the file from git, or create a stub: "
                    f"touch static/js/{filename} && git add static/js/{filename}"
                ),
                id="dashboard.E002",
                obj=str(rel_path),
            )
        )
    return errors


@register(TAG_GRM_JS)
def check_templates_reference_js_files(app_configs, **kwargs) -> list:
    """Verify base.html / rig_detail.html reference the JS files they should.

    Catches the case where someone refactors a template and accidentally
    drops a <script src="...js"></script> tag. The page would render
    but the JS wouldn't run, leading to silent breakage (tabs don't
    switch, charts don't load, etc.) that's hard to debug.
    """
    warnings = []
    base_path = Path(settings.BASE_DIR) / "templates" / "base.html"
    # rig_detail.html lives under templates/dashboard/, not templates/
    detail_path = Path(settings.BASE_DIR) / "templates" / "dashboard" / "rig_detail.html"

    template_files = {
        "base.html": base_path,
        "rig_detail.html": detail_path,
    }

    for filename, info in REQUIRED_JS_FILES.items():
        expected_loader = info["loaded_by"]
        template_path = template_files[expected_loader]
        if not template_path.is_file():
            # Template itself is missing — not our concern here
            # (covered by other Django checks).
            continue
        try:
            content = template_path.read_text(encoding="utf-8")
        except (FileNotFoundError, UnicodeDecodeError):
            continue
        # The reference is either a {% static 'js/...js' %} block or
        # a literal /static/js/...js URL.
        ref_static = f"{{% static 'js/{filename}' %}}"
        ref_literal = f"js/{filename}"
        if ref_static in content or ref_literal in content:
            continue
        try:
            rel_path = template_path.relative_to(settings.BASE_DIR)
        except ValueError:
            rel_path = template_path
        warnings.append(
            Warning(
                f"{filename} is expected to be loaded by {expected_loader} "
                f"but no reference was found in that template. The JS "
                f"will not run and the page will silently break.",
                hint=(
                    f"Add to {expected_loader}: "
                    f"<script src=\"{{% static 'js/{filename}' %}}\"></script>"
                ),
                id="dashboard.W003",
                obj=str(rel_path),
            )
        )
    return warnings
