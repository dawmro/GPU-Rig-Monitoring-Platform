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
def gpu_compact_summary(gpus):
    """Build a compact GPU model summary string for a list of GPUMetric objects.

    Groups by short model name and shows count.
    For mixed cards, shows only the most popular model + '...'.

    Examples:
        8x same model     -> "3060×8"
        4x same + others  -> "3060×4 + ..."
        all different     -> "3060 + ..."
        2 different       -> "4090, 3060"
    """
    if not gpus:
        return "—"

    # Build model -> count map
    from collections import OrderedDict
    model_counts = OrderedDict()
    for gpu in gpus:
        short = gpu_model_short(gpu.model) if gpu.model else "?"
        model_counts[short] = model_counts.get(short, 0) + 1

    # Sort by count descending (most popular first)
    sorted_models = sorted(model_counts.items(), key=lambda x: x[1], reverse=True)

    # If 3 or fewer unique models, show them all (no "...")
    if len(sorted_models) <= 3:
        parts = []
        for model, count in sorted_models:
            if count > 1:
                parts.append(f"{model}×{count}")
            else:
                parts.append(model)
        return ", ".join(parts)

    # More than 3 unique models: show only the most popular + "..."
    top_model, top_count = sorted_models[0]
    if top_count > 1:
        return f"{top_model}×{top_count} + ..."
    else:
        return f"{top_model} + ..."


@register.simple_tag
def gpu_temp_cell(temp_c):
    """Render a color-coded GPU temperature value."""
    if temp_c is None:
        return mark_safe('<span class="text-gray-600">—</span>')
    try:
        t = float(temp_c)
    except (ValueError, TypeError):
        return mark_safe('<span class="text-gray-600">—</span>')
    if t > 80:
        return mark_safe(f'<span class="text-red-400 font-medium">{t:.0f}</span>')
    elif t > 75:
        return mark_safe(f'<span class="text-orange-400 font-medium">{t:.0f}</span>')
    elif t > 70:
        return mark_safe(f'<span class="text-yellow-400">{t:.0f}</span>')
    elif t > 65:
        return mark_safe(f'<span class="text-green-400">{t:.0f}</span>')
    else:
        return mark_safe(f'<span class="text-gray-400">{t:.0f}</span>')


@register.simple_tag
def gpu_util_cell(util_pct):
    """Render a color-coded GPU utilization value."""
    if util_pct is None:
        return mark_safe('<span class="text-gray-600">—</span>')
    try:
        u = float(util_pct)
    except (ValueError, TypeError):
        return mark_safe('<span class="text-gray-600">—</span>')
    if u > 90:
        return mark_safe(f'<span class="text-green-400 font-medium">{u:.0f}</span>')
    elif u > 50:
        return mark_safe(f'<span class="text-gray-300">{u:.0f}</span>')
    else:
        return mark_safe(f'<span class="text-gray-500">{u:.0f}</span>')


@register.simple_tag
def gpu_fan_cell(fan_pct):
    """Render a color-coded GPU fan speed value."""
    if fan_pct is None:
        return mark_safe('<span class="text-gray-600">—</span>')
    try:
        f = float(fan_pct)
    except (ValueError, TypeError):
        return mark_safe('<span class="text-gray-600">—</span>')
    if f > 80:
        return mark_safe(f'<span class="text-red-400 font-medium">{f:.0f}</span>')
    elif f > 60:
        return mark_safe(f'<span class="text-yellow-400">{f:.0f}</span>')
    else:
        return mark_safe(f'<span class="text-gray-400">{f:.0f}</span>')


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

    NOTE: Do NOT append ' ago' after this filter in error sections — the
    output is already a relative time string. For fleet table, ' ago' is OK.

    Examples:
        '1 year, 1 month' -> '400d'
        '3 months, 1 week' -> '97d'
        '2 weeks' -> '14d'
        '1 day, 3 hours' -> '1d, 3h'
        '2 hours, 15 minutes' -> '2h, 15m'
        '45 minutes' -> '45m'
        '0 minutes' -> '0m'
    """
    if not value:
        return 'Never'
    from django.utils.timesince import timesince
    from datetime import datetime, timezone
    try:
        ts = timesince(value)
    except Exception:
        return '—'
    # For old rigs (contains year/month/week), show total days only
    if any(unit in ts for unit in ('year', 'month', 'week')):
        try:
            now = datetime.now(timezone.utc)
            if value.tzinfo is None:
                value = value.replace(tzinfo=timezone.utc)
            total_days = (now - value).days
            return f'{total_days}d'
        except Exception:
            pass
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
    # Handle "0 m" / "0 minutes" case
    if ts.strip() in ('0m', '0 m', '0 minutes'):
        return '0m'
    return ts
