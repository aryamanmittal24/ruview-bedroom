import { createBedroomScene } from './BedroomScene.js';

/**
 * Interactive 3D bedroom model built from the user's photos and measurements.
 * One CSI link supplies activity intensity only; it cannot provide a precise
 * body coordinate, so activity is shown as a room-wide status outside the floor plan.
 */
export class RoomMap {
  constructor(container) {
    this.container = container;
    this.unsubscribe = null;
    this.baseline = null;
    this.calibrating = false;
    this.calibrationSamples = [];
    this.pollTimer = null;
    this.lastTimestamp = null;
    this.previewUntil = 0;
  }

  init() {
    this.container.innerHTML = `
      <div class="room-map-shell">
        <div class="tab-header room-map-header">
          <div>
            <h2>3D Bedroom</h2>
            <p>Your approved WiZ room layout — drag to explore.</p>
          </div>
          <div id="room-map-source" class="room-map-source">CONNECTING SENSORS</div>
        </div>
        <div class="room-map-layout">
          <div class="room-map-stage room-map-stage-3d">
            <div class="room-map-controls" aria-label="3D room view controls">
              <button type="button" data-room-view="front">Front view</button>
              <button type="button" data-room-view="top">Top view</button>
              <button type="button" data-room-view="reset">Reset view</button>
              <button type="button" data-room-calibrate disabled>Calibrate fan-on baseline</button>
              <button type="button" data-room-preview>Preview activity indicator</button>
            </div>
            <canvas id="room-map-canvas" class="room-map-canvas" tabindex="0" role="img" aria-label="Interactive 3D model of an 11 by 10 foot bedroom. Drag to rotate the room."></canvas>
            <div id="room-map-indicator" class="room-map-indicator"><i></i><span id="room-map-indicator-text">Waiting for activity feed</span><small>Room-wide · location unavailable</small></div>
            <p class="room-map-hint">Drag to rotate · scroll to zoom</p>
          </div>
          <aside class="room-map-panel">
            <div class="room-map-readout"><span>Room</span><strong>Bedroom</strong></div>
            <div class="room-map-readout"><span>Room size</span><strong>11′ × 10′</strong></div>
            <div class="room-map-readout"><span>King bed model</span><strong>6′ × 6.5′</strong></div>
            <div class="room-map-readout"><span>ESP32 nodes</span><strong>Node 1: shared wall · Node 2: upper-right desk</strong></div>
            <div class="room-map-readout"><span>Two-node calibration</span><strong><a href="/movement">Open Movement Lab ↗</a></strong></div>
            <div class="room-map-readout"><span>Sensor</span><strong id="room-map-rssi">Waiting</strong></div>
            <div class="room-map-readout"><span>WiZ SpaceSense</span><strong id="room-map-wiz">Waiting</strong></div>
            <div class="room-map-readout"><span>Sensor fusion</span><strong id="room-map-fusion">Waiting</strong></div>
            <div class="room-map-readout"><span>Live CSI variation</span><strong id="room-map-raw">Waiting</strong></div>
            <div class="room-map-readout"><span>Activity</span><strong id="room-map-activity">Waiting</strong></div>
            <div class="room-map-meter"><span>CSI change above saved baseline</span><div><i id="room-map-meter-fill"></i></div></div>
            <div id="room-map-calibration-status" class="room-map-calibration-status">Calibration needs live raw CSI</div>
            <p class="room-map-note">The red status light shows room-wide activity. This setup does not measure where you or your cat are standing. Cyan rings mark the two fixed ESP32s. Fan and Wi-Fi traffic can also change the signal.</p>
          </aside>
        </div>
      </div>`;
    this.cache();
    this.bindScene();
    this.startPolling();
  }

  cache() {
    const q = (id) => this.container.querySelector(`#${id}`);
    this.el = { indicator: q('room-map-indicator'), indicatorText: q('room-map-indicator-text'), source: q('room-map-source'), canvas: q('room-map-canvas'), rssi: q('room-map-rssi'), wiz: q('room-map-wiz'), fusion: q('room-map-fusion'), raw: q('room-map-raw'), activity: q('room-map-activity'), meter: q('room-map-meter-fill'), calibration: q('room-map-calibration-status') };
    try { this.scene = createBedroomScene(this.el.canvas); }
    catch { this.container.querySelector('.room-map-hint').textContent = '3D unavailable in this browser. Try a browser with WebGL enabled.'; }
  }

