from django import template
import re
from django.utils import timezone
from django.utils.safestring import mark_safe
from datetime import timedelta

register = template.Library()


@register.filter
def gpu_model_name(value):
    """Clean up GPU model name for display.

    Strips common vendor prefixes and shows the meaningful model number.
    Examples:
        'NVIDIA GeForce RTX 3060' -> 'RTX 3060'
        'NVIDIA GeForce RTX 4090 Ti' -> 'RTX 4090 Ti'
        'AMD Radeon RX 7900 XTX' -> 'RX 7900 XTX'
        'Intel Arc A770' -> 'Arc A770'
        'NVIDIA A100-SXM4-40GB' -> 'A100-SXM4-40GB'
    """
    if not value:
        return value

    # Common vendor prefixes to strip
    prefixes = [
        r'NVIDIA\s+GeForce\s+',
        r'NVIDIA\s+',
        r'AMD\s+Radeon\s+',
        r'AMD\s+',
        r'Intel\s+Arc\s+',
        r'Intel\s+',
    ]

    result = value.strip()
    for prefix in prefixes:
        result = re.sub(prefix, '', result, flags=re.IGNORECASE)
        if result != value.strip():
            break  # Stop after first match

    return result.strip() or value


@register.filter
def gpu_model_short(value):
    """Extract just the GPU model number for compact display.

    Strips vendor prefixes (NVIDIA, AMD, Intel) and model prefixes (RTX, GTX, RX).
    Examples:
        'NVIDIA GeForce RTX 3060' -> '3060'
        'NVIDIA GeForce RTX 4090 Ti' -> '4090'
        'AMD Radeon RX 7900 XTX' -> '7900'
        'NVIDIA A100-SXM4-40GB' -> 'A100'
    """
    if not value:
        return value

    # Try to extract model number pattern (e.g., RTX 3060, RX 7900, Arc A770)
    match = re.search(r'(?:RTX|GTX|RX|Titan|V100|H100)\s*(\d{3,4})', value, re.IGNORECASE)
    if match:
        return match.group(1)
    # Handle Arc models: "Arc A770" -> "770"
    match = re.search(r'Arc\s+[A-Z]?(\d{3,4})', value, re.IGNORECASE)
    if match:
        return match.group(1)
    # Handle letter-prefix models like A100, H100, B100
    match = re.search(r'\b([A-Z])(\d{3,4})\b', value, re.IGNORECASE)
    if match and match.group(0).lower() not in ('rtx', 'gtx', 'rx', 'arc', 'titan'):
        return match.group(0).upper()

    # Fallback: strip vendor prefixes and return cleaned name
    cleaned = gpu_model_name(value)
    # Try to extract any remaining number
    num_match = re.search(r'(\d{3,4})', cleaned)
    if num_match:
        return num_match.group(1)

    return cleaned


@register.filter
def gpu_compact_summary_json(snapshot):
    """Build compact GPU model summary from LatestSnapshot JSON fields.

    Works with the denormalized GPU data stored in LatestSnapshot
    instead of querying GPUMetric timeseries table.

    Examples:
        8x same model          -> "3060×8"
        4x same + 4x other     -> "5080×4 + ..."
        single card            -> "3060"
        no GPUs                -> "—"
    """
    if not snapshot or not snapshot.gpu_count:
        return "—"

    from collections import OrderedDict
    model_counts = OrderedDict()
    for model in snapshot.gpu_models_json:
        short = gpu_model_short(model) if model else "?"
        model_counts[short] = model_counts.get(short, 0) + 1

    sorted_models = sorted(model_counts.items(), key=lambda x: x[1], reverse=True)

    if len(sorted_models) == 1:
        model, count = sorted_models[0]
        return f"{model}×{count}" if count > 1 else model

    top_model, top_count = sorted_models[0]
    if top_count > 1:
        return f"{top_model}×{top_count} + ..."
    return f"{top_model} + ..."


@register.simple_tag
def gpu_temp_cell_json(snapshot):
    """Render color-coded GPU temperature values from LatestSnapshot JSON.

    Each value gets the grm-text-{color} class from static/css/app.css.
    The bare color name (red/orange/yellow/green/gray) is the filter output.
    Thresholds are centralized in DEFAULT_THRESHOLDS["gpu_temp"].
    """
    return _render_tier_cell(snapshot, "gpu_temps_json", "gpu_temp", "grm-text-gray")


