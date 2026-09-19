#!/usr/bin/env python3
"""System-check layer 3: bucket defense (problem 1 from professional-prevention.md).
Verifies ChartDataView._build_buckets does NOT extend end_bucket past now + 1.
"""
import os
os.environ.setdefault('DJANGO_SETTINGS_MODULE', 'gpu_monitor.settings')
import django
django.setup()

from datetime import timedelta
from django.test import TestCase
from django.utils import timezone
from metrics_app.views import ChartDataView


class ChartBucketDefenseTest(TestCase):
    def test_end_bucket_not_extended_past_now(self):
        v = ChartDataView()
        labels, start, end = v._build_buckets(range_hours=24, bucket_minutes=1)
        # Professional defense: end_bucket must not include future partial bucket
        # (matches PR#185 design — only include current minute, not +1)
        now = timezone.now()
        delta_to_now = (end - now).total_seconds()
        # Allow small processing lag; must not exceed 60s (one bucket)
        self.assertLessEqual(delta_to_now, 60, f"end_bucket extended too far past now (+{delta_to_now}s)")
