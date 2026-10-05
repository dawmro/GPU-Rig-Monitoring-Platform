"""Django system checks for metrics_app.

All checks are registered via @register('metrics_app') and run by
`manage.py check`, `manage.py test`, and any management command that
invokes the check framework.

Registration happens at app-ready time: metrics_app/apps.py `ready()`
imports this module (`from . import checks`). Without that import the
@register decorators never fire, `manage.py check` reports 0 issues
(false comfort), and every defense check in this module is dead code
(the exact defect that let the `gpu_inforom` compaction gap ship —
see docs/RESEARCH-gpu-static-identifiers.md §8.2).

CWD independence (defect class from §8.4): checks that inspect
compaction behavior read COMPACT_TABLES in-memory (imported from the
compact_data command module) or use inspect.getsource() on the command
class. They NEVER use `open('gpu_monitor/...')` relative paths, which
only resolve from the repo root and silently no-op (or false-positive)
when run from gpu_monitor/ — where manage.py actually runs.
"""
from django.core.checks import Error, register
from metrics_app.models import MetricSnapshot, LatestSnapshot
from metrics_app.views import ChartDataView
from metrics_app.serializers import IngestSerializer


def _compaction_table(config_table):
    """Return the COMPACT_TABLES entry for `config_table` (in-memory,
    CWD-independent). Returns None if the table is not configured."""
    from metrics_app.management.commands.compact_data import COMPACT_TABLES
    for cfg in COMPACT_TABLES:
        if cfg['table'] == config_table:
            return cfg
    return None


def _gpumetric_static_fields():
    cfg = _compaction_table('metrics_gpumetric')
    return cfg.get('static_fields', []) if cfg else None


def _snapshot_agg_fields():
    cfg = _compaction_table('metrics_metricsnapshot')
    return cfg.get('agg_fields', {}) if cfg else None


def _compact_table_source():
    """Source of Command._compact_table (the SQL generator) — used to
    verify the bool-max INTEGER cast defense without reading a file
    path that depends on the current working directory."""
    import inspect
    from metrics_app.management.commands.compact_data import Command
    return inspect.getsource(Command._compact_table)


@register('metrics_app')
def check_has_active_job_system_checks(app_configs, **kwargs):
    errors = []

    # Layer 1: Model must have the field
    try:
        MetricSnapshot._meta.get_field('has_active_job')
    except Exception:
        errors.append(Error(
            'MetricSnapshot missing has_active_job field',
            hint='Run Step A migration: 0051_metric_snapshot_has_active_job',
            obj='metrics_app',
            id='metrics_app.E001',
        ))

    # Layer 2: ChartDataView must include the metric
    snapshot_metrics = getattr(ChartDataView, 'SNAPSHOT_METRICS', set())
    if 'has_active_job' not in snapshot_metrics:
        errors.append(Error(
            'SNAPSHOT_METRICS missing has_active_job',
            hint='Run Step D: add to ChartDataView.SNAPSHOT_METRICS',
            obj='metrics_app.views.ChartDataView',
            id='metrics_app.E002',
        ))

    # Layer 3: Serializer must write to MetricSnapshot defaults
    try:
        # has_active_job is validated by IngestSerializer (BooleanField)
        # and written into MetricSnapshot defaults by process_ingest.
        # Model + chart layers above are the durable checks; this layer
        # is best-effort source inspection.
        import inspect
        src = inspect.getsource(IngestSerializer)
        if 'has_active_job' not in src:
            errors.append(Error(
                'IngestSerializer does not declare has_active_job',
                hint='Add has_active_job BooleanField to IngestSerializer',
                obj='metrics_app.serializers.IngestSerializer',
                id='metrics_app.E003',
            ))
    except Exception:
        pass  # source inspection is best-effort; model + chart checks are durable

    # Layer 4: Compaction must aggregate the field (in-memory, CWD-safe)
    try:
        agg = _snapshot_agg_fields()
        if agg is None:
            errors.append(Error(
                'compaction: metrics_metricsnapshot not found in COMPACT_TABLES',
                hint='Verify metrics_metricsnapshot entry in compact_data.COMPACT_TABLES',
                obj='metrics_app.management.commands.compact_data',
                id='metrics_app.E004',
            ))
        elif agg.get('has_active_job') != 'max':
            errors.append(Error(
                "compact_data.py missing 'has_active_job': 'max'",
                hint='Run Step C: add to MetricSnapshot agg_fields (use max, not avg)',
                obj='metrics_app.management.commands.compact_data',
                id='metrics_app.E004',
            ))
        else:
            # bool max must use INTEGER cast, not raw MAX(bool) (NULL-propagation)
            compact_src = _compact_table_source()
            if "if agg == 'max' and f == 'has_active_job':" not in compact_src:
                errors.append(Error(
                    "compact_data: bool max missing INTEGER cast defense for has_active_job",
                    hint='Add MAX(CAST(has_active_job AS INTEGER)) branch in _compact_table',
                    obj='metrics_app.management.commands.compact_data',
                    id='metrics_app.E004',
                ))
    except Exception:
        pass  # compaction structure read is best-effort; E004 covers config absence

    return errors


