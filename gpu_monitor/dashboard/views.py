from django.shortcuts import render, get_object_or_404, redirect
from django.contrib.auth.decorators import login_required
from django.http import Http404, HttpResponse
from django.views.decorators.http import require_POST
from django.core.cache import cache
from django.utils import timezone
from datetime import timedelta
from functools import wraps
from statistics import pstdev, median
import math
import re
import time
from collections import Counter
from types import SimpleNamespace

from rigs.models import Rig, RigTag
from metrics_app.models import MetricSnapshot, LatestSnapshot, GPUMetric, StorageMetric, NetworkMetric, LatestDockerContainer
from audit.middleware import log_audit_event

# Pre-compiled regex for natural sort: splits "rig12" -> ["rig", 12, ""]
_NATURAL_SORT_RE = re.compile(r'(\d+)')

# Cache TTLs for Rig-related cached data (seconds).
# 30s for high-frequency polls (htmx_metrics, htmx_rig_status).
# After a rig's name/tags/owner changes, max 30s staleness.
# For permission-sensitive operations, we re-validate from DB.
_RIG_CACHE_TTL_S = 30

# Report operating threshold for GPU thermal exceedance statistics.
GPU_TEMPERATURE_THRESHOLD_C = 85.0
# Exclude long gaps from duration estimates and treat them as breaks between events.
GPU_TEMPERATURE_MAX_SAMPLE_GAP_S = 3600.0


def _get_rig_light_cached(uuid, user):
    """Get a minimal Rig representation for permission check + status display.

    Returns a SimpleNamespace with: uuid, owner_id, status, last_seen,
    error_history_json, container_history_json
    (or None if not found / not accessible to user).

    Used by high-frequency HTMX endpoints (htmx_metrics, htmx_rig_status)
    that poll every 15-30s. Avoids 1 DB query per poll.

    Full Rig object is NOT cached here because:
    - It's heavy (tags, owner, GPU arrays)
    - Permission check only needs owner_id
    - Status display only needs .status + .last_seen
    - For full data (rig_detail), use Rig.objects.get() directly

    Returns None if rig doesn't exist OR user doesn't have access.
    """
    cache_key = f'rig_light_{uuid}'
    cached = cache.get(cache_key)
    if cached is not None:
        rig = cached
    else:
        # Minimal DB query — only fields we need
        try:
            row = Rig.objects.only(
                'uuid', 'owner_id', 'status', 'last_seen',
                'error_history_json', 'container_history_json',
            ).get(uuid=uuid)
        except Rig.DoesNotExist:
            return None
        rig = SimpleNamespace(
            uuid=row.uuid,
            owner_id=row.owner_id,
            status=row.status,
            last_seen=row.last_seen,
            error_history_json=row.error_history_json,
            container_history_json=row.container_history_json,
        )
        cache.set(cache_key, rig, _RIG_CACHE_TTL_S)

    # Permission check (always re-validate from cached data, not DB)
    if rig.owner_id != user.id and not user.is_staff:
        return None
    return rig


def invalidate_rig_cache(uuid):
    """Invalidate cached Rig data. Call this when rig data changes
    (rename, ownership transfer, status change, tag changes, delete).

    Safe to call even if no cache entry exists.
    """
    cache.delete(f'rig_light_{uuid}')
    # Also invalidate the LatestSnapshot cache (in case it's affected)
    cache.delete(f'lsnap_{uuid}')


def index_view(request):
    """Root URL landing page.

    Authenticated users are redirected to the dashboard (rig list).
    Unauthenticated users are redirected to the login page.
    """
    if request.user.is_authenticated:
        return redirect('dashboard:rig-list')
    return redirect('accounts:login')


def rate_limit(max_requests, window_s):
    """Simple per-user/IP rate limit decorator for Django views.

    Args:
        max_requests: Maximum number of requests allowed in the window.
        window_s: Time window in seconds.
    """
    def decorator(view_func):
        @wraps(view_func)
        def wrapper(request, *args, **kwargs):
            # Use user ID for authenticated users, IP for anonymous
            if request.user.is_authenticated:
                key = f'rl_user_{request.user.id}'
            else:
                key = f'rl_ip_{request.META.get("REMOTE_ADDR", "unknown")}'

            now = time.time()
            window_start = now - window_s

            # Get request timestamps from cache
            timestamps = cache.get(key, [])
            # Remove timestamps outside the current window
            timestamps = [t for t in timestamps if t > window_start]

            if len(timestamps) >= max_requests:
                return HttpResponse(
                    'Rate limit exceeded. Please slow down.',
                    status=429,
                    content_type='text/plain'
                )

            timestamps.append(now)
            cache.set(key, timestamps, timeout=window_s)
            return view_func(request, *args, **kwargs)
        return wrapper
    return decorator


def _json_get(lst, idx, default=None):
    """Safely get an element from a JSON array field."""
    if lst and idx < len(lst):
        return lst[idx]
    return default


def _percentile(values, percentile):
    """Calculate a percentile using linear interpolation.

    This follows the default linear percentile convention used by NumPy:
    the fractional position is (n - 1) * percentile / 100.

    None, non-numeric values, NaN, and infinite values are ignored. Returns
    None when no valid observations are available.
    """
    if not 0 <= percentile <= 100:
        raise ValueError('percentile must be between 0 and 100')

    valid_values = []
    for value in values:
        if value is None:
            continue
        try:
            number = float(value)
        except (TypeError, ValueError, OverflowError):
            continue
        if not math.isfinite(number):
            continue
        valid_values.append(number)

    if not valid_values:
        return None

    valid_values.sort()
    if len(valid_values) == 1:
        return valid_values[0]

    position = (len(valid_values) - 1) * (percentile / 100.0)
    lower_index = int(position)
    upper_index = min(lower_index + 1, len(valid_values) - 1)
    fraction = position - lower_index

    return (
        valid_values[lower_index] * (1.0 - fraction)
        + valid_values[upper_index] * fraction
    )


def _percentile_stats(values, digits=2):
    """Return P50, P95, and P99 for a numeric sample series."""
    values = list(values)
    result = {}
    for percentile in (50, 95, 99):
        value = _percentile(values, percentile)
        result[f'p{percentile}'] = (
            round(value, digits) if value is not None else None
        )
    return result


def _robust_distribution_stats(values, digits=2, min_outlier_samples=5):
    """Return robust spread and outlier statistics for a numeric series.

    MAD is the median absolute deviation from the median. IQR is P75 - P25.
    When MAD is non-zero, candidate outliers use the modified Z-score
    threshold |0.6745 * (x - median) / MAD| > 3.5. If MAD is zero, Tukey's
    1.5*IQR fences are used; if both MAD and IQR are zero, values different
    from the median are candidates. Outlier rates are withheld until at least
    ``min_outlier_samples`` valid observations are available.

    Non-numeric, NULL, NaN, and infinite observations are ignored. These are
    statistical candidates only and do not imply a hardware fault.
    """
    valid_values = []
    for value in values:
        if value is None:
            continue
        try:
            number = float(value)
        except (TypeError, ValueError, OverflowError):
            continue
        if math.isfinite(number):
            valid_values.append(number)

    sample_count = len(valid_values)
    if not sample_count:
        return {
            'sample_count': 0,
            'median': None,
            'mad': None,
            'p25': None,
            'p75': None,
            'iqr': None,
            'outlier_count': None,
            'outlier_pct': None,
        }

    center = _percentile(valid_values, 50)
    abs_deviations = [abs(value - center) for value in valid_values]
    mad = _percentile(abs_deviations, 50)
    p25 = _percentile(valid_values, 25)
    p75 = _percentile(valid_values, 75)
    iqr = p75 - p25 if p25 is not None and p75 is not None else None

    outlier_count = None
    outlier_pct = None
    if sample_count >= min_outlier_samples:
        if mad is not None and mad > 0:
            outlier_count = sum(
                abs(0.6745 * (value - center) / mad) > 3.5
                for value in valid_values
            )
        elif iqr is not None and iqr > 0:
            lower_fence = p25 - 1.5 * iqr
            upper_fence = p75 + 1.5 * iqr
            outlier_count = sum(
                value < lower_fence or value > upper_fence
                for value in valid_values
            )
        else:
            # A zero MAD and zero IQR mean the central distribution is
            # constant; any different observation is a candidate outlier.
            outlier_count = sum(value != center for value in valid_values)
        outlier_pct = (outlier_count / sample_count) * 100.0

    return {
        'sample_count': sample_count,
        'median': round(center, digits) if center is not None else None,
        'mad': round(mad, digits) if mad is not None else None,
        'p25': round(p25, digits) if p25 is not None else None,
        'p75': round(p75, digits) if p75 is not None else None,
        'iqr': round(iqr, digits) if iqr is not None else None,
        'outlier_count': outlier_count,
        'outlier_pct': round(outlier_pct, 2) if outlier_pct is not None else None,
    }


