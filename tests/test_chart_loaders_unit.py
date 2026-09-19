#!/usr/bin/env python3
"""Loader unit tests — verifies loadChartMultiGpu contract (self-contained, Feature 1)."""
import unittest

# Minimal mock of ChartBase / Colors to test loader contract
class MockColors:
    GPU_COLORS = [{'border': '#ff0000', 'bg': '#ff000030'}]

class MockChartBase:
    STYLE = type('S', (), {
        'borderWidth': 2, 'pointRadius': 0, 'pointHitRadius': 10,
        'tension': 0.0, 'lineSpanGaps': False
    })()
    @staticmethod
    def baseOptions(opts): return opts
    @staticmethod
    def legendOptions(opts): return opts

class ChartLoaderContractTest(unittest.TestCase):
    def test_generateLabels_truncation_exists(self):
        # Read current loader file; verify generateLabels present (not removed)
        with open('gpu_monitor/static/js/chart-loaders.js') as f:
            src = f.read()
        self.assertIn('generateLabels', src)
        self.assertIn('substring(0, 12)', src)  # UUID truncation preserved

    def test_no_fatal_validation_guard(self):
        # PR#186 added fatal throw; must NOT exist
        with open('gpu_monitor/static/js/chart-loaders.js') as f:
            src = f.read()
        # Only original guard uses .datasets[0].data (line 69 style)
        # The PR186 guard used `data.datasets.length === 0` (should be absent)
        self.assertNotIn('data.datasets.length === 0', src)

    def test_loadChartMultiGpu_defined(self):
        with open('gpu_monitor/static/js/chart-loaders.js') as f:
            src = f.read()
        self.assertIn('function loadChartMultiGpu', src)

if __name__ == '__main__':
    unittest.main()
