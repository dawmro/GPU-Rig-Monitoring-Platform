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
    """Render color-coded GPU temperature values from LatestSnapshot JSON."""
    if not snapshot or not snapshot.gpu_temps_json:
        return mark_safe('<span class="text-gray-600">—</span>')

    parts = []
    for temp in snapshot.gpu_temps_json:
        if temp is None:
            parts.append('<span class="text-gray-600">—</span>')
        else:
            try:
                t = float(temp)
            except (ValueError, TypeError):
                parts.append('<span class="text-gray-600">—</span>')
                continue
            if t > 80:
                parts.append(f'<span class="text-red-400 font-medium">{t:.0f}</span>')
            elif t > 75:
                parts.append(f'<span class="text-orange-400 font-medium">{t:.0f}</span>')
            elif t > 70:
                parts.append(f'<span class="text-yellow-400">{t:.0f}</span>')
            elif t > 65:
                parts.append(f'<span class="text-green-400">{t:.0f}</span>')
            else:
                parts.append(f'<span class="text-gray-400">{t:.0f}</span>')
    return mark_safe(' '.join(parts))


@register.simple_tag
def gpu_util_cell_json(snapshot):
    """Render color-coded GPU utilization values from LatestSnapshot JSON."""
    if not snapshot or not snapshot.gpu_utils_json:
        return mark_safe('<span class="text-gray-600">—</span>')

    parts = []
    for util in snapshot.gpu_utils_json:
        if util is None:
            parts.append('<span class="text-gray-600">—</span>')
        else:
            try:
                u = float(util)
            except (ValueError, TypeError):
                parts.append('<span class="text-gray-600">—</span>')
                continue
            if u > 90:
                parts.append(f'<span class="text-green-400 font-medium">{u:.0f}</span>')
            elif u > 50:
                parts.append(f'<span class="text-gray-300">{u:.0f}</span>')
            else:
                parts.append(f'<span class="text-gray-500">{u:.0f}</span>')
    return mark_safe(' '.join(parts))


@register.simple_tag
def gpu_fan_cell_json(snapshot):
    """Render color-coded GPU fan speed values from LatestSnapshot JSON."""
    if not snapshot or not snapshot.gpu_fans_json:
        return mark_safe('<span class="text-gray-600">—</span>')

    parts = []
    for fan in snapshot.gpu_fans_json:
        if fan is None:
            parts.append('<span class="text-gray-600">—</span>')
        else:
            try:
                f = float(fan)
            except (ValueError, TypeError):
                parts.append('<span class="text-gray-600">—</span>')
                continue
            if f > 80:
                parts.append(f'<span class="text-red-400 font-medium">{f:.0f}</span>')
            elif f > 60:
                parts.append(f'<span class="text-yellow-400">{f:.0f}</span>')
            else:
                parts.append(f'<span class="text-gray-400">{f:.0f}</span>')
    return mark_safe(' '.join(parts))


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
