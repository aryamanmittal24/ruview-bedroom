"""Append-only labelled trials, balanced threshold fitting and prospective checks.

Uses only Python's standard library. Trials stay local. Check trials never fit
thresholds. Training metrics are explicitly distinct from future check metrics.
"""
import json
import math
import os
import re
import sqlite3
import statistics
import threading
import time
from collections import deque
from pathlib import Path

DB_FILE = Path.home() / '.local/state/ruview/movement-trials.sqlite3'
LABELS = ('still', 'empty', 'walk_node1', 'walk_node2', 'walk_room')
NEGATIVE = ('still', 'empty')
LOCK = threading.RLock()
RECENT = {}


def connect():
    DB_FILE.parent.mkdir(parents=True, exist_ok=True)
    db = sqlite3.connect(DB_FILE, timeout=10)
    os.chmod(DB_FILE, 0o600)
    db.row_factory = sqlite3.Row
    db.executescript('''
        CREATE TABLE IF NOT EXISTS settings (key TEXT PRIMARY KEY, value TEXT NOT NULL);
        CREATE TABLE IF NOT EXISTS trials (
            id TEXT PRIMARY KEY, created REAL NOT NULL, label TEXT NOT NULL,
            purpose TEXT NOT NULL, samples TEXT NOT NULL, excluded INTEGER NOT NULL DEFAULT 0,
            revision INTEGER NOT NULL, score REAL, outcome TEXT NOT NULL
        );
    ''')
    return db


def setting(db, key, default=None):
    row = db.execute('SELECT value FROM settings WHERE key=?', (key,)).fetchone()
    return json.loads(row[0]) if row else default


def save_setting(db, key, value):
    db.execute('INSERT OR REPLACE INTO settings VALUES (?, ?)', (key, json.dumps(value)))


def initial_model(legacy):
    return {'revision': 0, 'nodes': legacy['nodes'] if legacy else {},
            'created_at': legacy['created_at'] if legacy else None,
            'method': 'saved baseline with 2-of-3 confirmation', 'training': None}


def model_for(db, legacy):
    return setting(db, 'model', initial_model(legacy))


def responses(samples, nodes):
    """Replay exactly the live 2-of-3 confirmation, independently per trial."""
    history = {n: deque(maxlen=3) for n in ('1', '2')}
    result = []
    for sample in samples:
        active = []
        for n in history:
            history[n].append(sample['values'][n] >= nodes[n]['threshold'])
            active.append(sum(history[n]) >= 2)
        if len(history['1']) == 3:
            result.append(any(active))
    return result


def metrics(trials, nodes):
    grouped = {}
    for trial in trials:
        flags = responses(trial['samples'], nodes)
        grouped.setdefault(trial['label'], []).append(sum(flags) / len(flags))
    rates = {label: statistics.mean(rates) for label, rates in grouped.items()}
    negatives = [value for label, value in rates.items() if label in NEGATIVE]
    positives = [value for label, value in rates.items() if label not in NEGATIVE]
    return {'by_condition': rates, 'false_trigger_rate': max(negatives, default=0),
            'walk_response_rate': statistics.mean(positives) if positives else None,
            'basis': 'training trials; not independent accuracy'}


def fit(trials, fallback):
    quiet = [t for t in trials if t['label'] in NEGATIVE]
    walks = [t for t in trials if t['label'] not in NEGATIVE]
    if not quiet or not walks:
        return None
    candidates = {}
    for n in ('1', '2'):
        values = sorted(s['values'][n] for t in quiet for s in t['samples'])
        median = statistics.median(values)
        candidates[n] = []
        # Quantile candidates span sensitivity and a conservative no-trigger
        # candidate. Trials and conditions are balanced in the objective.
        for q in (.75, .85, .95, .99, 1.0):
            threshold = values[max(0, math.ceil(q * len(values)) - 1)] + .01
            candidates[n].append({'median': median, 'threshold': threshold,
                                   'sample_count': len(values)})
        if n in fallback['nodes']:
            candidates[n].append(fallback['nodes'][n])
    best = None
    for first in candidates['1']:
        for second in candidates['2']:
            nodes = {'1': first, '2': second}
            measured = metrics(trials, nodes)
            false_rate = measured['false_trigger_rate']
            # Under the 5% training false-trigger budget, maximize balanced
            # walking response. Prefer fewer false triggers on ties.
            rank = (false_rate <= .05, measured['walk_response_rate'] if false_rate <= .05 else -false_rate,
                    -false_rate, first['threshold'] + second['threshold'])
            if best is None or rank > best[0]:
                best = (rank, nodes, measured)
    return {'nodes': best[1], 'training': best[2], 'created_at': time.time(),
            'method': 'condition-balanced threshold search + 2-of-3 confirmation'}


def active_trials(db):
    return [dict(row, samples=json.loads(row['samples'])) for row in db.execute(
        "SELECT * FROM trials WHERE excluded=0 AND purpose='learn' ORDER BY created")]


def refit(db, legacy):
    old = model_for(db, legacy)
    seed = setting(db, 'seed', initial_model(legacy))
    candidate = fit(active_trials(db), seed) or dict(seed)
    if candidate['nodes'] == old['nodes'] and candidate.get('training') == old.get('training'):
        return old
    candidate['revision'] = old['revision'] + 1
    save_setting(db, 'model', candidate)
    return candidate