@register.simple_tag
def gpu_util_cell_json(snapshot):
    """Render color-coded GPU utilization values from LatestSnapshot JSON.

    Thresholds from DEFAULT_THRESHOLDS["gpu_util"] (inverted: high = good).
    """
    return _render_tier_cell(snapshot, "gpu_utils_json", "gpu_util", "grm-text-muted")


@register.simple_tag
def gpu_fan_cell_json(snapshot):
    """Render color-coded GPU fan speed values from LatestSnapshot JSON.

    Thresholds from DEFAULT_THRESHOLDS["gpu_fan"].
    """
    return _render_tier_cell(snapshot, "gpu_fans_json", "gpu_fan", "grm-text-gray")


def _render_tier_cell(snapshot, json_field, threshold_name, no_data_class):
    """Helper: render a multi-GPU cell with tier-based coloring.

    Used by the gpu_*_cell_json simple_tags. Each value in the JSON
    array becomes a <span class="grm-text-{color}"> value </span>.
    Missing values get the no_data_class (e.g. grm-text-gray).

    Args:
        snapshot: LatestSnapshot instance.
        json_field: Name of the JSON list field (e.g. 'gpu_temps_json').
        threshold_name: Key in DEFAULT_THRESHOLDS to look up.
        no_data_class: Full CSS class for missing values.
    """
    thresholds = DEFAULT_THRESHOLDS[threshold_name]
    values = getattr(snapshot, json_field, None) if snapshot else None
    if not values:
        return mark_safe(f'<span class="{no_data_class}">—</span>')

    parts = []
    for value in values:
        if value is None:
            parts.append(f'<span class="{no_data_class}">—</span>')
        else:
            color = _resolve_color(value, thresholds)
            if not color:
                # Threshold returned None (e.g. "no color override" spec)
                # Render as plain text, no color class
                parts.append(f'<span>{_format_one(value)}</span>')
            else:
                parts.append(f'<span class="grm-text-{color}">{_format_one(value)}</span>')
    return mark_safe(' '.join(parts))


def _format_one(value):
    """Format a single value for the multi-GPU cell.

    The gpu_*_cell_json simple_tags historically rendered values as
    ".0f" (no decimals). Keep that behavior for consistency.
    """
    try:
        return f"{float(value):.0f}"
    except (ValueError, TypeError):
        return "—"


@register.filter
def time_since(seconds):
    """Convert seconds to human-readable uptime string.

    Examples:
        3600 -> '1h 0m'
        86400 -> '1d 0h'
        1778196 -> '20d 15h 39m'
        0 -> '0s'
        None -> '—'
    """
    if seconds is None:
        return '—'
    try:
        seconds = int(seconds)
    except (ValueError, TypeError):
        return '—'
    if seconds <= 0:
        return '0s'
    td = timedelta(seconds=seconds)
    days = td.days
    hours, remainder = divmod(td.seconds, 3600)
    minutes, _ = divmod(remainder, 60)
    parts = []
    if days:
        parts.append(f'{days}d')
    if hours:
        parts.append(f'{hours}h')
    if minutes and not days:
        parts.append(f'{minutes}m')
    if not parts:
        parts.append('0s')
    return ' '.join(parts)