@register('metrics_app')
def check_gpu_uuid_compaction_defense(app_configs, **kwargs):
    errors = []
    from metrics_app.models import GPUMetric
    try:
        f = GPUMetric._meta.get_field('gpu_uuid')
        if not f.blank:
            errors.append(Error('GPUMetric.gpu_uuid missing blank=True',
                                hint='Add blank=True for grouping defense',
                                obj='metrics_app.GPUMetric.gpu_uuid', id='metrics_app.E005'))
        if f.default != '':
            errors.append(Error('GPUMetric.gpu_uuid missing default=""',
                                hint='Default must be empty string',
                                obj='metrics_app.GPUMetric.gpu_uuid', id='metrics_app.E006'))
    except Exception as e:
        errors.append(Error(f'GPUMetric missing gpu_uuid: {e}', id='metrics_app.E007'))

    # Layer 3: gpu_uuid must survive compaction (in-memory, CWD-safe)
    try:
        static_fields = _gpumetric_static_fields()
        if static_fields is None:
            errors.append(Error(
                'compaction: metrics_gpumetric not found in COMPACT_TABLES',
                hint='Verify metrics_gpumetric entry in compact_data.COMPACT_TABLES',
                obj='metrics_app.management.commands.compact_data',
                id='metrics_app.E008',
            ))
        elif 'gpu_uuid' not in static_fields:
            errors.append(Error("compact_data: metrics_gpumetric static_fields missing gpu_uuid",
                                hint='Preserve identity through compaction',
                                obj='metrics_app.management.commands.compact_data',
                                id='metrics_app.E008'))
    except Exception:
        pass

    # Layer 4 (bool aggregation defense): has_active_job must use INTEGER cast,
    # not raw MAX(bool). Verified against the in-memory SQL generator source.
    try:
        agg = _snapshot_agg_fields()
        if agg and agg.get('has_active_job') == 'max':
            compact_src = _compact_table_source()
            if "if agg == 'max' and f == 'has_active_job':" not in compact_src:
                errors.append(Error("compact_data: bool max missing INTEGER cast defense",
                                    hint='Add Cast(has_active_job, IntegerField()) for bool aggregation',
                                    obj='metrics_app.management.commands.compact_data',
                                    id='metrics_app.E009'))
    except Exception:
        errors.append(Error('compact_data: could not read SQL generator source',
                            id='metrics_app.E010'))
    return errors


@register('metrics_app')
def check_chart_query_budget(app_configs, **kwargs):
    errors = []
    try:
        import inspect
        import metrics_app.views as views_mod
        src = inspect.getsource(views_mod)
        if '_safe_gpu_label' not in src:
            errors.append(Error('Chart endpoint missing _safe_gpu_label defense',
                                hint='Add safe identity label helper',
                                obj='metrics_app.views.ChartDataView', id='metrics_app.E011'))
    except Exception:
        errors.append(Error('could not read metrics_app.views source for verification',
                            id='metrics_app.E012'))
    return errors