def _telemetry_coverage_stats(rows, fields, expected_duration_seconds, max_gap_seconds=3600.0):
    """Summarize timestamp coverage and per-field data completeness.

    Time coverage is the sum of positive adjacent timestamp intervals no
    longer than max_gap_seconds, divided by the selected reporting-window
    duration. This intentionally does not extrapolate before the first sample,
    after the last sample, or across long gaps. Field completeness is the
    percentage of rows with a non-NULL value for each field; it is a sample
    completeness measure, not a time-weighted measure.
    """
    rows = list(rows)
    timestamps = sorted(
        row.get('timestamp') for row in rows if row.get('timestamp') is not None
    )
    covered_seconds = 0.0
    for previous, current in zip(timestamps, timestamps[1:]):
        gap = (current - previous).total_seconds()
        if 0 < gap <= max_gap_seconds:
            covered_seconds += gap

    duration = max(float(expected_duration_seconds or 0), 0.0)
    return {
        'sample_count': len(rows),
        'time_coverage_pct': (
            round(min(100.0, 100.0 * covered_seconds / duration), 2)
            if duration > 0 else None
        ),
        'first_sample_timestamp': timestamps[0] if timestamps else None,
        'last_sample_timestamp': timestamps[-1] if timestamps else None,
        **{
            f'{field}_completeness_pct': (
                round(100.0 * sum(row.get(field) is not None for row in rows) / len(rows), 2)
                if rows else None
            )
            for field in fields
        },
    }


def _build_gpu_title(values, default_value='N/A', suffix='', fmt=None):
    """Build a 'GPU1: X | GPU2: Y | ...' title string for tooltips.

    Used in _rig_table.html to pre-compute title attributes for multi-GPU
    cells. Without this, the template iterates the JSON list per row to
    build the title, causing O(rigs × gpus) Python iterations on every
    Fleet Overview page load.

    Args:
        values: List of values (e.g. ['RTX 4090', 'A100', None])
                or None if rig has no GPUs
        default_value: Value to use when entry is None (e.g. 'Unknown', 'N/A')
        suffix: String to append to formatted value (e.g. '°C', '%', ' MHz')
        fmt: Optional format spec (e.g. '.1f' for 1 decimal place)

    Returns:
        Formatted title string like 'GPU1: RTX 4090 | GPU2: A100 | GPU3: N/A'
        or empty string if values is None/empty.
    """
    if not values:
        return ''

    parts = []
    for i, v in enumerate(values, 1):
        if v is None:
            value_str = default_value
        elif fmt:
            value_str = f'{v:{fmt}}{suffix}'
        else:
            value_str = f'{v}{suffix}'
        parts.append(f'GPU{i}: {value_str}')

    return ' | '.join(parts)


# =====================================================================
# Phase 2.4: Per-device metric builders (extracted from _fetch_rig_metrics)
# =====================================================================

def _build_gpu_metrics(snapshot):
    """Build GPU metrics list from LatestSnapshot JSON arrays.
    
    Args:
        snapshot: LatestSnapshot instance (or None)
    
    Returns:
        List of dicts with keys matching the template's expected format
        (gpu_index, gpu_uuid, model, gpu_temp_c, gpu_util_pct, etc.)
    """
    if not snapshot or not snapshot.gpu_count:
        return []
    
    metrics = []
    for i in range(snapshot.gpu_count):
        metrics.append({
            'gpu_index': i,
            'gpu_uuid': _json_get(snapshot.gpu_uuids_json, i, ''),
            'model': _json_get(snapshot.gpu_models_json, i, ''),
            # Static GPU identifiers (AIB board part number + subvendor)
            'gpu_board_part_number': _json_get(snapshot.gpu_board_part_numbers_json, i, ''),
            'gpu_subvendor': _json_get(snapshot.gpu_subvendors_json, i, ''),
             # Phase 2 static GPU identifiers
             'gpu_vbios': _json_get(snapshot.gpu_vbios_json, i, ''),
             'pci_bus_id': _json_get(snapshot.gpu_pci_bus_ids_json, i, ''),
             'gpu_architecture': _json_get(snapshot.gpu_architecture_json, i, ''),
             'gpu_bus_type': _json_get(snapshot.gpu_bus_type_json, i, ''),
             'gpu_board_id': _json_get(snapshot.gpu_board_ids_json, i),
             'gpu_serial': _json_get(snapshot.gpu_serials_json, i, ''),
             'gpu_pci_subsystem': _json_get(snapshot.gpu_pci_subsystems_json, i, ''),
             'gpu_inforom': _json_get(snapshot.gpu_inforom_json, i),
            # Phase 3 performance / thermal / topology
            'gpu_thermal_thresholds': _json_get(snapshot.gpu_thermal_thresholds_json, i),
            'gpu_pstates_util':       _json_get(snapshot.gpu_pstates_util_json, i),
            'gpu_max_clocks':         _json_get(snapshot.gpu_max_clocks_json, i),
            'gpu_mig_mode':           _json_get(snapshot.gpu_mig_modes_json, i),
            'gpu_bar1_mb':            _json_get(snapshot.gpu_bar1_mb_json, i),
            'gpu_temp_c': _json_get(snapshot.gpu_temps_json, i),
            'gpu_util_pct': _json_get(snapshot.gpu_utils_json, i),
            'fan_speed_pct': _json_get(snapshot.gpu_fans_json, i),
            'gpu_core_clock_mhz': _json_get(snapshot.gpu_core_clocks_json, i),
            'gpu_mem_clock_mhz': _json_get(snapshot.gpu_mem_clocks_json, i),
            'mem_used_mb': _json_get(snapshot.gpu_mem_used_json, i),
            'mem_total_mb': _json_get(snapshot.gpu_mem_total_json, i),
            'mem_util_pct': _json_get(snapshot.gpu_mem_util_pcts_json, i),
            'mem_controller_util_pct': _json_get(snapshot.gpu_mem_controller_utils_json, i),
            'mem_free_mb': _json_get(snapshot.gpu_mem_free_json, i),
            'power_draw_w': _json_get(snapshot.gpu_power_draws_json, i),
            'power_limit_w': _json_get(snapshot.gpu_power_limits_json, i),
            'pcie_current_gen': _json_get(snapshot.gpu_pcie_gen_json, i),
            'pcie_max_gen': _json_get(snapshot.gpu_pcie_max_gen_json, i),
            'pcie_current_width': _json_get(snapshot.gpu_pcie_width_json, i),
            'pcie_max_width': _json_get(snapshot.gpu_pcie_max_width_json, i),
        })
    return metrics


def _disk_model_label(vendor, model):
    """Build a 'vendor model' display label without duplicating the
    vendor when the model string already contains it.
    'Samsung' + 'SSD 870 EVO 1TB'      -> 'Samsung SSD 870 EVO 1TB'
    'Samsung' + 'Samsung SSD 870 ...'  -> 'Samsung SSD 870 ...'  (dedup)
    'Western Digital' + 'WD Blue ...'  -> 'Western Digital WD Blue ...' (kept: vendor prefix not in model)
    """
    vendor = (vendor or '').strip()
    model = (model or '').strip()
    if not vendor and not model:
        return ''
    if not vendor:
        return model
    if not model:
        return vendor
    if model.lower().startswith(vendor.lower()):
        return model
    return f'{vendor} {model}'


def _build_storage_metrics(snapshot):
    """Build storage metrics list from LatestSnapshot JSON arrays."""
    if not snapshot or not snapshot.storage_count:
        return []
    
    metrics = []
    for i in range(snapshot.storage_count):
        metrics.append({
            'device': _json_get(snapshot.storage_devices_json, i, ''),
            'fstype': _json_get(snapshot.storage_fstypes_json, i, ''),
            'mountpoint': _json_get(snapshot.storage_mountpoints_json, i, ''),
            'capacity_bytes': _json_get(snapshot.storage_capacities_json, i),
            'usage_pct': _json_get(snapshot.storage_usage_pcts_json, i),
            'temp_c': _json_get(snapshot.storage_temps_json, i),
            'smart_health': _json_get(snapshot.storage_smart_json, i, ''),
            # Static hardware identifiers (sysfs) — '' when unavailable
            'model': _json_get(snapshot.storage_models_json, i, ''),
            'vendor': _json_get(snapshot.storage_vendors_json, i, ''),
            'serial': _json_get(snapshot.storage_serials_json, i, ''),
            'wwn': _json_get(snapshot.storage_wwns_json, i, ''),
            # Pre-joined 'vendor model' label (Django templates can't
            # inline a Python ternary inside a {{ }} output tag).
            # sysfs 'model' often already starts with the vendor name
            # (e.g. 'Samsung SSD 870 EVO 1TB') — avoid 'Samsung Samsung …'.
            'model_label': _disk_model_label(
                _json_get(snapshot.storage_vendors_json, i, ''),
                _json_get(snapshot.storage_models_json, i, ''),
            ),
            # Disk I/O metrics — deltas (since last sample) and cumulative totals (since boot)
            'read_bytes_delta': _json_get(snapshot.storage_read_bytes_delta_json, i),
            'write_bytes_delta': _json_get(snapshot.storage_write_bytes_delta_json, i),
            'read_iops_delta': _json_get(snapshot.storage_read_iops_delta_json, i),
            'write_iops_delta': _json_get(snapshot.storage_write_iops_delta_json, i),
            'utilization_pct': _json_get(snapshot.storage_utilization_pcts_json, i),
            'read_bytes_total': _json_get(snapshot.storage_read_bytes_total_json, i),
            'write_bytes_total': _json_get(snapshot.storage_write_bytes_total_json, i),
            'read_iops_total': _json_get(snapshot.storage_read_iops_total_json, i),
            'write_iops_total': _json_get(snapshot.storage_write_iops_total_json, i),
        })
    return metrics