@register.filter
def last_seen_short(value):
    """Format a datetime as a short relative time string.

    Requires: {% load gpu_filters %} in the template.

    For anything >= 7 days, shows total days only (e.g. '400d') to keep
    the fleet table compact. For recent times, shows mixed units.
    For sub-minute times, shows seconds (e.g., '20s').

    NOTE: Do NOT append ' ago' after this filter in error sections — the
    output is already a relative time string. For fleet table, ' ago' is OK.

    Examples:
        '1 year, 1 month' -> '400d'
        '3 months, 1 week' -> '97d'
        '2 weeks' -> '14d'
        '1 day, 3 hours' -> '1d, 3h'
        '2 hours, 15 minutes' -> '2h, 15m'
        '45 minutes' -> '45m'
        '20 seconds' -> '20s'
        '0 seconds' -> '0s'
    """
    if not value:
        return 'Never'
    from django.utils.timesince import timesince
    from datetime import datetime, timezone
    try:
        now = datetime.now(timezone.utc)
        if value.tzinfo is None:
            value = value.replace(tzinfo=timezone.utc)
        diff_s = int((now - value).total_seconds())
    except Exception:
        return '—'
    
    # Sub-minute: show seconds
    if diff_s < 60:
        return f'{diff_s}s'
    
    # For old rigs (contains year/month/week), show total days only
    if diff_s >= 7 * 86400:
        total_days = diff_s // 86400
        return f'{total_days}d'
    
    # Medium duration: use timesince and shorten
    try:
        ts = timesince(value)
    except Exception:
        return '—'
    
    # Shorten unit names (no space between number and unit)
    replacements = [
        ('days', 'd'),
        ('day', 'd'),
        ('hours', 'h'),
        ('hour', 'h'),
        ('minutes', 'm'),
        ('minute', 'm'),
    ]
    for full, short in replacements:
        ts = ts.replace(full, short)
    # Remove space between number and unit
    import re
    ts = re.sub(r'(\d)\s+([dhm])', r'\1\2', ts)
    return ts


@register.filter
def format_iops(value):
    """Format IOPS value with k/M suffix for readability."""
    if value is None:
        return '—'
    try:
        value = int(value)
    except (ValueError, TypeError):
        return '—'
    if value >= 1_000_000:
        return f'{value / 1_000_000:.1f}M'
    elif value >= 1_000:
        return f'{value / 1_000:.1f}k'
    return str(value)


@register.filter
def format_throughput_mb(value):
    """Format bytes/s value as MB/s with 1 decimal."""
    if value is None:
        return '—'
    try:
        return f'{float(value) / (1024 * 1024):.1f}'
    except (ValueError, TypeError):
        return '—'


@register.filter
def max_disk_util(values):
    """Return the maximum utilization value from a list of disk utilization percentages.

    Used in fleet overview to show the highest disk utilization across all disks.
    Returns 0 if the list is empty or contains only None values.
    Example: [45.2, None, 12.1] -> 45.2
    """
    if not values:
        return 0
    try:
        valid = [float(v) for v in values if v is not None]
        return max(valid) if valid else 0
    except (ValueError, TypeError):
        return 0


@register.filter
def format_bytes_total(value):
    """Format cumulative bytes as human-readable size (GB/TB).

    Examples:
        37688539648 -> '35.1 GB'
        1614605331456 -> '1.5 TB'
        None -> '—'
    """
    if value is None:
        return '—'
    try:
        value = float(value)
    except (ValueError, TypeError):
        return '—'
    if value >= 1_000_000_000_000:
        return f'{value / 1_000_000_000_000:.1f} TB'
    elif value >= 1_000_000_000:
        return f'{value / 1_000_000_000:.1f} GB'
    elif value >= 1_000_000:
        return f'{value / 1_000_000:.1f} MB'
    elif value >= 1_000:
        return f'{value / 1_000:.1f} KB'
    return f'{value:.0f} B'


@register.filter
def multiply(value, arg):
    """Multiply value by arg. Usage: {{ value|multiply:arg }}"""
    try:
        return float(value) * float(arg)
    except (ValueError, TypeError):
        return 0


@register.filter
def divide(value, arg):
    """Divide value by arg. Usage: {{ value|divide:arg }}"""
    try:
        divisor = float(arg)
        if divisor == 0:
            return 0
        return float(value) / divisor
    except (ValueError, TypeError):
        return 0


@register.filter
def filter_running(containers):
    """Return only running containers from a list of container dicts.
    
    Usage: {% for c in docker_metrics|filter_running %}
    """
    if not containers:
        return []
    return [c for c in containers if c.get('status') == 'running']


