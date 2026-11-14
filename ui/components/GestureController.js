import { sensingService } from '../services/sensing.service.js';

const CALIBRATION_FRAMES = 45;
const BURST_GAP_MS = 1450;
const COOLDOWN_MS = 1800;

export class GestureController {
  constructor(container) {
    this.container = container;
    this.calibrating = false;
    this.calibrationValues = [];
    this.baseline = null;
    this.threshold = null;
    this.listening = false;
    this.active = false;
    this.activeSince = 0;
    this.pendingBurst = null;
    this.lastGestureAt = 0;
    this.bursts = [];
    this.unsubscribe = null;
    this.tabIndex = 0;
  }

  init() {
    this.cacheElements();
    this.bindEvents();
    this.unsubscribe = sensingService.onData((data) => this.handleData(data));
    this.updateSource();
  }

  cacheElements() {
    const q = (id) => this.container.querySelector(`#${id}`);
    this.elements = {
      state: q('gesture-state'), status: q('gesture-status'), meter: q('gesture-meter-fill'),
      energy: q('gesture-energy'), baseline: q('gesture-baseline'), source: q('gesture-source'),
      log: q('gesture-log'), calibrate: q('gesture-calibrate'), toggle: q('gesture-toggle'), action: q('gesture-action')
    };
  }

  bindEvents() {
    this.elements.calibrate.addEventListener('click', () => this.startCalibration());
    this.elements.toggle.addEventListener('click', () => this.toggleListening());
  }

  startCalibration() {
    this.calibrating = true;
    this.calibrationValues = [];
    this.listening = false;
    this.active = false;
    this.elements.toggle.disabled = true;
    this.elements.toggle.textContent = 'Calibrating…';
    this.elements.state.textContent = 'CALIBRATING';
    this.elements.state.className = 'gesture-state calibrating';
    this.elements.status.textContent = 'Stay still for about two seconds…';
    this.addLog('Calibration started — keep the room quiet', 'info');
  }

  finishCalibration() {
    const sorted = [...this.calibrationValues].sort((a, b) => a - b);
    this.baseline = sorted[Math.floor(sorted.length / 2)];
    const deviations = sorted.map((value) => Math.abs(value - this.baseline)).sort((a, b) => a - b);
    const mad = deviations[Math.floor(deviations.length / 2)] || 0;
    this.threshold = this.baseline + Math.max(4, mad * 5, Math.abs(this.baseline) * 0.35);
    this.calibrating = false;
    this.elements.baseline.textContent = this.formatValue(this.baseline);
    this.elements.toggle.disabled = false;
    this.elements.toggle.textContent = 'Start listening';
    this.elements.state.textContent = 'READY';
    this.elements.state.className = 'gesture-state ready';
    this.elements.status.textContent = 'Calibration complete. Start listening, then move once or twice.';
    this.addLog(`Baseline saved at ${this.formatValue(this.baseline)}`, 'info');
  }

  toggleListening() {
    if (!this.baseline) return;
    this.listening = !this.listening;
    this.active = false;
    this.bursts = [];
    this.pendingBurst = null;
    this.elements.toggle.textContent = this.listening ? 'Stop listening' : 'Start listening';
    this.elements.state.textContent = this.listening ? 'LISTENING' : 'READY';
    this.elements.state.className = `gesture-state ${this.listening ? 'listening' : 'ready'}`;
    this.elements.status.textContent = this.listening ? 'Waiting for a signal burst…' : 'Listening paused.';
    this.addLog(this.listening ? 'Gesture listening enabled' : 'Gesture listening paused', 'info');
  }

  handleData(data) {
    this.updateSource();
    const energy = this.energyFrom(data);
    if (energy == null) return;
    this.elements.energy.textContent = this.formatValue(energy);
    const ratio = this.threshold ? Math.min(1, energy / Math.max(this.threshold, 1)) : 0;
    this.elements.meter.style.width = `${Math.round(ratio * 100)}%`;
    if (this.calibrating) {
      this.calibrationValues.push(energy);
      this.elements.status.textContent = `Listening to the room… ${this.calibrationValues.length}/${CALIBRATION_FRAMES}`;
      if (this.calibrationValues.length >= CALIBRATION_FRAMES) this.finishCalibration();
      return;
    }
    if (!this.listening || !this.threshold || sensingService.dataSource !== 'live') return;
    const isBurst = energy > this.threshold;
    if (isBurst && !this.active) {
      this.active = true;
      this.activeSince = Date.now();
      this.elements.state.textContent = 'MOTION';
      this.elements.state.className = 'gesture-state motion';
    } else if (!isBurst && this.active) {
      this.active = false;
      const duration = Date.now() - this.activeSince;
      if (duration >= 100 && duration <= 1800) this.recordBurst();
      this.elements.state.textContent = 'LISTENING';
      this.elements.state.className = 'gesture-state listening';
    }
  }

  recordBurst() {
    const now = Date.now();
    this.bursts = this.bursts.filter((time) => now - time < BURST_GAP_MS);
    this.bursts.push(now);
    if (this.bursts.length >= 2) {
      if (this.pendingBurst) clearTimeout(this.pendingBurst);
      this.pendingBurst = null;
      this.bursts = [];
      this.emitGesture('Double wave');
      return;
    }
    if (this.pendingBurst) clearTimeout(this.pendingBurst);
    this.pendingBurst = setTimeout(() => {
      this.pendingBurst = null;
      this.bursts = [];
      this.emitGesture('Wave');
    }, BURST_GAP_MS);
  }

  emitGesture(name) {
    const now = Date.now();
    if (now - this.lastGestureAt < COOLDOWN_MS) return;
    this.lastGestureAt = now;
    this.elements.status.textContent = `${name} detected`;
    this.addLog(name, 'gesture');
    this.applyAction(name);
  }

  applyAction(name) {
    const action = this.elements.action.value;
    if (action === 'highlight') {
      this.container.classList.remove('gesture-flash');
      void this.container.offsetWidth;
      this.container.classList.add('gesture-flash');
    } else if (action === 'theme') {
      document.querySelector('[data-theme-toggle], .theme-toggle')?.click();
    } else if (action === 'tabs') {
      const tabs = [...document.querySelectorAll('.nav-tab[data-tab]')];
      const current = tabs.findIndex((tab) => tab.classList.contains('active'));
      this.tabIndex = (current + 1) % tabs.length;
      tabs[this.tabIndex]?.click();
    }
    this.container.dispatchEvent(new CustomEvent('ruview-gesture', { detail: { name, action } }));
  }

  energyFrom(data) {
    const value = data?.features?.motion_band_power ?? data?.features?.variance;
    return Number.isFinite(Number(value)) ? Number(value) : null;
  }

  updateSource() {
    const live = sensingService.dataSource === 'live';
    this.elements.source.textContent = live ? 'ESP32 LIVE' : `SOURCE: ${String(sensingService.dataSource).toUpperCase()}`;
    this.elements.source.className = `gesture-source ${live ? 'live' : ''}`;
  }

  addLog(message, kind) {
    const empty = this.elements.log.querySelector('.gesture-empty');
    if (empty) empty.remove();
    const item = document.createElement('div');
    item.className = `gesture-log-item ${kind}`;
    const time = new Date().toLocaleTimeString([], { hour: '2-digit', minute: '2-digit', second: '2-digit' });
    item.innerHTML = `<span>${time}</span><strong>${message}</strong>`;
    this.elements.log.prepend(item);
    while (this.elements.log.children.length > 8) this.elements.log.lastElementChild.remove();
  }

  formatValue(value) { return Number(value).toFixed(1); }
}
