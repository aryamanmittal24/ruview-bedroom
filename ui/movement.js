const $ = (selector) => document.querySelector(selector);
const sleep = (ms) => new Promise(resolve => setTimeout(resolve, ms));
const labels = {still: 'Sitting still', empty: 'Empty room', walk_node1: 'Walk near Node 1', walk_node2: 'Walk near Node 2', walk_room: 'Walk around room'};
const instructions = {
  still: 'Sit still for 20 seconds. This teaches normal variation while you are seated; it does not represent an empty room.',
  empty: 'You have 10 seconds to leave, then stay outside for 20 seconds. Keep fan/AC and door in their usual state.',
  walk_node1: 'After the 5-second countdown, walk naturally along the bedroom side of the shared TV wall for 20 seconds.',
  walk_node2: 'After the 5-second countdown, walk naturally near the upper-right desk for 20 seconds.',
  walk_room: 'After the 5-second countdown, walk your usual route around the room for 20 seconds.'
};
const history = {'1': [], '2': []};
const lastGraphTs = {'1': 0, '2': 0};
let latest = null, learning = null, task = null, pending = null;
try { pending = JSON.parse(sessionStorage.getItem('ruview-pending-trial') || 'null'); } catch { /* Ignore a corrupt local draft. */ }

async function request(url, body) {
  const response = await fetch(url, {cache: 'no-store', signal: AbortSignal.timeout(6000),
    ...(body ? {method: 'POST', headers: {'Content-Type': 'application/json'}, body: JSON.stringify(body)} : {})});
  const result = await response.json();
  if (!response.ok) throw new Error(result.error || `Server error ${response.status}`);
  return result;
}

function controls() {
  $('#start-trial').disabled = !!task || !!pending || latest?.online_count !== 2;
  $('#trial-label').disabled = !!task || !!pending;
  $('#trial-purpose').disabled = !!task || !!pending;
  $('#cancel-trial').hidden = task?.kind !== 'capture';
  $('#retry-trial').hidden = !pending;
  $('#discard-trial').hidden = !pending;
  $('#retry-trial').disabled = !!task;
  $('#discard-trial').disabled = !!task;
  document.querySelectorAll('[data-exclude]').forEach(button => { button.disabled = !!task || !!pending; });
}

function plot(canvas, values, threshold) {
  const ctx = canvas.getContext('2d'), {width, height} = canvas;
  ctx.clearRect(0, 0, width, height);
  if (!values.length) return;
  const top = Math.max(threshold || 0, ...values, 1) * 1.2;
  if (threshold) {
    ctx.strokeStyle = '#b5bd7280'; ctx.setLineDash([5, 5]);
    const y = height - threshold / top * height;
    ctx.beginPath(); ctx.moveTo(0, y); ctx.lineTo(width, y); ctx.stroke(); ctx.setLineDash([]);
  }
  ctx.strokeStyle = '#8fe8b1'; ctx.lineWidth = 2; ctx.beginPath();
  values.forEach((value, index) => {
    const x = index / Math.max(values.length - 1, 1) * width, y = height - value / top * (height - 4) - 2;
    if (index === 0) ctx.moveTo(x, y); else ctx.lineTo(x, y);
  }); ctx.stroke();
}