@register.filter
def cpu_util_color(value):
    """Return bare color name (e.g. 'red', 'yellow') for CPU utilization percentage.

    Used as a class suffix: grm-text-{{ x|cpu_util_color }}
    See static/css/app.css for the actual color values.

    Thresholds (matching Disk Util and Live Metrics progress bar):
        > 80%  -> red
        > 60%  -> orange
        > 40%  -> yellow
        > 20%  -> green
        <= 20% -> gray

    Usage: <span class="grm-text-{{ cpu_util|cpu_util_color }}">{{ cpu_util }}</span>
    """
    if value is None:
        return 'gray'
    try:
        v = float(value)
    except (ValueError, TypeError):
        return 'gray'
    if v > 80:
        return 'red'
    elif v > 60:
        return 'orange'
    elif v > 40:
        return 'yellow'
    elif v > 20:
        return 'green'
    return 'gray'


@register.filter
def trim(value):
    """Strip leading and trailing whitespace from a string.

    Usage: {% if line|trim %}...{% endif %}
    """
    if value is None:
        return ''
    return value.strip()


# =====================================================================
# 5-tier color threshold system (Phase 0.3)
# =====================================================================
#
# The fleet table, live metrics cards, and chart legends all need to
# color a value (CPU temp, GPU temp, disk util, etc.) based on thresholds.
# Before this module, each threshold chain was inlined in templates:
#
#   <span class="{% if x > 85 %}grm-text-red{% elif x > 70 %}grm-text-yellow
#                {% else %}grm-text-green{% endif %}">
#
# That code:
#   - was repeated 14+ times across templates and 3 times in Python
#     (the gpu_*_cell_json simple_tags)
#   - was inconsistently written: some used `> X`, some `>= X`, some
#     chained 3 thresholds, some 5, some mixed text-red-400 with
#     grm-text-red within the same file
#   - made it impossible to retune the system-wide "what counts as
#     red" without editing 14 different places
#
# The color_tier / color_tier_thresholds system centralizes this:
#
#   {% color_tier_thresholds "cpu_temp" as ct %}
#   <span class="grm-text-{{ snapshot.cpu_temp_c|tier:ct }}">
#
# Each threshold spec is a list of (min_value, color_name) pairs,
# highest first. The first match wins. If no threshold matches, the
# "default" color is used.
#
# Threshold specs can be defined:
#   1. In a context variable (via the simple_tag below)
#   2. Inline in a template literal (the `tier` filter accepts a string)
#   3. As a constant below (DEFAULT_THRESHOLDS dict)
#
# Keeping threshold definitions in context variables lets multiple
# tables on the same page share one spec, lets tests inspect them, and
# lets ops retune them without touching templates.

# Built-in threshold specs. Keys are spec names; values are the list
# of (min_value, color_name) tuples evaluated in order, highest first.
# Add new specs here when you need a new metric; do NOT inline the
# thresholds in templates.
DEFAULT_THRESHOLDS = {
    # CPU temperature (Celsius) — same in fleet table and live metrics
    "cpu_temp": [
        (85, "red"),
        (70, "yellow"),
        (None, "green"),  # default for values below the lowest threshold
    ],
    # GPU temperature (Celsius) — slightly tighter thresholds than CPU
    "gpu_temp": [
        (80, "red"),
        (70, "yellow"),
        (None, "green"),
    ],
    # CPU utilization (%) — 5-tier matching disk util
    "cpu_util": [
        (80, "red"),
        (60, "orange"),
        (40, "yellow"),
        (20, "green"),
        (None, "gray"),
    ],
    # GPU utilization (%) — inverted (high = good for miners)
    "gpu_util": [
        (90, "green"),
        (50, "gray"),
        (None, "muted"),
    ],
    # GPU fan speed (%)
    "gpu_fan": [
        (80, "red"),
        (60, "yellow"),
        (None, "gray"),
    ],
    # Memory usage (%)
    "mem_pct": [
        (85, "red"),
        (70, "yellow"),
        (None, "blue"),
    ],
    # Storage usage (%)
    "storage_pct": [
        (90, "red"),
        (75, "yellow"),
        (None, "blue"),
    ],
    # Disk utilization (%) — 5-tier like cpu_util
    "disk_util": [
        (80, "red"),
        (60, "orange"),
        (40, "yellow"),
        (20, "green"),
        (None, "muted"),
    ],
    # Top-process CPU % (in process list — lower thresholds since each
    # process can use a lot)
    "process_cpu": [
        (50, "red"),
        (20, "yellow"),
        (None, None),  # no color (default text)
    ],
    # Top-process memory %
    "process_mem": [
        (10, "red"),
        (5, "yellow"),
        (None, None),
    ],
}


