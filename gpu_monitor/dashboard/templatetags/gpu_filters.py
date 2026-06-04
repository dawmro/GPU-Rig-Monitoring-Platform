from django import template
import re
from django.utils import timezone
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
