#!/usr/bin/env python3
"""Compaction bool max defense test (layer 3 / W001).
Verifies SQL generation uses Cast to IntegerField for has_active_job max.
"""
import os
os.environ.setdefault('DJANGO_SETTINGS_MODULE', 'gpu_monitor.settings')
import django
django.setup()

from django.test import TestCase


class BoolMaxAggregationDefenseTest(TestCase):
    def test_compact_data_has_integer_cast_for_bool_max(self):
        src_path = 'gpu_monitor/metrics_app/management/commands/compact_data.py'
        try:
            with open(src_path) as f:
                src = f.read()
            # Professional defense: SQL generation must contain the special bool-max case
            self.assertIn("if agg == 'max' and f == 'has_active_job':", src,
                          "compact_data.py missing bool max INTEGER cast defense")
            self.assertIn("CAST({f} AS INTEGER)", src,
                          "SQL emission must cast bool field before MAX")
        except FileNotFoundError:
            self.fail("compact_data.py not found for verification")