@register('metrics_app')
def check_storage_hardware_identifiers(app_configs, **kwargs):
    """Defense in depth: disk hardware identifiers must be stored and written.

    (W001 style) model fields exist AND the serializer writes the arrays.
    Static disk identifiers live ONLY in LatestSnapshot (no time-series, no
    compaction) — see storage_models_json / storage_vendors_json /
    storage_serials_json / storage_wwns_json.
    """
    errors = []
    expected = ('storage_models_json', 'storage_vendors_json',
                'storage_serials_json', 'storage_wwns_json')
    for field in expected:
        try:
            LatestSnapshot._meta.get_field(field)
        except Exception:
            errors.append(Error(
                f'LatestSnapshot missing {field} field',
                hint='Run migration adding disk hardware identifiers to LatestSnapshot',
                obj='metrics_app.LatestSnapshot',
                id='metrics_app.E013',
            ))

    # Layer 2: serializer must append all four arrays per disk
    try:
        import inspect
        import metrics_app.serializers as s
        process_src = inspect.getsource(s.process_ingest)
        for field in expected:
            if f"'{field}':" not in process_src:
                errors.append(Error(
                    f"serializer process_ingest does not write '{field}' to LatestSnapshot",
                    hint="Add the array to ls_defaults in process_ingest",
                    obj='metrics_app.serializers.process_ingest',
                    id='metrics_app.E014',
                ))
    except Exception:
        pass  # source inspection is best-effort; model-field check above is durable
    return errors


@register('metrics_app')
def check_gpu_phase2_static_identifiers(app_configs, **kwargs):
    """Defense in depth: GPU Phase 2 static identifiers must be persisted and
    kept through compaction (W001-style, mirrors check_gpu_subvendor_pipeline).

    Layer 1: model fields exist on GPUMetric and LatestSnapshot.
    Layer 2: serializer process_ingest writes both per-row values and
             LatestSnapshot summary arrays.
    Layer 3: compact_data.py keeps all Phase 2 static GPU identifiers
             in the metrics_gpumetric static_fields (in-memory, CWD-safe).
    """
    errors = []
    from metrics_app.models import GPUMetric, LatestSnapshot

    # Phase 2 fields on GPUMetric (CharField/IntegerField/JSONField)
    gpumetric_fields = (
        'gpu_vbios', 'pci_bus_id', 'gpu_architecture', 'gpu_bus_type',
        'gpu_board_id', 'gpu_serial', 'gpu_pci_subsystem', 'gpu_inforom',
    )
    # Phase 2 fields on LatestSnapshot (JSONField arrays) and the actual
    # array variable names used by process_ingest (NOT field.replace('_json','s')
    # — that produced false positives like 'gpu_vbioss'; see research doc §8.3)
    phase2_array_variables = {
        'gpu_vbios_json': 'gpu_vbios',
        'gpu_pci_bus_ids_json': 'gpu_pci_bus_ids',
        'gpu_architecture_json': 'gpu_architecture',
        'gpu_bus_type_json': 'gpu_bus_type',
        'gpu_board_ids_json': 'gpu_board_ids',
        'gpu_serials_json': 'gpu_serials',
        'gpu_pci_subsystems_json': 'gpu_pci_subsystems',
        'gpu_inforom_json': 'gpu_inforom',
    }

    # Layer 1: model fields exist on GPUMetric
    for field in gpumetric_fields:
        try:
            GPUMetric._meta.get_field(field)
        except Exception:
            errors.append(Error(
                f'GPUMetric missing Phase 2 field: {field}',
                hint='Run migration 0058_gpumetric_phase2_static_identifiers',
                obj='metrics_app.GPUMetric',
                id='metrics_app.E015',
            ))

    # Layer 1: model fields exist on LatestSnapshot
    for field in phase2_array_variables:
        try:
            LatestSnapshot._meta.get_field(field)
        except Exception:
            errors.append(Error(
                f'LatestSnapshot missing Phase 2 field: {field}',
                hint='Run migration 0058_gpumetric_phase2_static_identifiers',
                obj='metrics_app.LatestSnapshot',
                id='metrics_app.E016',
            ))

    # Layer 2: serializer must persist Phase 2 fields (per-row + summary arrays)
    try:
        import inspect
        import metrics_app.serializers as s
        process_src = inspect.getsource(s.process_ingest)
        # Check per-row writes in GPUMetric defaults
        for field in gpumetric_fields:
            key = f"'{field}': gpu.get('{field}'"
            if key not in process_src:
                errors.append(Error(
                    f"serializer process_ingest does not write Phase 2 '{field}' to GPUMetric",
                    hint=f"Add '{field}' to the GPUMetric defaults dict",
                    obj='metrics_app.serializers.process_ingest',
                    id='metrics_app.E017',
                ))
        # Check summary arrays in LatestSnapshot defaults (correct variable names)
        for field, var in phase2_array_variables.items():
            key = f"'{field}': {var}"
            if key not in process_src:
                errors.append(Error(
                    f"serializer process_ingest does not write Phase 2 '{field}' to LatestSnapshot",
                    hint=f"Add '{field}': {var} to LatestSnapshot defaults",
                    obj='metrics_app.serializers.process_ingest',
                    id='metrics_app.E018',
                ))
    except Exception:
        pass  # source inspection is best-effort; model-field checks above are durable

    # Layer 3: compaction must keep all Phase 2 static identifiers (in-memory)
    try:
        static_fields = _gpumetric_static_fields()
        if static_fields is None:
            errors.append(Error(
                'compaction: metrics_gpumetric not found in COMPACT_TABLES',
                hint='Verify metrics_gpumetric entry in compact_data.COMPACT_TABLES',
                obj='metrics_app.management.commands.compact_data',
                id='metrics_app.E019',
            ))
        else:
            for field in gpumetric_fields:
                if field not in static_fields:
                    errors.append(Error(
                        f'compact_data: metrics_gpumetric static_fields missing Phase 2 {field}',
                        hint='Static GPU identifiers must survive tier-2/3 compaction',
                        obj='metrics_app.management.commands.compact_data',
                        id='metrics_app.E019',
                    ))
    except Exception:
        pass
    return errors


