from __future__ import annotations

import subprocess
import sys
from pathlib import Path


def test_fixed_viewport_tracking_does_not_import_scipy():
    root = Path(__file__).resolve().parents[1]
    script = '''
import importlib.abc
import runpy
import sys

class RejectScipy(importlib.abc.MetaPathFinder):
    def find_spec(self, fullname, path=None, target=None):
        if fullname == 'scipy' or fullname.startswith('scipy.'):
            raise AssertionError('fixed-viewport tracking must not import scipy')

sys.meta_path.insert(0, RejectScipy())
fixture = runpy.run_path('tests/test_tracking_analysis.py')
payload = fixture['_payload']()
payload['player_motion_status'] = 'unavailable_fixed_viewport_center'
result = fixture['analyze_continuous_tracking_v1'](payload)
assert result['metrics']['continuous_tracking.time_in_radius_ratio']['value'] == 1.0
assert result['metrics']['continuous_tracking.target_relative_error_px']['availability'] == 'available'
assert result['metrics']['continuous_tracking.coherence']['availability'] == 'unavailable'
assert not any(name == 'scipy' or name.startswith('scipy.') for name in sys.modules)
'''
    result = subprocess.run(
        [sys.executable, '-B', '-c', script],
        cwd=root,
        stdin=subprocess.DEVNULL,
        capture_output=True,
        text=True,
        timeout=20,
    )
    assert result.returncode == 0, result.stdout + result.stderr