function renderLive(data) {
  latest = data;
  const states = {needs_calibration: 'Calibration needed', degraded: 'Sensor stream incomplete',
    corroborated_change: 'Confirmed: both paths', single_node_change: 'Confirmed: one path', near_baseline: 'No confirmed movement'};
  $('#overall').textContent = states[data.movement] || 'Checking sensors';
  $('#overall').classList.toggle('warn', data.online_count < 2 || data.movement === 'needs_calibration');
  for (const node of data.nodes) {
    const card = $(`#node-${node.id}`);
    card.querySelector('.pill').textContent = node.online ? 'Live' : 'Offline';
    card.querySelector('.pill').className = `pill ${node.online ? 'online' : 'offline'}`;
    card.querySelector('.change').textContent = node.signal ? node.signal.change.toFixed(1) : '—';
    card.querySelector('.threshold').textContent = node.baseline ? `Threshold ${node.baseline.threshold.toFixed(1)} · ${node.confirmed_movement ? 'confirmed change' : 'waiting for repeated change'}` : 'No baseline yet';
    card.querySelector('.rssi').textContent = node.signal ? `${node.signal.rssi.toFixed(0)} dBm` : 'No recent stream';
    card.querySelector('.meter i').style.width = node.signal ? `${Math.min(100, node.signal.change / (node.baseline?.threshold || 20) * 80)}%` : '0%';
    if (node.signal && node.signal.ts > lastGraphTs[node.id]) {
      history[node.id].push(node.signal.change); history[node.id] = history[node.id].slice(-45); lastGraphTs[node.id] = node.signal.ts;
    }
    plot(card.querySelector('canvas'), history[node.id], node.baseline?.threshold);
  }
  controls();
}

const percent = value => `${Math.round(value * 100)}%`;
function renderLearning(data) {
  learning = data;
  $('#calibration-state').textContent = `Detector revision ${data.model.revision} · ${data.total_trials} saved trials`;
  $('#learning-guidance').textContent = data.guidance;
  $('#learning-counts').textContent = Object.entries(data.counts).map(([label, count]) => `${labels[label]}: ${count}`).join(' · ');
  const training = data.model.training;
  $('#training-summary').textContent = training ? `Training examples: ${percent(training.walk_response_rate)} walking response; ${percent(training.false_trigger_rate)} false-trigger readings. These are fitting results, not independent accuracy.` : 'Saved thresholds are the starting point. Add both still and walking examples to tune them.';
  const checks = Object.entries(data.checks).filter(([, value]) => value.trials);
  $('#check-summary').textContent = checks.length ? `Checks for this detector: ${checks.map(([label, value]) => `${labels[label]} — ${percent(value.response_rate)} ${['still', 'empty'].includes(label) ? 'false-trigger' : 'movement-response'} readings across ${value.trials} trial(s)`).join('; ')}.` : 'No independent checks for this detector yet. Select Check to measure it without learning from that trial.';
  const container = $('#trial-history'); container.replaceChildren();
  for (const row of data.history) {
    const entry = document.createElement('div'); entry.className = 'trial-row';
    const description = document.createElement('span');
    description.textContent = `${new Date(row.created * 1000).toLocaleTimeString()} · ${labels[row.label]} · ${row.purpose === 'learn' ? 'Learning' : 'Check'}${row.excluded ? ' · Excluded' : ''} — ${row.score === null ? 'No previous detector to score' : `${percent(row.score)} ${['still', 'empty'].includes(row.label) ? 'false-trigger' : 'response'} readings on revision ${row.revision}, before any learning`}`;
    entry.append(description);
    if (!row.excluded) {
      const button = document.createElement('button'); button.type = 'button'; button.className = 'secondary'; button.dataset.exclude = row.id;
      button.textContent = 'Exclude trial'; button.addEventListener('click', () => excludeTrial(row.id)); entry.append(button);
    }
    container.append(entry);
  }
  if (!data.history.length) container.textContent = 'Your next trial starts the cumulative history. Existing thresholds are retained.';
  controls();
}

async function refreshLearning() { renderLearning(await request('/api/v1/movement/learning')); }
async function poll() {
  try { renderLive(await request('/api/v1/movement/live')); }
  catch { latest = null; $('#overall').textContent = 'Connection unavailable'; controls(); }
  setTimeout(poll, 1000);
}

async function savePending() {
  if (!pending) return;
  task = {kind: 'saving'}; controls();
  try {
    const result = await request('/api/v1/movement/trials', pending);
    const purpose = pending.purpose;
    pending = null; sessionStorage.removeItem('ruview-pending-trial'); renderLearning(result);
    $('#progress').textContent = purpose === 'learn' ? 'Trial added. Choose any condition to keep learning when you want.' : 'Check saved. Detector unchanged.';
  } catch (error) { $('#progress').textContent = `${error.message}. Your captured trial is kept in this tab; retry saving.`; }
  finally { task = null; controls(); }
}