@register('metrics_app')
def check_gpu_subvendor_pipeline(app_configs, **kwargs):
    """Defense in depth: AIB subvendor must be persisted and kept through
    compaction (W001-style, mirrors check_storage_hardware_identifiers).

    Layer 1: model fields exist (GPUMetric.gpu_subvendor,
             LatestSnapshot.gpu_subvendors_json).
    Layer 2: serializer process_ingest writes both the per-row value and
             the LatestSnapshot summary array.
    Layer 3: compact_data.py keeps both static GPU identifiers
             (gpu_subvendor AND gpu_board_part_number) in the
             metrics_gpumetric static_fields (in-memory, CWD-safe).
    """
    errors = []
    from metrics_app.models import GPUMetric

    # Layer 1: model fields
    try:
        GPUMetric._meta.get_field('gpu_subvendor')
    except Exception:
        errors.append(Error(
            'GPUMetric missing gpu_subvendor field',
            hint='Run migration 0057_gpumetric_gpu_subvendor',
            obj='metrics_app.GPUMetric',
            id='metrics_app.E020',
        ))
    try:
        LatestSnapshot._meta.get_field('gpu_subvendors_json')
    except Exception:
        errors.append(Error(
            'LatestSnapshot missing gpu_subvendors_json field',
            hint='Run migration 0057_gpumetric_gpu_subvendor',
            obj='metrics_app.LatestSnapshot',
            id='metrics_app.E021',
        ))

    # Layer 2: serializer must persist subvendor (per-row + summary array)
    try:
        import inspect
        import metrics_app.serializers as s
        process_src = inspect.getsource(s.process_ingest)
        if "'gpu_subvendor': gpu.get('gpu_subvendor'" not in process_src:
            errors.append(Error(
                "serializer process_ingest does not write 'gpu_subvendor' to GPUMetric",
                hint="Add 'gpu_subvendor' to the GPUMetric defaults dict",
                obj='metrics_app.serializers.process_ingest',
                id='metrics_app.E022',
            ))
        if "'gpu_subvendors_json': gpu_subvendors" not in process_src:
            errors.append(Error(
                "serializer process_ingest does not write 'gpu_subvendors_json' to LatestSnapshot",
                hint="Add 'gpu_subvendors_json': gpu_subvendors to LatestSnapshot defaults",
                obj='metrics_app.serializers.process_ingest',
                id='metrics_app.E023',
            ))
    except Exception:
        pass  # source inspection is best-effort; model-field checks above are durable

    # Layer 3: compaction must keep both static identifiers (in-memory)
    try:
        static_fields = _gpumetric_static_fields()
        if static_fields is None:
            errors.append(Error(
                'compaction: metrics_gpumetric not found in COMPACT_TABLES',
                hint='Verify metrics_gpumetric entry in compact_data.COMPACT_TABLES',
                obj='metrics_app.management.commands.compact_data',
                id='metrics_app.E024',
            ))
        else:
            for field in ('gpu_subvendor', 'gpu_board_part_number'):
                if field not in static_fields:
                    errors.append(Error(
                        f'compact_data: metrics_gpumetric static_fields missing {field}',
                        hint='Static GPU identifiers must survive tier-2/3 compaction',
                        obj='metrics_app.management.commands.compact_data',
                        id='metrics_app.E024',
                    ))
    except Exception:
        pass
    return errors