  bindScene() {
    this.container.querySelectorAll('[data-room-view]').forEach(button => {
      button.addEventListener('click', () => this.scene?.view(button.dataset.roomView));
    });
    const calibrate = this.container.querySelector('[data-room-calibrate]');
    calibrate?.addEventListener('click', () => {
      if (!this.lastSignalFrame || Date.now()-this.lastSignalFrame>6000) { this.el.calibration.textContent='Live raw CSI is needed to calibrate'; return; }
      this.calibrating = true;
      this.calibrationSamples = [];
      calibrate.disabled = true;
      if (this.el.calibration) this.el.calibration.textContent = 'Listening with fan on… 0/20 samples';
    });
    this.container.querySelector('[data-room-preview]')?.addEventListener('click', () => {
      this.previewUntil = Date.now() + 4000;
      this.scene?.setActivity(.9);
      this.setIndicator(true, 'PREVIEW · not sensor data');
      this.el.activity.textContent = 'Activity indicator preview · not sensor data';
      clearTimeout(this.previewTimer);
      this.previewTimer = setTimeout(() => {
        this.previewUntil = 0;
        if (this.lastData) this.update(this.lastData);
        else { this.scene?.setActivity(0); this.setIndicator(false, 'Waiting for activity feed'); }
      }, 4000);
    });
  }

  setIndicator(active, text) {
    this.el.indicator.classList.toggle('active', active);
    this.el.indicatorText.textContent = text;
  }

  startPolling() {
    const poll = async () => {
      try {
        const response = await fetch('/api/v1/sensing/latest', { cache: 'no-store' });
        if (!response.ok) throw new Error(`HTTP ${response.status}`);
        const data = await response.json();
        const stamp = `${data.timestamp_ms}:${data.signal?.ts || 0}:${data.wiz?.ts || 0}`;
        if (stamp !== this.lastTimestamp) {
          this.lastTimestamp = stamp;
          this.update(data);
        }
      } catch {
        if (!this.lastFrame || Date.now() - this.lastFrame > 6000) this.markUnavailable();
      }
    };
    void poll();
    this.pollTimer = setInterval(poll, 500);
    this.staleTimer = setInterval(() => {
      if (!this.lastFrame || Date.now() - this.lastFrame > 6000) this.markUnavailable();
    }, 1500);
  }

  markUnavailable() {
    this.el.source.textContent = 'NO RECENT SENSOR DATA';
    this.el.source.classList.remove('live');
    this.el.activity.textContent = 'Unavailable';
    this.el.rssi.textContent = '— dBm';
    this.el.raw.textContent = 'Unavailable';
    this.el.wiz.textContent = 'Unavailable';
    this.el.fusion.textContent = 'ESP32 unavailable';
    this.el.meter.style.width = '0%';
    this.scene?.setActivity(0);
    this.setIndicator(false, 'Activity feed unavailable');
    this.scene?.setSignal(0);
    this.calibrating=false;
    this.calibrationSamples=[];
    this.container.querySelector('[data-room-calibrate]').disabled=true;
    this.el.calibration.textContent='Calibration needs live raw CSI';
  }

