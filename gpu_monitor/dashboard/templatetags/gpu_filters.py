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

    Examples:
        'NVIDIA GeForce RTX 3060' -> '3060'
        'NVIDIA GeForce RTX 4090 Ti' -> '4090'
        'AMD Radeon RX 7900 XTX' -> '7900'
    """
    if not value:
        return value

    # Try to extract model number pattern (e.g., RTX 3060, RX 7900, A100)
    match = re.search(r'(?:RTX|GTX|RX|Arc|A\d|Titan|V100|H100)\s*(\d{3,4})', value, re.IGNORECASE)
    if match:
        return match.group(0).strip()

    # Fallback: clean the full name
    return gpu_model_name(value)


@register.filter
def gpu_compact_summary(gpus):
    """Build a compact GPU model summary string for a list of GPUMetric objects.

    Groups by short model name and shows count.
    Examples:
        8x same model  -> "RTX 3060 ×8"
        2 different    -> "RTX 4090, RTX 3060"
        mixed counts   -> "RTX 3060 ×3, RTX 4090"
    """
    if not gpus:
        return "—"

    # Build model -> count map using short names
    from collections import OrderedDict
    model_counts = OrderedDict()
    for gpu in gpus:
        short = gpu_model_short(gpu.model) if gpu.model else "?"
        model_counts[short] = model_counts.get(short, 0) + 1

    parts = []
    for model, count in model_counts.items():
        if count > 1:
            parts.append(f"{model} ×{count}")
        else:
            parts.append(model)

    if len(parts) > 3:
        # Too many different models: truncate
        return ", ".join(parts[:3]) + "…"
    return ", ".join(parts)


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