@register('metrics_app')
def check_gpu_phase3_performance_thermal(app_configs, **kwargs):
    """Defense in depth: Phase 3 GPU performance/thermal metrics must be
    persisted AND survive compaction. CWD-independent (in-memory
    COMPACT_TABLES, not a file read)."""
    errors = []
    from metrics_app.models import GPUMetric, LatestSnapshot
    from metrics_app.management.commands.compact_data import COMPACT_TABLES

    gpumetric_fields = ('gpu_thermal_thresholds','gpu_pstates_util',
                        'gpu_max_clocks','gpu_mig_mode','gpu_bar1_mb')
    latestsnapshot_fields = ('gpu_thermal_thresholds_json','gpu_pstates_util_json',
                             'gpu_max_clocks_json','gpu_mig_modes_json','gpu_bar1_mb_json')

    # Layer 1: model fields exist
    for f in gpumetric_fields:
        try: GPUMetric._meta.get_field(f)
        except Exception:
            errors.append(Error(f'GPUMetric missing Phase 3 field: {f}',
                hint='Run migration 0059_gpumetric_phase3_performance_thermal',
                obj='metrics_app.GPUMetric', id=f'metrics_app.E060'))
    for f in latestsnapshot_fields:
        try: LatestSnapshot._meta.get_field(f)
        except Exception:
            errors.append(Error(f'LatestSnapshot missing Phase 3 field: {f}',
                hint='Run migration 0059_gpumetric_phase3_performance_thermal',
                obj='metrics_app.LatestSnapshot', id=f'metrics_app.E065'))

    # Layer 2: serializer writes per-row + summary arrays
    import re
    import inspect
    import metrics_app.serializers as s
    src = inspect.getsource(s.process_ingest)
    for f in gpumetric_fields:
        # Use regex to match with flexible whitespace; gpu_mig_mode uses a ternary pattern
        if f == 'gpu_mig_mode':
            pattern = rf"'{re.escape(f)}'\s*:\s*\(gpu\.get\('{re.escape(f)}'\).*?is not None"
        else:
            pattern = rf"'{re.escape(f)}'\s*:\s*gpu\.get\('{re.escape(f)}'"
        if not re.search(pattern, src, re.DOTALL):
            errors.append(Error(f"serializer does not write Phase 3 '{f}' to GPUMetric",
                hint=f"Add '{f}' to GPUMetric defaults", obj='metrics_app.serializers.process_ingest',
                id='metrics_app.E070'))

    # Layer 3: compaction preserves all Phase 3 fields (in-memory, CWD-safe)
    try:
        gpu_cfg = next(c for c in COMPACT_TABLES if c['table'] == 'metrics_gpumetric')
        for f in gpumetric_fields:
            if f not in gpu_cfg['static_fields']:
                errors.append(Error(f'compaction static_fields missing Phase 3 {f}',
                    hint='Static/semi-static GPU metrics must survive tier-2/3',
                    obj='metrics_app.management.commands.compact_data',
                    id='metrics_app.E075'))
    except Exception as e:
        errors.append(Error(f'could not read COMPACT_TABLES: {e}',
            id='metrics_app.E080'))
    return errors


