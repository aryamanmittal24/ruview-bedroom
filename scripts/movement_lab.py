"""Two-node, room-wide CSI movement calibration. No position inference."""
from __future__ import annotations

import json
import math
import os
import statistics
import time
from pathlib import Path
import movement_learning

SIGNAL_DIR = Path('/tmp')
BASELINE_FILE = Path.home() / '.local/state/ruview/movement-baseline.json'
NODE_IDS = ('1', '2')
SIGNAL_MAX_AGE_S = 3.0


def _signal(node_id: str) -> dict | None:
    try:
        data = json.loads((SIGNAL_DIR / f'ruview-node-{node_id}-signal.json').read_text())
        age = time.time() - float(data['ts'])
        change = float(data['change'])
        if data.get('node_id') != int(node_id) or not 0 <= age <= SIGNAL_MAX_AGE_S:
            return None
        if not math.isfinite(change) or change < 0:
            return None
        return {'change': change, 'ts': float(data['ts']), 'sample_count': int(data.get('sample_count', 0)),
                'rssi': float(data.get('mean_rssi', 0)), 'age_s': round(age, 2)}
    except (OSError, ValueError, TypeError, KeyError):
        return None


def load_baseline() -> dict | None:
    try:
        data = json.loads(BASELINE_FILE.read_text())
        if data.get('version') not in (1, 2) or set(data.get('nodes', {})) != set(NODE_IDS):
            return None
        for node in data['nodes'].values():
            if not 0 <= float(node['median']) < float(node['threshold']) < 1000:
                return None
        return data
    except (OSError, ValueError, TypeError, KeyError):
        return None


def live() -> dict:
    legacy = load_baseline()
    model = movement_learning.get_model(legacy)
    baseline = {'nodes': model['nodes'], 'version': 3, 'created_at': model['created_at']} if model['nodes'] else None
    nodes = []
    triggered = []
    for node_id in NODE_IDS:
        signal = _signal(node_id)
        reference = baseline['nodes'].get(node_id) if baseline else None
        node = {'id': node_id, 'label': 'Shared-wall node' if node_id == '1' else 'Upper-right desk node',
                'online': signal is not None, 'signal': signal,
                'baseline': reference}
        if signal and reference:
            node['above_threshold'] = signal['change'] >= reference['threshold']
            node['relative_level'] = round(signal['change'] / reference['threshold'], 2)
            node['confirmed_movement'] = movement_learning.confirmed(node_id, signal, reference['threshold'], model['revision'])
            if node['confirmed_movement']:
                triggered.append(node_id)
        else:
            movement_learning.confirmed(node_id, None, 0, model['revision'])
        nodes.append(node)
    online = sum(node['online'] for node in nodes)
    if not baseline:
        movement = 'needs_calibration'
    elif online < 2:
        movement = 'degraded'
    elif len(triggered) == 2:
        movement = 'corroborated_change'
    elif len(triggered) == 1:
        movement = 'single_node_change'
    else:
        movement = 'near_baseline'
    return {'nodes': nodes, 'online_count': online, 'movement': movement,
            'profile_version': baseline['version'] if baseline else None,
            'model_revision': model['revision'],
            'confirmation': 'At least 2 of the last 3 fresh readings per node',
            'calibrated_at': baseline['created_at'] if baseline else None,
            'checked_at': time.time(),
            'note': 'Signal change is not a person count or a location estimate.'}


def calibrate(samples: dict) -> dict:
    if not isinstance(samples, dict) or set(samples) != set(NODE_IDS):
        raise ValueError('Collect empty-room samples from both nodes')
    if any(_signal(node_id) is None for node_id in NODE_IDS):
        raise ValueError('Both nodes must be live when calibration finishes')
    nodes = {}
    for node_id in NODE_IDS:
        values = samples[node_id]
        if not isinstance(values, list) or not 20 <= len(values) <= 60:
            raise ValueError('Need 20–60 one-second samples per node')
        if any(type(value) not in (int, float) or not math.isfinite(value)
               or not 0 <= value <= 500 for value in values):
            raise ValueError('Invalid signal sample')
        ordered = sorted(values)
        median = statistics.median(ordered)
        mad = statistics.median(abs(value - median) for value in ordered)
        p95 = ordered[math.ceil(.95 * len(ordered)) - 1]
        threshold = max(median + max(3 * mad, 2.0), p95 * 1.25)
        nodes[node_id] = {'median': round(median, 3), 'mad': round(mad, 3),
                          'p95': round(p95, 3), 'threshold': round(threshold, 3),
                          'sample_count': len(values)}
    result = {'version': 1, 'created_at': time.time(), 'nodes': nodes,
              'method': '30-second empty-room median/MAD/p95; user-confirmed room state'}
    BASELINE_FILE.parent.mkdir(parents=True, exist_ok=True)
    os.chmod(BASELINE_FILE.parent, 0o700)
    temporary = BASELINE_FILE.with_suffix('.tmp')
    fd = os.open(temporary, os.O_CREAT | os.O_WRONLY | os.O_TRUNC, 0o600)
    with os.fdopen(fd, 'w') as file:
        json.dump(result, file)
    os.replace(temporary, BASELINE_FILE)
    os.chmod(BASELINE_FILE, 0o600)
    return result


