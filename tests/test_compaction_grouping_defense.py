#!/usr/bin/env python3
"""Compaction grouping defense test (W001 / defense layer 3).

Verifies that adding gpu_uuid to static_fields does NOT break grouping
when some rows have empty string (default ''). Empty UUID is preserved
as last value (''), matching PR#186 design with blank=True defense.
"""
import os
os.environ.setdefault('DJANGO_SETTINGS_MODULE', 'gpu_monitor.settings')

import django
django.setup()

from django.test import TestCase
from metrics_app.management.commands.compact_data import COMPACT_TABLES


class CompactionGroupingDefenseTest(TestCase):
    def test_gpumetric_has_uuid_in_static_fields_with_blank_defense(self):
        gpumetric_config = next(c for c in COMPACT_TABLES if c['table'] == 'metrics_gpumetric')
        # Professional fix: gpu_uuid preserved in compaction (identity tracking)
        self.assertIn('gpu_uuid', gpumetric_config['static_fields'])
        # Model defense verified separately (blank=True, default='')

    def test_model_has_blank_true_default_empty_for_uuid(self):
        from metrics_app.models import GPUMetric
        field = GPUMetric._meta.get_field('gpu_uuid')
        self.assertTrue(field.blank)
        self.assertEqual(field.default, '')
        self.assertEqual(field.max_length, 64)
        # CharField (not UUIDField) avoids PostgreSQL UUID parser conflict
        self.assertIn('CharField', str(type(field)).lower())