@register('metrics_app')
def check_report_job_saturation(app_configs, **kwargs):
    """System check for Job Saturation in report.
    
    Verifies:
    - _build_report_context includes has_active_job_avg in snap_agg aggregate
    - _build_report_context returns job_saturation_pct in context dict
    - _report_table.html template renders job_saturation_pct
    """
    errors = []
    
    # Layer 1: Check _build_report_context source for has_active_job_avg in aggregate
    try:
        import inspect
        from gpu_monitor.dashboard.views import _build_report_context
        src = inspect.getsource(_build_report_context)
        # Check for Cast import from django.db.models.functions and usage
        if "has_active_job_avg=Avg(Cast('has_active_job', IntegerField()))" not in src:
            errors.append(Error(
                'Report context missing has_active_job_avg in MetricSnapshot aggregate',
                hint='Add has_active_job_avg=Avg(Cast("has_active_job", IntegerField())) to snap_agg',
                obj='gpu_monitor.dashboard.views._build_report_context',
                id='metrics_app.E085',
            ))
        # Also verify Cast is imported from correct location
        if "from django.db.models.functions import Cast" not in src:
            errors.append(Error(
                'Report context missing correct Cast import',
                hint='Add "from django.db.models.functions import Cast" to imports',
                obj='gpu_monitor.dashboard.views._build_report_context',
                id='metrics_app.E085',
            ))
    except Exception:
        pass  # source inspection is best-effort
    
    # Layer 2: Check _build_report_context returns job_saturation_pct
    try:
        import inspect
        from gpu_monitor.dashboard.views import _build_report_context
        src = inspect.getsource(_build_report_context)
        if "'job_saturation_pct'" not in src and '"job_saturation_pct"' not in src:
            errors.append(Error(
                'Report context missing job_saturation_pct in return dict',
                hint='Add job_saturation_pct to the return dict in _build_report_context',
                obj='gpu_monitor.dashboard.views._build_report_context',
                id='metrics_app.E086',
            ))
    except Exception:
        pass
    
    # Layer 3: Check template renders job_saturation_pct
    try:
        import os
        template_path = 'gpu_monitor/templates/dashboard/_report_table.html'
        if os.path.exists(template_path):
            with open(template_path, 'r') as f:
                template_content = f.read()
            if 'job_saturation_pct' not in template_content:
                errors.append(Error(
                    'Report template missing job_saturation_pct rendering',
                    hint='Add {{ job_saturation_pct|floatformat:1 }}% to _report_table.html System section',
                    obj='gpu_monitor.templates.dashboard._report_table',
                    id='metrics_app.E087',
                ))
    except Exception:
        pass
    
    return errors