def calibrate_profile(trials: dict) -> dict:
    """Fit an exploratory threshold from repeated quiet and movement samples.

    These trials are training data, not an independent validation set.
    """
    if not isinstance(trials, dict) or set(trials) != {'quiet', 'desk_walk', 'foot_walk'}:
        raise ValueError('Need quiet, desk_walk and foot_walk trials')
    if any(_signal(node_id) is None for node_id in NODE_IDS):
        raise ValueError('Both nodes must be live when calibration finishes')
    nodes = {}
    for node_id in NODE_IDS:
        quiet_runs = trials['quiet'].get(node_id)
        desk_runs = trials['desk_walk'].get(node_id)
        foot_runs = trials['foot_walk'].get(node_id)
        for runs, name, count in ((quiet_runs, 'quiet', 2), (desk_runs, 'desk walk', 1),
                                  (foot_runs, 'foot walk', 1)):
            if not isinstance(runs, list) or len(runs) != count:
                raise ValueError(f'Need {count} {name} trial(s) for node {node_id}')
            for run in runs:
                if not isinstance(run, list) or not 12 <= len(run) <= 40 or any(
                    type(v) not in (int, float) or not math.isfinite(v) or not 0 <= v <= 1000
                    for v in run
                ):
                    raise ValueError(f'Invalid {name} samples for node {node_id}')
        quiet = sorted(value for run in quiet_runs for value in run)
        walks = [value for run in desk_runs + foot_runs for value in run]
        median = statistics.median(quiet)
        mad = statistics.median(abs(value - median) for value in quiet)
        p95 = quiet[math.ceil(.95 * len(quiet)) - 1]
        # The quiet distribution controls false triggers; labelled walking is
        # validation rather than a reason to force a lower, noisy threshold.
        threshold = max(p95, median + 2.5 * mad) + max(.5, .05 * median)
        quiet_false_rate = sum(v >= threshold for v in quiet) / len(quiet)
        desk_rate = sum(v >= threshold for v in desk_runs[0]) / len(desk_runs[0])
        foot_rate = sum(v >= threshold for v in foot_runs[0]) / len(foot_runs[0])
        nodes[node_id] = {
            'median': round(median, 3), 'mad': round(mad, 3), 'p95': round(p95, 3),
            'threshold': round(threshold, 3), 'sample_count': len(quiet),
            'quiet_false_rate': round(quiet_false_rate, 3),
            'desk_walk_detection_rate': round(desk_rate, 3),
            'foot_walk_detection_rate': round(foot_rate, 3),
            'quality': 'needs_validation' if quiet_false_rate <= .1 and max(desk_rate, foot_rate) >= .2 else 'weak',
        }
    result = {'version': 2, 'created_at': time.time(), 'nodes': nodes,
              'method': '2 empty-room trials + upper-right desk walk + shared-wall walk; exploratory quiet threshold',
              'layout': 'node1_shared_wall_node2_upper_right_desk'}
    BASELINE_FILE.parent.mkdir(parents=True, exist_ok=True)
    os.chmod(BASELINE_FILE.parent, 0o700)
    temporary = BASELINE_FILE.with_suffix('.tmp')
    fd = os.open(temporary, os.O_CREAT | os.O_WRONLY | os.O_TRUNC, 0o600)
    with os.fdopen(fd, 'w') as file:
        json.dump(result, file)
    os.replace(temporary, BASELINE_FILE)
    os.chmod(BASELINE_FILE, 0o600)
    return result