@register.simple_tag
def color_tier_thresholds(name, varname=None):
    """Define a named threshold spec as a context variable.

    Usage:
        {% color_tier_thresholds "cpu_temp" as cpu_temp_thresholds %}
        <span class="grm-text-{{ val|tier:cpu_temp_thresholds }}">

    Or, to set in the context and not assign to a variable:
        {% color_tier_thresholds "cpu_temp" %}

    The `name` must be a key in DEFAULT_THRESHOLDS. Custom specs can
    be added by extending DEFAULT_THRESHOLDS (in a settings module or
    in a tests file).

    Returns the spec list so the simple_tag form can be assigned to
    a variable via `as`.
    """
    if name not in DEFAULT_THRESHOLDS:
        raise KeyError(
            f"Unknown threshold spec '{name}'. Known specs: "
            f"{', '.join(sorted(DEFAULT_THRESHOLDS.keys()))}. "
            f"Add the spec to DEFAULT_THRESHOLDS in "
            f"dashboard/templatetags/gpu_filters.py."
        )
    return DEFAULT_THRESHOLDS[name]


def _resolve_color(value, thresholds):
    """Apply a threshold spec to a numeric value.

    `thresholds` is a list of (min_value, color_name) pairs, highest
    first. The first pair where `min_value` is None OR `value > min_value`
    determines the returned color. If `color_name` is None, the function
    returns None (signaling "no color override").

    Returns None if the value can't be coerced to a number (NaN-like
    inputs). The caller decides what to do with None — typically
    substitute a default color like 'gray' or 'muted'.
    """
    if thresholds is None:
        return None
    if value is None:
        return None
    try:
        v = float(value)
    except (ValueError, TypeError):
        return None
    for min_value, color in thresholds:
        if min_value is None:
            # Default color (must be last entry)
            return color
        if v > min_value:
            return color
    # If we get here, all thresholds had a non-None min_value but none
    # matched (shouldn't happen if DEFAULT_THRESHOLDS is well-formed).
    # Return the last color as a safe fallback.
    return thresholds[-1][1]


@register.filter(name="tier")
def tier(value, thresholds):
    """Return the bare color name for `value` based on `thresholds`.

    Use this when you want to compose the class yourself:
        <span class="grm-text-{{ x|tier:cpu_temp_thresholds }}">

    The `thresholds` argument is a list of (min_value, color_name)
    pairs, highest first. Usually created by the `color_tier_thresholds`
    simple_tag from DEFAULT_THRESHOLDS. Can also be passed inline as
    a list literal (rare).

    Returns the color name string ('red', 'yellow', etc.) or None if
    the value can't be coerced. None for "no color override" is
    returned when the spec explicitly has a (None, None) entry.
    """
    return _resolve_color(value, thresholds)


@register.filter(name="tier_text")
def tier_text(value, thresholds):
    """Convenience filter: return the full grm-text-{color} class.

    Use this when you don't need to compose the class with others:
        <span class="{{ x|tier_text:cpu_temp_thresholds }}">

    Returns 'grm-text-{color}' for matching thresholds, or the empty
    string '' if the spec says "no color override" (e.g. low process
    CPU usage). Returns '' on bad input too (no class = no style).
    """
    color = _resolve_color(value, thresholds)
    if not color:
        return ""
    return f"grm-text-{color}"


@register.filter(name="tier_fill")
def tier_fill(value, thresholds):
    """Convenience filter: return the full grm-progress-fill-{color} class.

    Use this for progress bar fills:
        <div class="grm-progress">
          <div class="{{ x|tier_fill:cpu_util_thresholds }}"
               style="width: {{ x }}%"></div>
        </div>

    Returns 'grm-progress-fill-{color}' for matching thresholds, or the
    empty string '' for "no color override". Note: progress bar fills
    typically have a default color (e.g. 'gray' for "no data"), so the
    spec should not return None for default unless the caller wants an
    invisible fill.
    """
    color = _resolve_color(value, thresholds)
    if not color:
        return ""
    return f"grm-progress-fill-{color}"