  update(data) {
    if (!data || !this.el) return;
    this.lastData = data;
    this.lastFrame = Date.now();
    const espOnline = data.esp32_online === true;
    const signal = espOnline && data.signal && Number.isFinite(Number(data.signal.change))
      ? data.signal : null;
    const signalChange = signal ? Number(signal.change) : 0;
    const newSignal = signal && signal.ts !== this.lastSignalTs;
    if (newSignal) {
      this.lastSignalTs = signal.ts;
      this.lastSignalFrame = Date.now();
    }
    if (!this.calibrating && !this.savingBaseline) this.baseline = data.baseline || null;
    const calibrate = this.container.querySelector('[data-room-calibrate]');
    if (signal) {
      if (!this.calibrating && !this.savingBaseline) {
        calibrate.disabled = false;
        if (this.baseline) this.el.calibration.textContent = `Saved baseline · ${this.baseline.sample_count} samples · ${this.baseline.context} · room boundary unverified`;
        if (!this.baseline) this.el.calibration.textContent = 'Raw CSI live · ready for fan-on calibration';
      }
    } else {
      this.calibrating = false;
      this.calibrationSamples = [];
      this.baseline = null;
      calibrate.disabled = true;
      this.el.calibration.textContent = 'Calibration needs live raw CSI';
    }
    if (this.calibrating && newSignal) {
      this.calibrationSamples.push(signalChange);
      this.el.calibration.textContent = `Listening with fan on… ${this.calibrationSamples.length}/20 samples`;
      if (this.calibrationSamples.length >= 20) {
        const samples = [...this.calibrationSamples];
        this.calibrating = false;
        this.savingBaseline = true;
        this.calibrationSamples = [];
        this.el.calibration.textContent = 'Saving baseline…';
        fetch('/api/v1/room/baseline', {method:'POST', headers:{'Content-Type':'application/json'}, body:JSON.stringify({samples})})
          .then(async response => {
            const result = await response.json();
            if (!response.ok) throw new Error(result.error || 'Save failed');
            this.baseline = result;
            this.el.calibration.textContent = 'Baseline saved across refreshes';
          })
          .catch(error => { this.el.calibration.textContent = `Calibration not saved: ${error.message}`; })
          .finally(() => { this.savingBaseline = false; calibrate.disabled = false; });
      }
    }
    const activity = data.activity || {};
    const espEnergy = Number(activity.esp_energy || 0);
    const wiz = data.wiz;
    const wizKnown = activity.wiz_known === true;
    const wizOccupied = activity.wiz_active === true;
    const energy = Number(activity.energy || 0);
    const active = activity.esp_calibrated && espEnergy > .25;
    const verified = Boolean(signal || wizKnown);
    this.scene?.setSignal(signalChange);
    const previewing = Date.now() < this.previewUntil;
    this.scene?.setActivity(previewing ? .9 : energy);
    this.setIndicator(previewing || active || wizOccupied, previewing ? 'PREVIEW · not sensor data' : (active ? 'Signal change detected' : (wizOccupied ? 'WiZ occupancy reported' : (signal && !this.baseline ? 'Calibrate to detect changes' : (!verified ? 'Activity feed unavailable' : 'Near saved baseline'))))); 
    this.el.meter.style.width = `${Math.round(energy * 100)}%`;
    this.el.rssi.textContent = !espOnline ? 'Offline · no packets' : `Node ${data.node_id ?? 1} · packets live`;
    this.el.raw.textContent = signal ? `${signalChange.toFixed(1)} · ${signal.sample_count} CSI frames` : 'No raw CSI';
    this.el.activity.textContent = previewing ? 'Activity indicator preview · not sensor data' : !verified ? 'No motion feed' :
      (signal && !this.baseline ? 'Calibrate to detect changes' :
        (active ? 'Signal change detected' : (wizOccupied ? 'WiZ occupancy reported' : 'Near saved baseline')));
    const onlineBulbs = Number(wiz?.online_count || 0);
    const wizPushLive = wiz?.bulbs?.some(bulb => bulb.registered && Date.now() / 1000 - Number(bulb.last_push_ts || 0) < 30);
    this.el.wiz.textContent = wiz ? `${onlineBulbs}/${wiz.configured_count || 4} bulbs connected · ${wizKnown ? (wizOccupied ? 'Occupied' : 'Clear') : (wizPushLive ? 'event connection OK; no motion event yet' : 'no SpaceSense event')}` : 'Listener unavailable';
    this.el.fusion.textContent = !verified ? 'Waiting for raw CSI or SpaceSense' : (signal && wizKnown ? 'ESP32 CSI + WiZ occupancy' : (signal ? 'ESP32 CSI only' : 'WiZ occupancy only'));
    this.el.source.textContent = !verified ? (espOnline ? 'ESP32 ONLINE · CSI WAITING' : 'NO VERIFIED MOTION FEED') : (signal && wizKnown ? 'ESP32 + WIZ SENSING' : (signal ? 'ESP32 CSI LIVE' : 'WIZ OCCUPANCY LIVE'));
    this.el.source.classList.toggle('live', verified);
  }

  dispose() {
    this.unsubscribe?.();
    clearInterval(this.pollTimer);
    clearInterval(this.staleTimer);
    clearTimeout(this.previewTimer);
    this.scene?.dispose();
  }
}