def validate_payload(payload, now):
    if not isinstance(payload, dict):
        raise ValueError('Invalid trial')
    trial_id = payload.get('id', '')
    if not isinstance(trial_id, str) or not re.fullmatch(r'[a-zA-Z0-9-]{12,80}', trial_id):
        raise ValueError('Invalid trial identifier')
    if payload.get('label') not in LABELS or payload.get('purpose') not in ('learn', 'check'):
        raise ValueError('Choose a condition and Learn or Check')
    samples = payload.get('samples')
    if not isinstance(samples, list) or not 20 <= len(samples) <= 60:
        raise ValueError('A trial requires 20–60 fresh readings')
    previous = None
    for sample in samples:
        if not isinstance(sample, dict) or set(sample.get('values', {})) != {'1', '2'} or set(sample.get('timestamps', {})) != {'1', '2'}:
            raise ValueError('Both nodes are required in every reading')
        for n in ('1', '2'):
            value, ts = sample['values'][n], sample['timestamps'][n]
            if type(value) not in (int, float) or not math.isfinite(value) or not 0 <= value <= 1000:
                raise ValueError('Invalid signal value')
            if type(ts) not in (int, float) or not math.isfinite(ts) or not now - 86400 <= ts <= now + 2:
                raise ValueError('Trial readings are too old; capture again')
            if previous and not .5 <= ts - previous['timestamps'][n] <= 3.5:
                raise ValueError('Duplicate readings or a stream gap; capture again')
        if abs(sample['timestamps']['1'] - sample['timestamps']['2']) > 3.5:
            raise ValueError('Node readings are not aligned in time')
        previous = sample
    if max(samples[-1]['timestamps'].values()) - min(samples[0]['timestamps'].values()) > 180:
        raise ValueError('Trial duration is too long')
    return trial_id


def add_trial(payload, legacy):
    with LOCK:
        db = connect()
        try:
            # Idempotent retry: never count a submitted trial twice.
            if isinstance(payload, dict) and isinstance(payload.get('id'), str) and db.execute('SELECT 1 FROM trials WHERE id=?', (payload.get('id'),)).fetchone():
                return status(db, legacy)
            trial_id = validate_payload(payload, time.time())
            current = model_for(db, legacy)
            if payload.get('revision') != current['revision']:
                raise ValueError('Detector changed during capture. Retry this trial.')
            if not current['nodes'] and payload['purpose'] == 'check':
                raise ValueError('Add a still and a walking learning trial first')
            samples = payload['samples']
            flags = responses(samples, current['nodes']) if current['nodes'] else []
            score = sum(flags) / len(flags) if flags else None
            if setting(db, 'seed') is None:
                save_setting(db, 'seed', initial_model(legacy))
                save_setting(db, 'model', current)
            db.execute('INSERT INTO trials VALUES (?,?,?,?,?,0,?,?,?)', (
                trial_id, max(samples[-1]['timestamps'].values()), payload['label'], payload['purpose'], json.dumps(samples),
                current['revision'], score, 'Scored before learning' if flags else 'No prior detector'))
            if payload['purpose'] == 'learn':
                refit(db, legacy)
            db.commit()
            return status(db, legacy)
        finally:
            db.close()


def exclude_trial(trial_id, legacy):
    if not isinstance(trial_id, str):
        raise ValueError('Invalid trial identifier')
    with LOCK:
        db = connect()
        try:
            row = db.execute('SELECT * FROM trials WHERE id=?', (trial_id,)).fetchone()
            if row is None:
                raise ValueError('Trial not found')
            if not row['excluded']:
                db.execute('UPDATE trials SET excluded=1 WHERE id=?', (trial_id,))
                if row['purpose'] == 'learn':
                    refit(db, legacy)
                db.commit()
            return status(db, legacy)
        finally:
            db.close()


def status(db, legacy):
    model = model_for(db, legacy)
    rows = [dict(r) for r in db.execute(
        'SELECT id,created,label,purpose,excluded,revision,score,outcome FROM trials ORDER BY created DESC')]
    counts = {label: sum(t['label'] == label and not t['excluded'] and t['purpose'] == 'learn' for t in rows) for label in LABELS}
    checks = [t for t in rows if not t['excluded'] and t['purpose'] == 'check' and t['revision'] == model['revision']]
    check_rates = {}
    for label in LABELS:
        scores = [t['score'] for t in checks if t['label'] == label and t['score'] is not None]
        check_rates[label] = {'trials': len(scores), 'response_rate': statistics.mean(scores) if scores else None}
    return {'model': model, 'counts': counts, 'total_trials': len(rows), 'history': rows[:30],
            'checks': check_rates,
            'guidance': ('Add a still or empty-room learning trial when convenient. Your saved baseline is still used.'
                         if not counts['still'] and not counts['empty'] else
                         'Add a walking learning trial to fit the detector.' if not any(counts[k] for k in LABELS if k not in NEGATIVE) else
                         'Use Check mode for fresh trials that measure this detector without changing it.'),
            'legacy_summary_only': bool(legacy)}


def get_status(legacy):
    with LOCK:
        db = connect()
        try:
            return status(db, legacy)
        finally:
            db.close()


def get_model(legacy):
    with LOCK:
        db = connect()
        try:
            return model_for(db, legacy)
        finally:
            db.close()


def confirmed(node_id, signal, threshold, revision):
    """Deduplicate polls; clear history on signal loss, model change or gaps."""
    with LOCK:
        state = RECENT.get(node_id)
        if signal is None:
            RECENT.pop(node_id, None)
            return False
        ts = signal['ts']
        if state is None or state['revision'] != revision or ts - state['ts'] > 3.5 or ts < state['ts']:
            state = {'revision': revision, 'ts': 0, 'flags': deque(maxlen=3)}
            RECENT[node_id] = state
        if ts > state['ts']:
            state['flags'].append(signal['change'] >= threshold)
            state['ts'] = ts
        return sum(state['flags']) >= 2