def _build_network_metrics(snapshot):
    """Build network metrics list from LatestSnapshot JSON arrays."""
    if not snapshot or not snapshot.network_count:
        return []
    
    metrics = []
    for i in range(snapshot.network_count):
        metrics.append({
            'interface': _json_get(snapshot.network_interfaces_json, i, ''),
            'ipv4': _json_get(snapshot.network_ipv4s_json, i, ''),
            'link_speed_mbps': _json_get(snapshot.network_speeds_json, i),
            'rx_bytes': _json_get(snapshot.network_rx_bytes_json, i),
            'tx_bytes': _json_get(snapshot.network_tx_bytes_json, i),
            'rx_errors': _json_get(snapshot.network_rx_errors_json, i, 0),
            'tx_errors': _json_get(snapshot.network_tx_errors_json, i, 0),
        })
    return metrics


def _build_docker_metrics(uuid):
    """Fetch docker container metrics via ORM (no snapshot needed)."""
    if not LatestDockerContainer.objects.filter(rig_uuid=str(uuid)).exists():
        return []
    
    metrics = []
    # Deduplicate by container_id at query level (defense-in-depth)
    latest_containers = (
        LatestDockerContainer.objects
        .filter(rig_uuid=str(uuid))
        .order_by('container_id')
        .distinct('container_id')
    )
    for lc in latest_containers:
        metrics.append({
            'container_id': lc.container_id,
            'name': lc.name,
            'image': lc.image,
            'status': lc.status,
            'created': lc.created,
            'status_text': lc.status_text,
            'manifest': lc.manifest_json,
            'logs': lc.logs_json,
        })
    return metrics


def _build_process_details(snapshot):
    """Build process details from top CPU + top memory processes."""
    if not snapshot:
        return []
    
    seen_pids = set()
    details = []
    for proc in ((snapshot.top_cpu_processes_json or [])[:10] +
                 (snapshot.top_mem_processes_json or [])[:10]):
        pid = proc.get('pid')
        if pid in seen_pids:
            continue
        if not proc.get('cmdline'):
            continue  # omit kernel/system pseudo-processes without a command line
        seen_pids.add(pid)
        details.append({
            'pid': pid,
            'name': proc.get('name') or '—',
            'cmdline': proc.get('cmdline'),
            'cpu_pct': proc.get('cpu_pct') or 0.0,
            'mem_pct': proc.get('mem_pct') or 0.0,
        })
    details.sort(key=lambda p: (-p['cpu_pct'], -p['mem_pct']))
    return details


def _derive_primary_ip(network_metrics):
    """Find the first non-loopback, non-virtual interface IP."""
    if not network_metrics:
        return ''
    
    virtual_prefixes = ('vmware', 'virtual', 'vbox', 'hyper-v',
                        'docker', 'tun', 'tap', 'br-', 'veth')
    
    for iface in network_metrics:
        ip = iface.get('ipv4', '')
        if not ip or ip == '—' or ip.startswith('127.'):
            continue
        name = iface.get('interface', '').lower()
        if any(prefix in name for prefix in virtual_prefixes):
            continue
        return ip
    
    # Fallback: first non-loopback
    for iface in network_metrics:
        ip = iface.get('ipv4', '')
        if ip and ip != '—' and not ip.startswith('127.'):
            return ip
    return ''


def _fetch_rig_metrics(uuid, rig=None):
    """Fetch the latest rig metrics for Live Metrics display.

    Uses SQL-level latest-per-device queries instead of fetching all rows.
    """
    # LatestSnapshot changes only on heartbeat (~60s), but is polled every 30s.
    # Cache with 50s TTL to reduce DB reads between heartbeats.
    snapshot = None
    if rig is not None:
        cache_key = f'lsnap_{rig.uuid}'
        snapshot = cache.get(cache_key)
        if snapshot is None:
            try:
                snapshot = LatestSnapshot.objects.get(rig_uuid=str(uuid))
            except LatestSnapshot.DoesNotExist:
                pass
            else:
                cache.set(cache_key, snapshot, 50)
    gpu_metrics = _build_gpu_metrics(snapshot)
    storage_metrics = _build_storage_metrics(snapshot)
    network_metrics = _build_network_metrics(snapshot)
    docker_metrics = _build_docker_metrics(uuid)
    process_details = _build_process_details(snapshot)
    primary_ip = _derive_primary_ip(network_metrics)

    # Derived fields
    error_history = rig.error_history_json if rig else []
    recent_errors = list(reversed(error_history[-10:])) if error_history else []
    container_history = rig.container_history_json if rig else []
    gpu_processes = snapshot.gpu_processes_json if snapshot else []

    return {
        'snapshot': snapshot,
        'gpu_metrics': gpu_metrics,
        'storage_metrics': storage_metrics,
        'network_metrics': network_metrics,
        'docker_metrics': docker_metrics,
        'process_details': process_details,
        'recent_errors': recent_errors,
        'error_history': error_history,
        'container_history': container_history,
        'primary_ip': primary_ip,
        'top_cpu_processes': snapshot.top_cpu_processes_json if snapshot else [],
        'top_mem_processes': snapshot.top_mem_processes_json if snapshot else [],
        'process_count': snapshot.process_count if snapshot else 0,
        'gpu_processes': gpu_processes,
    }


@login_required
@rate_limit(max_requests=60, window_s=60)
def rig_list(request):
    """Fleet Overview page.

    Refreshes every 30s via HTMX (see rig_list.html).
    All table columns are rendered from ``rig_data`` passed to
    ``_rig_table.html``.  To add a new column:

    1. Add the column header to ``_rig_table.html`` <thead>.
    2. Fetch the data here in the ``rig_data`` loop (or annotate the queryset).
    3. Extend the ``rig_data.append({...})`` dict with the new key.
    4. Add the <td> cell in ``_rig_table.html`` <tbody> using the new key.

    Query strategy:
    - 1 query: Rig base queryset (with prefetched tags + owner)
    - 1 query: LatestSnapshot batch fetch for all rig UUIDs
    - Counts derived in Python (no extra queries) via Counter
    - Total: 2 queries regardless of how many rigs
    """
    user = request.user

    # Step 1: Load ALL rigs (no status/search/tag filter) for status counts.
    # This avoids re-querying with .values_list().annotate(Count) which
    # was an extra DB roundtrip.
    if user.is_staff:
        all_rigs = Rig.objects.all().prefetch_related('tags', 'owner')
    else:
        all_rigs = Rig.objects.filter(owner=user).prefetch_related('tags', 'owner')

    # Step 2: Compute status counts from already-loaded data.
    # Counter is O(N) and avoids an extra .values_list('status').annotate() query.
    status_counts = dict(Counter(r.status for r in all_rigs))
    online_count = status_counts.get('online', 0)
    stale_count = status_counts.get('stale', 0)
    offline_count = status_counts.get('offline', 0)
    total_count = online_count + stale_count + offline_count

    # Step 3: Apply user filters to derive the displayed queryset.
    # We use the already-loaded all_rigs list for natural sort (Python-side)
    # since prefetched relations + Python sort is faster than re-fetching
    # ordered with .order_by() on the queryset.
    rigs = all_rigs

    status_filter = request.GET.get('status', '')
    if status_filter:
        rigs = [r for r in rigs if r.status == status_filter]

    search = request.GET.get('search', '')
    if search:
        rigs = [r for r in rigs if search.lower() in (r.name or '').lower()]

    tag_filter = request.GET.get('tag', '')
    if tag_filter:
        # Build set of rig UUIDs that have the requested tag (1 query)
        # instead of per-rig tags.filter().exists() which is N+1.
        rigs_with_tag = set(
            RigTag.objects.filter(name=tag_filter).values_list('rigs__uuid', flat=True)
        )
        rigs = [r for r in rigs if str(r.uuid) in rigs_with_tag]

    # Sort rigs naturally by name (e.g., rig2 before rig11).
    # Use pre-compiled _NATURAL_SORT_RE for efficiency.
    def _natural_sort_key(value):
        """Split string into text/number chunks for human-friendly sorting."""
        return [
            int(chunk) if chunk.isdigit() else chunk.lower()
            for chunk in _NATURAL_SORT_RE.split(value or '')
        ]
    rigs = sorted(rigs, key=lambda r: _natural_sort_key(r.name))

    # Batch-fetch all LatestSnapshot rows in ONE query (avoids N+1)
    rig_uuids = [str(r.uuid) for r in rigs]
    latest_snapshots = {
        str(s.rig_uuid): s  # Use str key to match rig_uuid_str lookups
        for s in LatestSnapshot.objects.filter(rig_uuid__in=rig_uuids)
    }

    # Build rig_data using snapshot data (no GPUMetric queries needed).
    # Pre-compute title strings for multi-GPU cells (4 cells per row × N rigs).
    # Without this, the template iterates the JSON list 4× per row to build
    # title attributes, causing O(rigs × gpus) Python iterations on every page load.
    rig_data = []
    for rig in rigs:
        rig_uuid_str = str(rig.uuid)
        snapshot = latest_snapshots.get(rig_uuid_str)
        rig_data.append({
            'rig': rig,
            'snapshot': snapshot,
            'gpu_models_title': _build_gpu_title(snapshot.gpu_models_json if snapshot else None,
                                                 default_value='Unknown', suffix=''),
            'gpu_temps_title': _build_gpu_title(snapshot.gpu_temps_json if snapshot else None,
                                                default_value='N/A', suffix='°C', fmt='.1f'),
            'gpu_fans_title': _build_gpu_title(snapshot.gpu_fans_json if snapshot else None,
                                               default_value='N/A', suffix='%', fmt='.0f'),
            'gpu_utils_title': _build_gpu_title(snapshot.gpu_utils_json if snapshot else None,
                                                default_value='N/A', suffix='%', fmt='.1f'),
        })

    if request.headers.get('HX-Request'):
        return render(request, 'dashboard/_rig_table.html', {'rig_data': rig_data})

    all_tags = RigTag.objects.filter(user=user).order_by('name') if not user.is_staff else RigTag.objects.all().order_by('name')

    return render(request, 'dashboard/rig_list.html', {
        'rig_data': rig_data,
        'status_filter': status_filter,
        'search': search,
        'tag_filter': tag_filter,
        'all_tags': all_tags,
        'online_count': online_count,
        'stale_count': stale_count,
        'offline_count': offline_count,
        'total_count': total_count,
    })