async function startTrial() {
  if (task || pending || latest?.online_count !== 2) return;
  const label = $('#trial-label').value, purpose = $('#trial-purpose').value;
  const token = {kind: 'capture', cancelled: false}; task = token; controls();
  try {
    await refreshLearning();
    if (purpose === 'check' && !Object.keys(learning.model.nodes).length) throw new Error('Add a still and walking example before checking');
    const payload = {id: crypto.randomUUID(), label, purpose, revision: learning.model.revision, samples: []};
    for (let left = label === 'empty' ? 10 : 5; left > 0; left--) {
      if (token.cancelled) throw new Error('Trial cancelled; no readings saved');
      $('#progress').textContent = `${labels[label]} starts in ${left}s`;
      await sleep(1000);
    }
    let last = null; const deadline = Date.now() + 60000;
    while (payload.samples.length < 20) {
      if (token.cancelled) throw new Error('Trial cancelled; no readings saved');
      if (Date.now() > deadline) throw new Error('Stream too slow; incomplete trial discarded');
      const data = await request('/api/v1/movement/live');
      if (data.online_count !== 2) throw new Error('A node went offline; incomplete trial discarded');
      if (data.model_revision !== payload.revision) throw new Error('Detector changed during capture; please retry');
      const sample = {values: {}, timestamps: {}};
      for (const node of data.nodes) { sample.values[node.id] = node.signal.change; sample.timestamps[node.id] = node.signal.ts; }
      if (!last) last = sample;
      else if (['1', '2'].every(id => sample.timestamps[id] - last.timestamps[id] >= .5)) {
        if (['1', '2'].some(id => sample.timestamps[id] - last.timestamps[id] > 3.5)) throw new Error('Stream gap; incomplete trial discarded');
        payload.samples.push(sample); last = sample;
      }
      $('#progress').textContent = `${labels[label]} · ${payload.samples.length}/20 fresh readings. ${label === 'still' ? 'Remain still.' : label === 'empty' ? 'Keep the room empty.' : 'Keep walking naturally.'}`;
      if (payload.samples.length < 20) await sleep(1000);
    }
    if (token.cancelled) throw new Error('Trial cancelled; no readings saved');
    pending = payload; sessionStorage.setItem('ruview-pending-trial', JSON.stringify(payload));
    await savePending();
  } catch (error) { $('#progress').textContent = error.message; }
  finally { task = null; controls(); }
}

async function excludeTrial(id) {
  if (task || pending) return;
  task = {kind: 'saving'}; controls();
  try {
    renderLearning(await request('/api/v1/movement/trials/exclude', {id}));
    $('#progress').textContent = 'Trial excluded from learning/check results. Its record remains in history.';
  } catch (error) { $('#progress').textContent = error.message; }
  finally { task = null; controls(); }
}

$('#trial-label').addEventListener('change', () => { $('#trial-instructions').textContent = instructions[$('#trial-label').value]; });
$('#start-trial').addEventListener('click', startTrial);
$('#cancel-trial').addEventListener('click', () => { if (task?.kind === 'capture') task.cancelled = true; });
$('#retry-trial').addEventListener('click', savePending);
$('#discard-trial').addEventListener('click', () => { pending = null; sessionStorage.removeItem('ruview-pending-trial'); $('#progress').textContent = 'Unsaved trial discarded.'; controls(); });
window.addEventListener('beforeunload', event => { if (task?.kind === 'capture') { event.preventDefault(); event.returnValue = ''; } });
refreshLearning().then(() => { $('#progress').textContent = pending ? 'A captured trial is waiting to be saved.' : 'Choose one condition. Capture is enabled when both nodes are live.'; }).catch(error => { $('#progress').textContent = error.message; });
poll();