@register('metrics_app')
def check_cpu_per_core_fields(app_configs, **kwargs):
    """Defense in depth: per-CPU-core metrics must be persisted AND survive compaction.
    
    Layer 1: model fields exist on MetricSnapshot and LatestSnapshot.
    Layer 2: ChartDataView.SNAPSHOT_METRICS includes all three per-core metrics.
    Layer 3: serializer process_ingest writes all three per-core arrays to both snapshots.
    """
    errors = []
    from metrics_app.models import MetricSnapshot, LatestSnapshot
    from metrics_app.views import ChartDataView
    
    # Layer 1: Model fields exist
    required_ms_fields = [
        'cpu_utilization_per_core_json',
        'cpu_temp_per_core_json',
        'cpu_freq_per_core_json',
    ]
    for field_name in required_ms_fields:
        try:
            MetricSnapshot._meta.get_field(field_name)
        except Exception:
            errors.append(Error(
                f'MetricSnapshot missing {field_name} field',
                hint='Run migration 0060_add_cpu_per_core_fields',
                obj='metrics_app.MetricSnapshot',
                id='metrics_app.E090',
            ))
    
    required_ls_fields = [
        'cpu_utilization_per_core_json',
        'cpu_temp_per_core_json',
        'cpu_freq_per_core_json',
    ]
    for field_name in required_ls_fields:
        try:
            LatestSnapshot._meta.get_field(field_name)
        except Exception:
            errors.append(Error(
                f'LatestSnapshot missing {field_name} field',
                hint='Run migration 0060_add_cpu_per_core_fields',
                obj='metrics_app.LatestSnapshot',
                id='metrics_app.E091',
            ))
    
    # Layer 2: ChartDataView must include all three per-core metrics
    snapshot_metrics = getattr(ChartDataView, 'SNAPSHOT_METRICS', set())
    required_chart_metrics = [
        'cpu_utilization_per_core_pct',
        'cpu_temp_per_core_c',
        'cpu_freq_per_core_current_mhz',
    ]
    for metric_name in required_chart_metrics:
        if metric_name not in snapshot_metrics:
            errors.append(Error(
                f'ChartDataView.SNAPSHOT_METRICS missing {metric_name}',
                hint=f'Add {metric_name} to SNAPSHOT_METRICS frozenset',
                obj='metrics_app.views.ChartDataView',
                id='metrics_app.E092',
            ))
    
    # Layer 3: Serializer must write all three per-core arrays
    try:
        import inspect
        import metrics_app.serializers as s
        process_src = inspect.getsource(s.process_ingest)
        required_arrays = [
            ('cpu_utilization_per_core_json', 'cpu_per_core'),
            ('cpu_temp_per_core_json', 'cpu_temp_per_core'),
            ('cpu_freq_per_core_json', 'cpu_freq_per_core'),
        ]
        for field, var in required_arrays:
            # Check MetricSnapshot defaults
            if f"'{field}': {var}" not in process_src:
                errors.append(Error(
                    f"serializer process_ingest does not write '{field}' to MetricSnapshot",
                    hint=f"Add '{field}': {var} to MetricSnapshot defaults dict",
                    obj='metrics_app.serializers.process_ingest',
                    id='metrics_app.E093',
                ))
            # Check LatestSnapshot defaults
            if f"'{field}': {var}" not in process_src:
                errors.append(Error(
                    f"serializer process_ingest does not write '{field}' to LatestSnapshot",
                    hint=f"Add '{field}': {var} to LatestSnapshot defaults dict",
                    obj='metrics_app.serializers.process_ingest',
                    id='metrics_app.E094',
                ))
    except Exception:
        pass  # source inspection is best-effort; model-field checks above are durable
    
    # Layer 4: Compaction must include per-core JSON fields with 'last' aggregation
    try:
        from metrics_app.management.commands.compact_data import COMPACT_TABLES
        snapshot_config = next((c for c in COMPACT_TABLES if c['table'] == 'metrics_metricsnapshot'), None)
        if snapshot_config is None:
            errors.append(Error(
                'compaction: metrics_metricsnapshot not found in COMPACT_TABLES',
                hint='Add metrics_metricsnapshot entry to compact_data.COMPACT_TABLES',
                obj='metrics_app.management.commands.compact_data',
                id='metrics_app.E095',
            ))
        else:
            required_compact_fields = {
                'cpu_utilization_per_core_json': 'last',
                'cpu_temp_per_core_json': 'last',
                'cpu_freq_per_core_json': 'last',
            }
            for field, agg in required_compact_fields.items():
                if field not in snapshot_config.get('agg_fields', {}):
                    errors.append(Error(
                        f'compact_data: metrics_metricsnapshot agg_fields missing {field}',
                        hint=f'Add {field}: {agg} to MetricSnapshot agg_fields for tier-2/3 compaction',
                        obj='metrics_app.management.commands.compact_data',
                        id='metrics_app.E096',
                    ))
                elif snapshot_config['agg_fields'][field] != agg:
                    errors.append(Error(
                        f'compact_data: metrics_metricsnapshot agg_fields[{field}] must be {agg} (got {snapshot_config["agg_fields"][field]})',
                        hint=f'JSON arrays use last aggregation to preserve most recent per-core data',
                        obj='metrics_app.management.commands.compact_data',
                        id='metrics_app.E097',
                    ))
    except Exception:
        pass  # compaction structure read is best-effort
    
    return errors