@login_required
def rig_toggle_tag(request, uuid, tag_id):
    """Toggle a tag on/off for a rig."""
    if request.method == 'POST':
        rig = get_object_or_404(Rig, uuid=uuid)
        if rig.owner_id != request.user.id and not request.user.is_staff:
            raise Http404
        tag = get_object_or_404(RigTag, id=tag_id, user=request.user)
        if tag in rig.tags.all():
            rig.tags.remove(tag)
            action = 'tag.removed'
        else:
            rig.tags.add(tag)
            action = 'tag.added'
        log_audit_event(request, action, 'Rig', rig.uuid, {'tag': tag.name})
        # Invalidate cached rig data (tags changed; status badge still valid
        # but the cached rig data may be stale for htmx_metrics in 30s)
        invalidate_rig_cache(uuid)
        if request.headers.get('HX-Request'):
            return render(request, 'dashboard/_rig_tags.html', {'rig': rig})
    return redirect('dashboard:rig-detail', uuid=uuid)


@login_required
@rate_limit(max_requests=60, window_s=60)
def rig_detail(request, uuid):
    """Rig detail page."""
    rig = get_object_or_404(Rig, uuid=uuid)
    if rig.owner_id != request.user.id and not request.user.is_staff:
        raise Http404

    context = _fetch_rig_metrics(uuid, rig)
    context['rig'] = rig
    context['is_data_stale'] = rig.status in [Rig.Status.OFFLINE, Rig.Status.STALE]

    return render(request, 'dashboard/rig_detail.html', context)


@login_required
@rate_limit(max_requests=120, window_s=60)
def htmx_metrics(request, uuid):
    """HTMX polling endpoint for live metrics.

    Polled every ~30s by the rig detail page. Uses cached Rig lookup to
    avoid a DB query per poll (was: 1 query every 30s per rig = 200 queries/min
    for 100 rigs). The 30s cache TTL matches typical rig data change frequency.
    """
    rig = _get_rig_light_cached(uuid, request.user)
    if rig is None:
        raise Http404

    # _fetch_rig_metrics accepts a rig argument. Our SimpleNamespace is compatible
    # with the duck-typed access (rig.uuid, rig.status, etc.).
    context = _fetch_rig_metrics(uuid, rig)
    # Template (_metrics_cards.html) only needs snapshot + is_data_stale,
    # but we pass rig for consistency with the rig_detail view.
    context['rig'] = rig
    context['is_data_stale'] = rig.status in [Rig.Status.OFFLINE, Rig.Status.STALE]

    return render(request, 'dashboard/_metrics_cards.html', context)


@login_required
@rate_limit(max_requests=120, window_s=60)
def htmx_rig_status(request, uuid):
    """HTMX polling endpoint — returns just the status badge + last_seen.

    Polled every ~15s for the Fleet Overview status badges. Uses cached
    Rig lookup (was: 4 queries/min × 100 rigs = 400 queries/min).
    """
    rig = _get_rig_light_cached(uuid, request.user)
    if rig is None:
        raise Http404

    # rig already has uuid, owner_id, status, last_seen from cache
    return render(request, 'dashboard/_rig_status_badge.html', {'rig': rig})


@login_required
@require_POST
def rig_delete(request, uuid):
    """Delete a rig and all its associated data."""
    rig = get_object_or_404(Rig, uuid=uuid)
    if rig.owner_id != request.user.id and not request.user.is_staff:
        raise Http404

    rig_name = rig.name

    # Delete all associated metric data (MetricSnapshot has rig_uuid as UUIDField, not FK)
    from metrics_app.models import MetricSnapshot, LatestSnapshot, GPUMetric, \
        StorageMetric, NetworkMetric, LatestDockerContainer, RigStatusEvent
    MetricSnapshot.objects.filter(rig_uuid=uuid).delete()
    LatestSnapshot.objects.filter(rig_uuid=uuid).delete()
    GPUMetric.objects.filter(rig_uuid=uuid).delete()
    StorageMetric.objects.filter(rig_uuid=uuid).delete()
    NetworkMetric.objects.filter(rig_uuid=uuid).delete()
    LatestDockerContainer.objects.filter(rig_uuid=uuid).delete()
    RigStatusEvent.objects.filter(rig_uuid=uuid).delete()

    rig.delete()
    # Invalidate all cached data for this rig (rig deleted, no longer exists)
    invalidate_rig_cache(uuid)
    log_audit_event(request, 'rig.deleted', 'Rig', uuid, {'name': rig_name})

    if request.headers.get('HX-Request'):
        response = render(request, 'dashboard/_rig_deleted_notice.html', {'rig_name': rig_name})
        response['HX-Redirect'] = '/dashboard/rigs/'
        return response

    return redirect('dashboard:rig-list')


@login_required
@require_POST
def rig_rename(request, uuid):
    """Rename a rig. Accepts both form POST and HTMX POST."""
    rig = get_object_or_404(Rig, uuid=uuid)
    if rig.owner_id != request.user.id and not request.user.is_staff:
        raise Http404

    new_name = request.POST.get('name', '').strip()
    if new_name:
        old_name = rig.name
        rig.name = new_name[:128]
        rig.save(update_fields=['name'])
        log_audit_event(request, 'rig.renamed', 'Rig', rig.uuid, {
            'old_name': old_name,
            'new_name': rig.name,
        })
        # Invalidate cached rig data (name changed; cached rig still has old name)
        invalidate_rig_cache(uuid)

    if request.headers.get('HX-Request'):
        return render(request, 'dashboard/_rig_name.html', {'rig': rig})

    return redirect('dashboard:rig-detail', uuid=uuid)


@login_required
@rate_limit(max_requests=30, window_s=60)
def htmx_report_data(request, uuid):
    """HTMX endpoint: renders report table partial for a rig.

    Fetches aggregated report data and passes it to _report_table.html.
    Uses cached Rig lookup to avoid DB query per request.
    """
    rig = _get_rig_light_cached(uuid, request.user)
    if rig is None:
        raise Http404

    range_hours = int(request.GET.get('range_hours', 24))
    if range_hours not in (24, 168, 720):
        range_hours = 24

    # Cache report data per rig+range (55s TTL — under heartbeat interval)
    cache_key = f'report_{uuid}_{range_hours}'
    context = cache.get(cache_key)
    if context is None:
        context = _build_report_context(uuid, str(uuid), range_hours)
        cache.set(cache_key, context, 55)

    # Add user-specific cost estimate (not cached — depends on user settings)
    # Copy before adding user-specific values so one user's electricity rate
    # cannot leak into another user's cached report context.
    context = dict(context)
    try:
        rate = float(request.user.electricity_rate_kwh)
        context['power_cost_estimate'] = round(context['power_total_kwh'] * rate, 2)
        energy_kwh = context.get('cost_per_active_gpu_hour_energy_kwh')
        active_gpu_hours = context.get('active_gpu_hours')
        context['cost_per_active_gpu_hour'] = (
            round((energy_kwh * rate) / active_gpu_hours, 4)
            if energy_kwh is not None and active_gpu_hours is not None and active_gpu_hours > 0
            else None
        )
        idle_energy_kwh = context.get('idle_energy_kwh')
        context['idle_power_waste_cost'] = (
            round(idle_energy_kwh * rate, 2)
            if idle_energy_kwh is not None
            else None
        )
    except (TypeError, ValueError, ArithmeticError):
        context['power_cost_estimate'] = None
        context['cost_per_active_gpu_hour'] = None
        context['idle_power_waste_cost'] = None

    return render(request, 'dashboard/_report_table.html', context)


def _build_report_context(uuid, uuid_str, range_hours):
    """Build the report context dict (separated for caching).

    Performance strategy:
    - For tables that ARE compacted (GPUMetric, StorageMetric, NetworkMetric),
      use SQL aggregation at the chart's bucket size. For 7d/30d ranges,
      this means scanning ~700 rows instead of ~10000 raw rows.
    - MetricSnapshot summary statistics use one aggregate query.
    - P50/P95/P99 and robust MAD/IQR/outlier statistics reuse the existing
      GPUMetric raw scan for per-GPU metrics; system percentiles use a values-only query.
    - Job state transitions use a chronological values-only scan of has_active_job.
    - Power-on hours before restart uses a chronological values-only uptime scan.
    - Underutilization duration correlates per-GPU utilization samples with
      same-timestamp job-state samples, then accumulates qualifying intervals.
    - The power cost (kWh) calculation is derived from the existing
      snap_agg total_system_power_w_avg — no separate query needed.
    - Caching at the view level (55s TTL) handles the common case of repeated loads.

    Main query groups for the report context: 10 queries
        1. GPUMetric raw scan (identity changes, slopes, percentiles, MAD/IQR, outliers)
        2. GPUMetric aggregation (summary metrics)
        3. MetricSnapshot aggregation (CPU/Memory/Power/Errors)
        4. MetricSnapshot values query (system percentiles)
        5. MetricSnapshot ordered job-state scan (transition count)
        6. MetricSnapshot ordered uptime scan (average uptime before reboot)
        7. MetricSnapshot job-state lookup for underutilization duration
        8. StorageMetric aggregation
        9. NetworkMetric aggregation
    Additional LatestSnapshot / latest GPUMetric lookups are performed when
    resolving each GPU's current identity, as in the existing implementation.
    """
    now = timezone.now()
    start = now - timedelta(hours=range_hours)
    base_filter = dict(rig_uuid=uuid_str, timestamp__gte=start, timestamp__lte=now)

    from django.db.models import Avg, Max, Min, Sum, Q
    from django.db.models.functions import Cast
    from django.db.models.fields import IntegerField

    # Query 1a: GPU raw scan for identity change detection
    # Fetches all needed fields in chronological order per GPU index
    gpu_raw = list(
        GPUMetric.objects.filter(**base_filter)
        .values(
            'gpu_index', 'gpu_uuid', 'model', 'timestamp',
            'gpu_temp_c', 'gpu_util_pct', 'power_draw_w', 'power_limit_w',
            'mem_controller_util_pct', 'mem_used_mb', 'fan_speed_pct',
            'gpu_core_clock_mhz', 'gpu_mem_clock_mhz',
        )
        .order_by('gpu_index', 'timestamp')
    )

    # Telemetry coverage and field completeness reuse the existing GPU raw scan.
    # The one-hour continuity cap supports both raw and hourly-compacted data
    # while preventing long outages from being counted as covered time.
    gpu_coverage_fields = (
        'gpu_temp_c', 'gpu_util_pct', 'mem_controller_util_pct', 'power_draw_w',
        'mem_used_mb', 'fan_speed_pct', 'gpu_core_clock_mhz', 'gpu_mem_clock_mhz',
    )
    gpu_rows_by_index = {}
    for row in gpu_raw:
        gpu_rows_by_index.setdefault(row['gpu_index'], []).append(row)
    gpu_coverage_by_index = {
        idx: _telemetry_coverage_stats(
            rows, gpu_coverage_fields, range_hours * 3600.0,
            GPU_TEMPERATURE_MAX_SAMPLE_GAP_S,
        )
        for idx, rows in gpu_rows_by_index.items()
    }

    # Query 1b: GPU metrics aggregation (groups by index + model only, NOT uuid)
    # UUID is fetched from raw data for the header to avoid fragmentation
    gpu_agg = list(
        GPUMetric.objects.filter(**base_filter)
        .values('gpu_index', 'model')
        .annotate(
            gpu_temp_c_avg=Avg('gpu_temp_c'),
            gpu_temp_c_max=Max('gpu_temp_c'),
            gpu_util_pct_avg=Avg('gpu_util_pct'),
            gpu_util_pct_max=Max('gpu_util_pct'),
            mem_controller_util_pct_avg=Avg('mem_controller_util_pct'),
            mem_controller_util_pct_max=Max('mem_controller_util_pct'),
            power_draw_w_avg=Avg('power_draw_w'),
            power_draw_w_max=Max('power_draw_w'),
            mem_used_mb_avg=Avg('mem_used_mb'),
            mem_used_mb_max=Max('mem_used_mb'),
            fan_speed_pct_avg=Avg('fan_speed_pct'),
            fan_speed_pct_max=Max('fan_speed_pct'),
            gpu_core_clock_mhz_avg=Avg('gpu_core_clock_mhz'),
            gpu_core_clock_mhz_max=Max('gpu_core_clock_mhz'),
            gpu_mem_clock_mhz_avg=Avg('gpu_mem_clock_mhz'),
            gpu_mem_clock_mhz_max=Max('gpu_mem_clock_mhz'),
            cooling_efficiency_index_avg=Avg('cooling_efficiency_index'),
            cooling_efficiency_index_max=Max('cooling_efficiency_index'),
            fan_adjusted_cooling_response_avg=Avg('fan_adjusted_cooling_response'),
            fan_adjusted_cooling_response_max=Max('fan_adjusted_cooling_response'),
            
        ).order_by('gpu_index')
    )
    
    # Temperature-to-PowerDraw Ratio Stability
    #
    # Ratio = GPU temperature / GPU power draw.
    # Population standard deviation measures variability across
    # all valid samples in the selected reporting period.

    temp_power_ratios_by_gpu = {}

    for row in gpu_raw:
        idx = row['gpu_index']
        temp = row['gpu_temp_c']
        power = row['power_draw_w']

        # Skip missing measurements and zero/negative power.
        if temp is None or power is None or power <= 0:
            continue

        ratio = temp / power

        temp_power_ratios_by_gpu.setdefault(idx, []).append(ratio)

    gpu_temp_power_stability = {}

    for idx, ratios in temp_power_ratios_by_gpu.items():
        if len(ratios) >= 2:
            gpu_temp_power_stability[idx] = pstdev(ratios)
        else:
            gpu_temp_power_stability[idx] = None

    # Post-process: detect identity changes per GPU index from raw data
    changes_by_index = {}
    for row in gpu_raw:
        idx = row['gpu_index']
        if idx not in changes_by_index:
            changes_by_index[idx] = []
        changes_by_index[idx].append({
            'uuid': row['gpu_uuid'] or '',
            'model': row['model'] or '',
            'timestamp': row['timestamp'],
        })

    gpu_identity_changes = []
    for idx, history in changes_by_index.items():
        prev = None
        for entry in history:
            if prev and (prev['uuid'] != entry['uuid'] or prev['model'] != entry['model']):
                gpu_identity_changes.append({
                    'gpu_index': idx,
                    'from_uuid': prev['uuid'],
                    'from_model': prev['model'],
                    'to_uuid': entry['uuid'],
                    'to_model': entry['model'],
                    'change_timestamp': entry['timestamp'],
                })
            prev = entry

    # Deduplicate gpu_agg to one row per index (latest model/uuid for header)
    # UUID is fetched from raw data for the header to avoid fragmentation
    gpu_devices = []
    seen = set()
    for row in reversed(gpu_agg):
        idx = row['gpu_index']
        if idx not in seen:
            seen.add(idx)
            # Professional identity: current UUID from LatestSnapshot (independent of range window)
            latest_snap = LatestSnapshot.objects.filter(rig_uuid=uuid_str).first()
            latest_metric = GPUMetric.objects.filter(
                rig_uuid=uuid_str, gpu_index=idx
            ).order_by('-timestamp').values('gpu_uuid', 'model').first()
            snap_uuids = (latest_snap.gpu_uuids_json if latest_snap and latest_snap.gpu_uuids_json else []) or []
            snap_uuid = snap_uuids[idx] if (snap_uuids and idx < len(snap_uuids)) else None
            row['gpu_uuid'] = (str(snap_uuid) if snap_uuid else None) or \
                (str(latest_metric.get('gpu_uuid', '')) if latest_metric else '') or ''
            gpu_devices.append(row)
    gpu_devices.reverse()  # restore index order
    
    # P50/P95/P99 distribution statistics for per-GPU measurements.
    # Robust MAD/IQR/outlier statistics are exact only for the 24h window:
    # GPUMetric rows older than one day are compacted into bucket aggregates,
    # and robust statistics must not be reconstructed from average-only rows.
    gpu_percentile_fields = (
        'gpu_temp_c',
        'gpu_util_pct',
        'power_draw_w',
        'mem_controller_util_pct',
        'mem_used_mb',
        'fan_speed_pct',
        'gpu_core_clock_mhz',
        'gpu_mem_clock_mhz',
    )
    gpu_values_by_index = {}
    for row in gpu_raw:
        idx = row['gpu_index']
        per_gpu = gpu_values_by_index.setdefault(
            idx, {field: [] for field in gpu_percentile_fields}
        )
        for field in gpu_percentile_fields:
            value = row.get(field)
            if value is not None:
                per_gpu[field].append(value)

    gpu_percentiles_by_index = {}
    gpu_robust_stats_by_index = {}
    gpu_robust_metric_labels = {
        'gpu_util_pct': ('Core Utilization', '%'),
        'gpu_temp_c': ('Temperature', '°C'),
        'power_draw_w': ('Power Draw', 'W'),
        'gpu_core_clock_mhz': ('Core Clock', 'MHz'),
        'fan_speed_pct': ('Fan Speed', '%'),
        'gpu_mem_clock_mhz': ('Memory Clock', 'MHz'),
        'mem_controller_util_pct': ('Memory Controller Utilization', '%'),
        'mem_used_mb': ('VRAM Used', 'MB'),
    }
    for idx, field_values in gpu_values_by_index.items():
        per_gpu_percentiles = {}
        robust_rows = []
        for field, values in field_values.items():
            for percentile_name, percentile_value in _percentile_stats(values).items():
                per_gpu_percentiles[f'{field}_{percentile_name}'] = percentile_value
            # GPUMetric is raw for the 24h window, but 7d/30d windows include
            # compacted bucket averages. Do not treat those averages as raw
            # observations for MAD, IQR, or robust outlier detection.
            robust = (
                _robust_distribution_stats(values)
                if range_hours == 24
                else {
                    'sample_count': None,
                    'median': None,
                    'mad': None,
                    'p25': None,
                    'p75': None,
                    'iqr': None,
                    'outlier_count': None,
                    'outlier_pct': None,
                }
            )
            label, unit = gpu_robust_metric_labels[field]
            robust_rows.append({
                'field': field,
                'label': label,
                'unit': unit,
                **robust,
            })
        gpu_percentiles_by_index[idx] = per_gpu_percentiles
        gpu_robust_stats_by_index[idx] = robust_rows

    # Conditional temperature distribution: compare temperatures under similar
    # operating conditions. Normalize GPUMetric.power_draw_w against the
    # power limit recorded alongside that same per-GPU time-series sample in
    # GPUMetric.power_limit_w. Ten-percent bands are comparable across GPUs
    # with different power limits. These distributions are shown only for the
    # raw 24h window because longer windows contain compacted bucket averages.
    CONDITIONAL_MIN_SAMPLES = 5
    UTILIZATION_BANDS = (
        ('Low', 0.0, 30.0),
        ('Medium', 30.0, 70.0),
        ('High', 70.0, 100.000001),
    )
    POWER_PERCENT_BAND_WIDTH = 10

    conditional_samples_by_gpu = {}
    for row in gpu_raw:
        idx = row.get('gpu_index')
        temperature = row.get('gpu_temp_c')
        power = row.get('power_draw_w')
        utilization = row.get('gpu_util_pct')
        if temperature is None:
            continue
        try:
            temperature = float(temperature)
        except (TypeError, ValueError, OverflowError):
            continue
        if not math.isfinite(temperature):
            continue
        sample = {
            'temperature_c': temperature,
            'power_w': None,
            'power_limit_w': None,
            'normalized_power_pct': None,
            'utilization_pct': None,
        }
        try:
            power = float(power) if power is not None else None
            if power is not None and math.isfinite(power) and power >= 0:
                sample['power_w'] = power
        except (TypeError, ValueError, OverflowError):
            pass

        # The power limit is stored directly on each GPUMetric row, so no
        # cross-table timestamp matching or fallback to the latest snapshot is
        # necessary for this historical time-series analysis.
        try:
            power_limit = row.get('power_limit_w')
            power_limit = float(power_limit) if power_limit is not None else None
            if (
                sample['power_w'] is not None
                and power_limit is not None
                and math.isfinite(power_limit)
                and power_limit > 0
            ):
                sample['power_limit_w'] = power_limit
                sample['normalized_power_pct'] = sample['power_w'] / power_limit * 100.0
        except (TypeError, ValueError, OverflowError):
            pass

        try:
            utilization = float(utilization) if utilization is not None else None
            if utilization is not None and math.isfinite(utilization) and 0 <= utilization <= 100:
                sample['utilization_pct'] = utilization
        except (TypeError, ValueError, OverflowError):
            pass
        conditional_samples_by_gpu.setdefault(idx, []).append(sample)

    gpu_conditional_temperature_by_index = {}
    for idx, samples in conditional_samples_by_gpu.items():
        if range_hours != 24:
            unavailable = {
                'available': False,
                'reason': 'Conditional distributions require raw GPUMetric samples; 7d/30d data contains compacted bucket averages.',
                'utilization_bands': [],
                'power_bands': [],
                'power_band_width_pct': POWER_PERCENT_BAND_WIDTH,
                'low_high_utilization_delta_c': None,
                'low_high_power_delta_c': None,
            }
            gpu_conditional_temperature_by_index[idx] = unavailable
            continue

        def summarize_conditional_band(label, observations, lower=None, upper=None):
            temps = [sample['temperature_c'] for sample in observations]
            enough = len(temps) >= CONDITIONAL_MIN_SAMPLES
            limits = [sample['power_limit_w'] for sample in observations if sample.get('power_limit_w') is not None]
            avg_limit_w = (sum(limits) / len(limits)) if limits else None
            return {
                'label': label,
                'lower': lower,
                'upper': upper,
                'sample_count': len(temps),
                'avg_temp_c': round(sum(temps) / len(temps), 2) if enough else None,
                'p95_temp_c': _percentile(temps, 95) if enough else None,
                'avg_power_limit_w': round(avg_limit_w, 1) if avg_limit_w is not None else None,
                'lower_power_w': round(avg_limit_w * lower / 100.0, 1) if avg_limit_w is not None and lower is not None else None,
                'upper_power_w': round(avg_limit_w * upper / 100.0, 1) if avg_limit_w is not None and upper is not None else None,
                'available': enough,
            }

        utilization_bands = []
        for label, lower, upper in UTILIZATION_BANDS:
            observations = [
                sample for sample in samples
                if sample['utilization_pct'] is not None
                and lower <= sample['utilization_pct'] < upper
            ]
            utilization_bands.append(summarize_conditional_band(label, observations, lower, upper))

        power_bands = []
        normalized_samples = [
            sample for sample in samples
            if sample['normalized_power_pct'] is not None
        ]
        for band_index in range(10):
            lower = band_index * POWER_PERCENT_BAND_WIDTH
            upper = lower + POWER_PERCENT_BAND_WIDTH
            observations = [
                sample for sample in normalized_samples
                if lower <= sample['normalized_power_pct'] < upper
                or (band_index == 9 and sample['normalized_power_pct'] == 100.0)
            ]
            power_bands.append(summarize_conditional_band(
                f'{lower}–{upper}%', observations, lower, upper
            ))
        over_limit_observations = [
            sample for sample in normalized_samples
            if sample['normalized_power_pct'] > 100.0
        ]
        if over_limit_observations:
            power_bands.append(summarize_conditional_band(
                '>100%', over_limit_observations, 100.0, None
            ))

        usable_util = [band for band in utilization_bands if band['available']]
        low_util = next((band for band in usable_util if band['label'] == 'Low'), None)
        high_util = next((band for band in usable_util if band['label'] == 'High'), None)
        usable_power = [band for band in power_bands if band['available'] and band['label'] != '>100%']
        low_power = usable_power[0] if len(usable_power) >= 2 else None
        high_power = usable_power[-1] if len(usable_power) >= 2 else None

        gpu_conditional_temperature_by_index[idx] = {
            'available': True,
            'reason': None,
            'minimum_samples_per_band': CONDITIONAL_MIN_SAMPLES,
            'utilization_bands': utilization_bands,
            'power_bands': power_bands,
            'power_band_width_pct': POWER_PERCENT_BAND_WIDTH,
            'power_limit_valid_sample_count': len(normalized_samples),
            'power_limit_missing_sample_count': sum(
                1 for sample in samples
                if sample['power_w'] is not None and sample['power_limit_w'] is None
            ),
            'low_high_utilization_delta_c': (
                round(high_util['avg_temp_c'] - low_util['avg_temp_c'], 2)
                if low_util and high_util else None
            ),
            'low_high_power_delta_c': (
                round(high_power['avg_temp_c'] - low_power['avg_temp_c'], 2)
                if low_power and high_power else None
            ),
            'low_power_band_label': low_power['label'] if low_power else None,
            'high_power_band_label': high_power['label'] if high_power else None,
        }

    # GPU temperature threshold exceedance statistics (strictly above 85°C).
    # Duration is estimated only across adjacent valid temperature samples no
    # more than one hour apart. Long gaps and missing temperatures break events
    # and are excluded from observed-time denominators. No extra DB query is
    # needed because gpu_raw already contains the timestamp and temperature.
    temperature_rows_by_gpu = {}
    for row in gpu_raw:
        temperature_rows_by_gpu.setdefault(row['gpu_index'], []).append(row)

    gpu_temperature_exceedance_by_index = {}
    for idx, rows in temperature_rows_by_gpu.items():
        rows.sort(key=lambda row: row['timestamp'])
        event_count = 0
        observed_seconds = 0.0
        exceedance_seconds = 0.0
        current_event_seconds = 0.0
        longest_event_seconds = 0.0
        previous_is_contiguous_above = False
        previous_row = None

        for position, row in enumerate(rows):
            temperature = row.get('gpu_temp_c')
            timestamp = row.get('timestamp')
            if temperature is None or timestamp is None:
                previous_is_contiguous_above = False
                current_event_seconds = 0.0
                previous_row = row
                continue

            # A gap over the limit breaks continuity, even when both readings
            # are above the threshold.
            if previous_row is not None:
                previous_temp = previous_row.get('gpu_temp_c')
                previous_timestamp = previous_row.get('timestamp')
                gap_seconds = (
                    (timestamp - previous_timestamp).total_seconds()
                    if previous_timestamp is not None else None
                )
                if (previous_temp is None or gap_seconds is None or
                        gap_seconds <= 0 or
                        gap_seconds > GPU_TEMPERATURE_MAX_SAMPLE_GAP_S):
                    previous_is_contiguous_above = False
                    current_event_seconds = 0.0

            is_above_threshold = float(temperature) > GPU_TEMPERATURE_THRESHOLD_C
            if is_above_threshold and not previous_is_contiguous_above:
                event_count += 1
                current_event_seconds = 0.0

            # Estimate the interval represented by this reading using the next
            # reading, but only when both temperatures are valid and the gap is
            # positive and no longer than the configured maximum.
            if position + 1 < len(rows):
                next_row = rows[position + 1]
                next_temp = next_row.get('gpu_temp_c')
                next_timestamp = next_row.get('timestamp')
                interval_seconds = (
                    (next_timestamp - timestamp).total_seconds()
                    if next_timestamp is not None else None
                )
                valid_interval = (
                    next_temp is not None and interval_seconds is not None and
                    0 < interval_seconds <= GPU_TEMPERATURE_MAX_SAMPLE_GAP_S
                )
                if valid_interval:
                    observed_seconds += interval_seconds
                    if is_above_threshold:
                        exceedance_seconds += interval_seconds
                        current_event_seconds += interval_seconds
                        longest_event_seconds = max(
                            longest_event_seconds, current_event_seconds
                        )

            previous_is_contiguous_above = is_above_threshold
            previous_row = row

        gpu_temperature_exceedance_by_index[idx] = {
            'temperature_threshold_c': GPU_TEMPERATURE_THRESHOLD_C,
            'temperature_exceedance_count': event_count,
            'temperature_exceedance_duration_minutes': round(
                exceedance_seconds / 60.0, 2
            ),
            'temperature_longest_exceedance_minutes': round(
                longest_event_seconds / 60.0, 2
            ),
            'temperature_exceedance_observed_pct': (
                round(100.0 * exceedance_seconds / observed_seconds, 2)
                if observed_seconds > 0 else None
            ),
            'temperature_observed_duration_minutes': round(
                observed_seconds / 60.0, 2
            ),
        }

    # Thermal Degradation Slope (per GPU).
    # Match workload intensity by retaining samples within +/-10% of the
    # median GPU utilization for that GPU in the selected reporting period.
    # The tolerance is relative to the median (not percentage points).
    # Reuse gpu_raw so this feature does not add another database query.
    thermal_samples_by_gpu = {}
    for row in gpu_raw:
        idx = row['gpu_index']
        utilization = row['gpu_util_pct']
        temperature = row['gpu_temp_c']
        timestamp = row['timestamp']
        if utilization is None or temperature is None or timestamp is None:
            continue
        thermal_samples_by_gpu.setdefault(idx, []).append(
            (timestamp, float(temperature), float(utilization))
        )

    thermal_degradation_slope_by_gpu = {}
    for idx, samples in thermal_samples_by_gpu.items():
        median_utilization = median(sample[2] for sample in samples)
        utilization_tolerance = abs(median_utilization) * 0.10
        matched_samples = [
            sample for sample in samples
            if abs(sample[2] - median_utilization) <= utilization_tolerance
        ]

        # At least two distinct timestamps are needed to estimate a slope.
        if len(matched_samples) < 2:
            thermal_degradation_slope_by_gpu[idx] = None
            continue

        first_timestamp = min(sample[0] for sample in matched_samples)
        time_days = [
            (sample[0] - first_timestamp).total_seconds() / 86400.0
            for sample in matched_samples
        ]
        temperatures = [sample[1] for sample in matched_samples]
        mean_time = sum(time_days) / len(time_days)
        mean_temperature = sum(temperatures) / len(temperatures)
        denominator = sum((t - mean_time) ** 2 for t in time_days)

        if denominator <= 0:
            thermal_degradation_slope_by_gpu[idx] = None
            continue

        slope_c_per_day = sum(
            (t - mean_time) * (temp - mean_temperature)
            for t, temp in zip(time_days, temperatures)
        ) / denominator
        thermal_degradation_slope_by_gpu[idx] = round(slope_c_per_day, 3)

    # Add derived metrics to each GPU device.
    for device in gpu_devices:
        idx = device['gpu_index']
        device['temp_power_stability_stddev'] = (
            gpu_temp_power_stability.get(idx)
        )
        device['thermal_degradation_slope_c_per_day'] = (
            thermal_degradation_slope_by_gpu.get(idx)
        )
        device.update(gpu_temperature_exceedance_by_index.get(idx, {
            'temperature_threshold_c': GPU_TEMPERATURE_THRESHOLD_C,
            'temperature_exceedance_count': 0,
            'temperature_exceedance_duration_minutes': 0.0,
            'temperature_longest_exceedance_minutes': 0.0,
            'temperature_exceedance_observed_pct': None,
            'temperature_observed_duration_minutes': 0.0,
        }))
        device.update(gpu_percentiles_by_index.get(idx, {}))
        device['gpu_robust_stats'] = gpu_robust_stats_by_index.get(idx, [])
        device['gpu_conditional_temperature'] = gpu_conditional_temperature_by_index.get(idx, {
            'available': False,
            'reason': 'No valid GPU temperature samples in this reporting period.',
            'utilization_bands': [],
            'power_bands': [],
            'power_band_width_pct': POWER_PERCENT_BAND_WIDTH,
            'low_high_utilization_delta_c': None,
            'low_high_power_delta_c': None,
        })
        device.update({f'telemetry_{key}': value for key, value in gpu_coverage_by_index.get(idx, {}).items()})

    # Query 2: CPU / Memory / Power / Errors aggregation
    # MetricSnapshot may contain raw or hourly-compacted samples depending on
    # the reporting range. Keep these summary statistics in one aggregate query.
    snap_agg = MetricSnapshot.objects.filter(**base_filter).aggregate(
        cpu_utilization_pct_avg=Avg('cpu_utilization_pct'),
        cpu_utilization_pct_max=Max('cpu_utilization_pct'),
        cpu_temp_c_avg=Avg('cpu_temp_c'),
        cpu_temp_c_max=Max('cpu_temp_c'),
        cpu_power_w_avg=Avg('cpu_power_w'),
        cpu_power_w_max=Max('cpu_power_w'),
        cpu_freq_current_mhz_avg=Avg('cpu_freq_current_mhz'),
        cpu_freq_current_mhz_max=Max('cpu_freq_current_mhz'),
        mem_used_bytes_avg=Avg('mem_used_bytes'),
        mem_used_bytes_max=Max('mem_used_bytes'),
        swap_used_bytes_avg=Avg('swap_used_bytes'),
        swap_used_bytes_max=Max('swap_used_bytes'),
        total_system_power_w_avg=Avg('total_system_power_w'),
        total_system_power_w_max=Max('total_system_power_w'),
        idle_system_power_w_avg=Avg(
            'total_system_power_w',
            filter=Q(has_active_job=False),
        ),
        active_system_power_w_avg=Avg(
            'total_system_power_w',
            filter=Q(has_active_job=True),
        ),
        error_count_sum=Sum('error_count'),
        has_active_job_avg=Avg(Cast('has_active_job', IntegerField())),
    )

    # System-level P50/P95/P99 statistics. Percentiles are calculated in
    # Python for database portability; the ORM query fetches only the numeric
    # fields needed for these distribution summaries.
    system_percentile_fields = (
        'cpu_utilization_pct',
        'cpu_temp_c',
        'cpu_power_w',
        'cpu_freq_current_mhz',
        'mem_used_bytes',
        'swap_used_bytes',
        'total_system_power_w',
    )
    system_coverage_fields = (
        'cpu_utilization_pct', 'cpu_temp_c', 'cpu_power_w',
        'cpu_freq_current_mhz', 'mem_used_bytes', 'swap_used_bytes',
        'total_system_power_w', 'has_active_job', 'error_count',
    )
    system_raw = list(
        MetricSnapshot.objects.filter(**base_filter).values(
            'timestamp', *system_percentile_fields,
            'has_active_job', 'error_count',
        ).order_by('timestamp')
    )
    system_telemetry_coverage = _telemetry_coverage_stats(
        system_raw, system_coverage_fields, range_hours * 3600.0,
        GPU_TEMPERATURE_MAX_SAMPLE_GAP_S,
    )
    system_percentiles = {}
    for field in system_percentile_fields:
        stats = _percentile_stats(row[field] for row in system_raw)
        for percentile_name, percentile_value in stats.items():
            system_percentiles[f'{field}_{percentile_name}'] = percentile_value

    # Query 3: Job state transition frequency.
    # Scan chronologically and count only transitions between adjacent known
    # states. A NULL/unknown state breaks the sequence, avoiding inferred
    # transitions across missing state information.
    job_states = MetricSnapshot.objects.filter(**base_filter).order_by(
        'timestamp'
    ).values_list('has_active_job', flat=True)

    job_state_transition_count = 0
    previous_job_state = None
    for job_state in job_states.iterator():
        if job_state is None:
            previous_job_state = None
            continue

        if previous_job_state is not None and job_state != previous_job_state:
            job_state_transition_count += 1

        previous_job_state = job_state

    # Query 4: Power-on hours before restart.
    # A decrease in uptime_s marks a reboot. Record the last observed uptime
    # before each decrease; only completed uptime periods are averaged, so a
    # currently running period is not mistaken for a completed pre-reboot run.
    uptime_values = MetricSnapshot.objects.filter(**base_filter).order_by(
        'timestamp'
    ).values_list('uptime_s', flat=True)

    completed_uptime_periods_s = []
    previous_uptime_s = None
    peak_uptime_since_restart_s = None
    for uptime_s in uptime_values.iterator():
        if uptime_s is None:
            # Missing uptime breaks adjacency; do not infer a reboot across it.
            previous_uptime_s = None
            peak_uptime_since_restart_s = None
            continue

        if previous_uptime_s is not None and uptime_s < previous_uptime_s:
            if peak_uptime_since_restart_s is not None:
                completed_uptime_periods_s.append(peak_uptime_since_restart_s)
            peak_uptime_since_restart_s = uptime_s
        elif peak_uptime_since_restart_s is None:
            peak_uptime_since_restart_s = uptime_s
        else:
            peak_uptime_since_restart_s = max(peak_uptime_since_restart_s, uptime_s)

        previous_uptime_s = uptime_s

    power_on_hours_before_restart_avg = (
        round(
            (sum(completed_uptime_periods_s) / len(completed_uptime_periods_s)) / 3600,
            2,
        )
        if completed_uptime_periods_s
        else None
    )

    # Query 5: Underutilization duration per GPU.
    # GPUMetric owns the per-GPU utilization series; has_active_job is stored
    # on MetricSnapshot at the same ingest timestamp. Exact timestamp matching
    # avoids attributing a stale system job state to a GPU sample.
    job_state_by_timestamp = dict(
        MetricSnapshot.objects.filter(**base_filter)
        .values_list('timestamp', 'has_active_job')
    )
    underutilization_by_gpu = {}
    underutilization_start_by_gpu = {}
    underutilization_previous_timestamp_by_gpu = {}

    for row in gpu_raw:
        idx = row['gpu_index']
        timestamp = row['timestamp']
        gpu_util = row['gpu_util_pct']
        has_active_job = job_state_by_timestamp.get(timestamp)
        is_underutilized = (
            gpu_util is not None
            and gpu_util < 5
            and has_active_job is True
        )

        underutilization_by_gpu.setdefault(idx, 0.0)
        if is_underutilized:
            if idx not in underutilization_start_by_gpu:
                underutilization_start_by_gpu[idx] = timestamp
        else:
            start = underutilization_start_by_gpu.pop(idx, None)
            if start is not None:
                underutilization_by_gpu[idx] += max(
                    0.0, (timestamp - start).total_seconds() / 60.0
                )

        underutilization_previous_timestamp_by_gpu[idx] = timestamp

    # A qualifying run that has no later sample cannot be assigned a duration
    # reliably, so it is intentionally not extrapolated past the last sample.
    for device in gpu_devices:
        idx = device['gpu_index']
        start = underutilization_start_by_gpu.get(idx)
        last_timestamp = underutilization_previous_timestamp_by_gpu.get(idx)
        if start is not None and last_timestamp is not None and last_timestamp > start:
            underutilization_by_gpu[idx] += (
                last_timestamp - start
            ).total_seconds() / 60.0
        device['underutilization_duration_minutes'] = round(
            underutilization_by_gpu.get(idx, 0.0), 1
        )

    # Query 6: Storage metrics per device
    disk_devices = list(
        StorageMetric.objects.filter(**base_filter)
        .values('device', 'mountpoint')
        .annotate(
            disk_usage_pct_max=Max('usage_pct'),
            disk_read_bytes_sum=Sum('read_bytes_delta'),
            disk_write_bytes_sum=Sum('write_bytes_delta'),
            disk_read_iops_max=Max('read_iops_delta'),
            disk_write_iops_max=Max('write_iops_delta'),
            disk_utilization_pct_max=Max('utilization_pct'),
        ).order_by('device')
    )

    # Query 7: Network metrics per interface
    net_interfaces = list(
        NetworkMetric.objects.filter(**base_filter)
        .values('interface')
        .annotate(
            net_rx_bytes_sum=Sum('rx_bytes_delta'),
            net_tx_bytes_sum=Sum('tx_bytes_delta'),
            net_rx_errors_sum=Sum('rx_errors'),
            net_tx_errors_sum=Sum('tx_errors'),
        ).order_by('interface')
    )

    # Calculate power_total_kwh from the existing aggregation.
    # Previously: separate query that did TruncMinute/TruncHour grouping.
    # Now: derive from total_system_power_w_avg * range_hours.
    # This is less precise (assumes constant power over the range) but
    # avoids a 5th DB query.
    avg_power_w = snap_agg.get('total_system_power_w_avg') or 0
    power_total_kwh = round((avg_power_w * range_hours) / 1000, 3)

    # Job saturation: percentage of samples with an active job.
    # AVG(CAST(has_active_job AS INTEGER)) ignores NULL job-state samples.
    job_saturation_pct = (
        round(snap_agg['has_active_job_avg'] * 100, 1)
        if snap_agg.get('has_active_job_avg') is not None
        else None
    )

    # Idle Power Waste Ratio: average system power while no job is active
    # divided by average system power while a job is active. Samples with an
    # unknown job state are excluded by the conditional AVG aggregates.
    # This is a relative power ratio, not a share of total energy consumed.
    avg_idle_power_w = snap_agg.get('idle_system_power_w_avg')
    avg_active_power_w = snap_agg.get('active_system_power_w_avg')
    idle_power_waste_ratio = (
        round(avg_idle_power_w / avg_active_power_w, 3)
        if avg_idle_power_w is not None
        and avg_active_power_w is not None
        and avg_active_power_w > 0
        else None
    )

    # Cost per Active GPU-Hour.
    # A system-level has_active_job flag does not identify which GPU is active,
    # so estimate active GPU-hours from per-GPU utilization >= 5% while a job is
    # active. Weight each qualifying sample by the interval until the next
    # sample for that GPU, capped at one hour to avoid filling long data gaps.
    # This remains an estimate: utilization is a proxy for active compute.
    active_gpu_seconds = 0.0
    rows_by_gpu = {}
    for row in gpu_raw:
        rows_by_gpu.setdefault(row['gpu_index'], []).append(row)

    for idx, rows in rows_by_gpu.items():
        rows.sort(key=lambda row: row['timestamp'])
        for position, current in enumerate(rows):
            timestamp = current['timestamp']
            gpu_util = current['gpu_util_pct']
            has_active_job = job_state_by_timestamp.get(timestamp)
            if gpu_util is None or gpu_util < 5 or has_active_job is not True:
                continue

            interval_end = (
                rows[position + 1]['timestamp']
                if position + 1 < len(rows)
                else now
            )
            interval_seconds = (interval_end - timestamp).total_seconds()
            if interval_seconds > 0:
                active_gpu_seconds += min(interval_seconds, 3600.0)

    active_gpu_hours = active_gpu_seconds / 3600.0
    electricity_rate = None
    # User-specific rate is applied in htmx_report_data after cached context
    # is retrieved, so these two values are finalized there.
    # Idle Power Waste Cost: estimate idle energy from average system power
    # during no-job samples and the estimated idle share of the selected range.
    # The active-job fraction is sample-weighted, so this assumes roughly
    # regular snapshot intervals. Unknown job-state samples are excluded.
    active_fraction = snap_agg.get('has_active_job_avg')
    idle_hours = (
        range_hours * (1.0 - active_fraction)
        if active_fraction is not None
        else None
    )
    idle_energy_kwh = (
        (avg_idle_power_w * idle_hours / 1000.0)
        if avg_idle_power_w is not None and idle_hours is not None
        else None
    )

    return {
        'range_hours': range_hours,
        'gpu_devices': gpu_devices,
        'gpu_identity_changes': gpu_identity_changes,
        'disk_devices': disk_devices,
        'net_interfaces': net_interfaces,
        'power_total_kwh': power_total_kwh,
        'power_cost_estimate': None,
        'job_saturation_pct': job_saturation_pct,
        'idle_power_waste_ratio': idle_power_waste_ratio,
        'active_gpu_hours': round(active_gpu_hours, 3) if active_gpu_hours > 0 else None,
        'cost_per_active_gpu_hour_energy_kwh': power_total_kwh if active_gpu_hours > 0 else None,
        'idle_energy_kwh': round(idle_energy_kwh, 3) if idle_energy_kwh is not None else None,
        'job_state_transition_count': job_state_transition_count,
        'power_on_hours_before_restart_avg': power_on_hours_before_restart_avg,
        **snap_agg,
        **system_percentiles,
        **{f'system_telemetry_{key}': value for key, value in system_telemetry_coverage.items()},
    }
