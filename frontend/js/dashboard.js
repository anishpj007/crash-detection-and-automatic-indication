// dashboard.js — wires up the Crash Detection dashboard UI to the REST API.

(function guardAuth() {
  if (!localStorage.getItem('cds_token')) {
    window.location.href = '/index.html';
  }
})();

const user = JSON.parse(localStorage.getItem('cds_user') || '{}');
document.getElementById('user-label').textContent = `${user.username || ''} (${user.role || ''})`;
document.getElementById('logout-btn').onclick = () => {
  localStorage.removeItem('cds_token');
  localStorage.removeItem('cds_user');
  window.location.href = '/index.html';
};

// ---------------- Navigation ----------------
const views = ['overview', 'upload', 'live', 'incidents', 'map'];
const titles = { overview: 'Overview', upload: 'Upload & Analyze', live: 'Live Camera', incidents: 'Incidents', map: 'Incident Map' };
const INCIDENT_LOCATION = {
  name: 'SRM Institute of Science and Technology, Kattankulathur',
  latitude: 12.824943,
  longitude: 80.045181,
};

function incidentMapUrl(incident = {}) {
  const latitude = Number(incident.latitude);
  const longitude = Number(incident.longitude);
  const hasValidCoordinates = Number.isFinite(latitude) && Number.isFinite(longitude)
    && latitude >= -90 && latitude <= 90 && longitude >= -180 && longitude <= 180
    && (latitude !== 0 || longitude !== 0);
  const point = hasValidCoordinates
    ? `${latitude},${longitude}`
    : `${INCIDENT_LOCATION.latitude},${INCIDENT_LOCATION.longitude}`;
  return `https://www.google.com/maps/search/?api=1&query=${encodeURIComponent(point)}`;
}

document.querySelectorAll('.nav-item').forEach(el => {
  el.addEventListener('click', () => switchView(el.dataset.view));
});

function switchView(name) {
  views.forEach(v => {
    document.getElementById(`view-${v}`).classList.toggle('hidden', v !== name);
  });
  document.querySelectorAll('.nav-item').forEach(el => {
    const active = el.dataset.view === name;
    el.classList.toggle('active', active);
    if (active) el.setAttribute('aria-current', 'page');
    else el.removeAttribute('aria-current');
  });
  document.getElementById('view-title').textContent = titles[name];
  const reducedMotion = window.matchMedia?.('(prefers-reduced-motion: reduce)').matches;
  window.scrollTo({ top: 0, behavior: reducedMotion ? 'auto' : 'smooth' });
  if (name === 'overview') loadOverview();
  if (name === 'incidents') loadIncidents();
  if (name === 'map') loadMap();
}

// ---------------- Overview ----------------
async function loadOverview() {
  try {
    const s = await api.stats();
    document.getElementById('stat-videos').textContent = s.videos_processed;
    document.getElementById('stat-incidents').textContent = s.total_incidents;
    const high = (s.by_severity.find(x => x.severity === 'High') || {}).c || 0;
    const resolved = (s.by_status.find(x => x.status === 'resolved') || {}).c || 0;
    document.getElementById('stat-high').textContent = high;
    document.getElementById('stat-resolved').textContent = resolved;

    const recent = await api.listIncidents();
    renderIncidentTable(recent.slice(0, 6), document.getElementById('recent-incidents-table'));
  } catch (err) {
    console.error(err);
  }
}

// ---------------- Incident table (shared renderer) ----------------
function renderIncidentTable(rows, container) {
  if (!rows.length) {
    container.innerHTML = `<div class="empty-state">No incidents yet. Upload a video to start analysis.</div>`;
    return;
  }
  const html = `
    <table>
      <thead><tr>
        <th><input type="checkbox" class="incident-select-all-check" aria-label="Select all incidents shown"></th>
        <th>ID</th><th>Video</th><th>Timestamp</th><th>Severity</th><th>Confidence</th><th>Status</th><th>Detected</th><th>Location</th><th></th>
      </tr></thead>
      <tbody>
        ${rows.map(r => `
          <tr class="incident-row" data-id="${r.id}">
            <td class="incident-select-cell"><input type="checkbox" class="incident-row-select" data-id="${r.id}" aria-label="Select incident #${r.id}"></td>
            <td class="mono">#${r.id}</td>
            <td>${escapeHtml(r.video_name)}</td>
            <td class="mono">${r.timestamp_in_video}s</td>
            <td><span class="badge badge-${r.severity}">${r.severity}</span></td>
            <td class="mono">${Math.round(r.confidence * 100)}%</td>
            <td><span class="badge badge-${r.status}">${r.status.replace('_', ' ')}</span></td>
            <td class="mono">${r.detected_at}</td>
            <td><a class="map-link table-map-link" href="${incidentMapUrl(r)}" target="_blank" rel="noopener noreferrer">Map ↗</a></td>
            <td class="incident-delete-cell"><button type="button" class="incident-delete-btn" data-id="${r.id}" aria-label="Delete incident #${r.id}" title="Delete incident">×</button></td>
          </tr>
        `).join('')}
      </tbody>
    </table>`;
  container.innerHTML = `
    <div class="incident-table-toolbar">
      <button type="button" class="select-all-incidents">Select all shown</button>
      <button type="button" class="clear-incident-selection">Clear</button>
      <button type="button" class="delete-selected-incidents danger" disabled>Delete selected (0)</button>
      <span class="incident-action-feedback" aria-live="polite"></span>
    </div>${html}`;
  container.querySelectorAll('.incident-row').forEach(row => {
    row.addEventListener('click', () => openIncidentModal(row.dataset.id));
  });
  const updateSelection = () => {
    const checks = [...container.querySelectorAll('.incident-row-select')];
    const selected = checks.filter(check => check.checked).length;
    const allCheck = container.querySelector('.incident-select-all-check');
    allCheck.checked = checks.length > 0 && selected === checks.length;
    allCheck.indeterminate = selected > 0 && selected < checks.length;
    const deleteButton = container.querySelector('.delete-selected-incidents');
    deleteButton.disabled = selected === 0;
    deleteButton.textContent = `Delete selected (${selected})`;
  };
  container.querySelectorAll('.incident-row-select').forEach(check => {
    check.addEventListener('click', event => event.stopPropagation());
    check.addEventListener('change', updateSelection);
  });
  container.querySelector('.incident-select-all-check').addEventListener('change', event => {
    container.querySelectorAll('.incident-row-select').forEach(check => { check.checked = event.target.checked; });
    updateSelection();
  });
  container.querySelector('.select-all-incidents').addEventListener('click', () => {
    container.querySelectorAll('.incident-row-select').forEach(check => { check.checked = true; });
    updateSelection();
  });
  container.querySelector('.clear-incident-selection').addEventListener('click', () => {
    container.querySelectorAll('.incident-row-select').forEach(check => { check.checked = false; });
    updateSelection();
  });
  container.querySelector('.delete-selected-incidents').addEventListener('click', () => {
    const ids = [...container.querySelectorAll('.incident-row-select:checked')].map(check => check.dataset.id);
    deleteSelectedIncidents(ids);
  });
  container.querySelectorAll('.incident-delete-btn').forEach(button => {
    button.addEventListener('click', event => {
      event.stopPropagation();
      deleteIncidentFromUi(button.dataset.id, button);
    });
  });
  container.querySelectorAll('.table-map-link').forEach(link => {
    link.addEventListener('click', event => event.stopPropagation());
  });
}

async function deleteSelectedIncidents(ids) {
  if (!ids.length) return;
  const buttons = document.querySelectorAll('.delete-selected-incidents');
  buttons.forEach(button => { button.disabled = true; });
  let errorMessage = '';
  try {
    for (const id of ids) await api.deleteIncident(id);
  } catch (err) {
    errorMessage = `Some incidents could not be deleted: ${err.message}`;
  }
  await Promise.all([loadIncidents(), loadOverview()]);
  if (errorMessage) document.querySelectorAll('.incident-action-feedback').forEach(el => { el.textContent = errorMessage; });
}

function escapeHtml(s) {
  return (s || '').replace(/[&<>"']/g, c => ({ '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;' }[c]));
}

// ---------------- Incidents view ----------------
async function loadIncidents() {
  const severity = document.getElementById('filter-severity').value;
  const status = document.getElementById('filter-status').value;
  const filters = {};
  if (severity) filters.severity = severity;
  if (status) filters.status = status;
  const rows = await api.listIncidents(filters);
  renderIncidentTable(rows, document.getElementById('incidents-table'));
}
document.getElementById('filter-severity').addEventListener('change', loadIncidents);
document.getElementById('filter-status').addEventListener('change', loadIncidents);

// ---------------- Incident detail modal ----------------
async function openIncidentModal(id) {
  const inc = await api.getIncident(id);
  const root = document.getElementById('modal-root');
  root.innerHTML = `
    <div class="modal-overlay" id="modal-overlay">
      <div class="modal">
        <button class="modal-close" id="modal-close">✕</button>
        <h3>Incident #${inc.id}</h3>
        ${inc.snapshot_path ? `<img src="/snapshots/${inc.snapshot_path}" alt="Crash snapshot">` : ''}
        <div class="detail-row"><span class="k">Video</span><span>${escapeHtml(inc.video_name)}</span></div>
        <div class="detail-row"><span class="k">Timestamp in video</span><span class="mono">${inc.timestamp_in_video}s (frame ${inc.frame_number})</span></div>
        <div class="detail-row"><span class="k">Severity</span><span class="badge badge-${inc.severity}">${inc.severity}</span></div>
        <div class="detail-row"><span class="k">Confidence</span><span class="mono">${Math.round(inc.confidence * 100)}%</span></div>
        <div class="detail-row"><span class="k">Location</span><span class="incident-location-value">${escapeHtml(inc.location_name || INCIDENT_LOCATION.name)} <a class="map-link" href="${incidentMapUrl(inc)}" target="_blank" rel="noopener noreferrer">Open map ↗</a></span></div>
        <div class="detail-row"><span class="k">Status</span><span class="badge badge-${inc.status}">${inc.status.replace('_', ' ')}</span></div>
        <div class="detail-row"><span class="k">Detected at</span><span class="mono">${inc.detected_at}</span></div>
        <div class="status-actions">
          <button data-status="acknowledged">Acknowledge</button>
          <button data-status="resolved">Mark Resolved</button>
          <button data-status="false_alarm">False Alarm</button>
          <button id="delete-incident-btn" class="danger">Delete Incident</button>
        </div>
        <div class="claim-option-actions">
          <button type="button" id="prepare-claim-btn">Prepare Insurance Claim</button>
        </div>
      </div>
    </div>`;

  document.getElementById('modal-close').onclick = closeModal;
  document.getElementById('modal-overlay').addEventListener('click', (e) => {
    if (e.target.id === 'modal-overlay') closeModal();
  });
  root.querySelectorAll('.status-actions button').forEach(btn => {
    if (btn.id === 'delete-incident-btn') return;
    btn.addEventListener('click', async () => {
      await api.updateIncidentStatus(id, btn.dataset.status);
      closeModal();
      loadIncidents();
      loadOverview();
    });
  });
  document.getElementById('delete-incident-btn').addEventListener('click', event => {
    deleteIncidentFromUi(id, event.currentTarget, true);
  });
  document.getElementById('prepare-claim-btn').addEventListener('click', () => {
    openClaimDraft(inc);
  });
}

function openClaimDraft(incident) {
  const modal = document.querySelector('#modal-root .modal');
  if (!modal || document.getElementById('claim-draft-form')) return;

  modal.insertAdjacentHTML('beforeend', `
    <section class="claim-draft" id="claim-draft">
      <h4>Prepare an insurance claim summary</h4>
      <p class="claim-note">Fill in the details you know, then create a printable draft. This app will not submit it to an insurer. Confirm the event and use your insurer’s official claim process.</p>
      <form id="claim-draft-form">
        <div class="claim-form-grid">
          <label>Insurer
            <input name="insurer" autocomplete="organization" placeholder="Insurance company">
          </label>
          <label>Policy number
            <input name="policy_number" autocomplete="off">
          </label>
          <label>Insured / claimant name
            <input name="claimant_name" autocomplete="name">
          </label>
          <label>Contact number or email
            <input name="contact" autocomplete="email">
          </label>
          <label>Vehicle registration number
            <input name="vehicle_registration" placeholder="e.g. TN 00 AB 1234">
          </label>
          <label>Driver name
            <input name="driver_name" autocomplete="name">
          </label>
          <label>Accident date and time (confirm from footage)
            <input name="accident_datetime" type="datetime-local">
          </label>
          <label>Accident location / address
            <input name="accident_location" placeholder="Road, area, city">
          </label>
          <label>Claim type
            <select name="claim_type">
              <option value="Vehicle damage">Vehicle damage</option>
              <option value="Third-party damage">Third-party damage</option>
              <option value="Injury">Injury</option>
              <option value="Other / to be confirmed">Other / to be confirmed</option>
            </select>
          </label>
          <label>Police report / FIR reference, if applicable
            <input name="police_reference">
          </label>
          <label class="claim-full-width">Accident description (your account)
            <textarea name="description" rows="4" placeholder="Describe what happened. Separate what you observed from what is uncertain."></textarea>
          </label>
          <label class="claim-full-width">Witnesses or other vehicles involved
            <textarea name="witnesses" rows="2"></textarea>
          </label>
        </div>
        <div class="claim-draft-actions">
          <button type="submit">Create printable claim summary</button>
          <span class="claim-feedback" id="claim-feedback" aria-live="polite"></span>
        </div>
      </form>
    </section>`);

  document.getElementById('claim-draft-form').addEventListener('submit', event => {
    event.preventDefault();
    const reportWindow = window.open('', '_blank');
    if (!reportWindow) {
      document.getElementById('claim-feedback').textContent = 'Allow pop-ups for this local app, then try again.';
      return;
    }
    const values = Object.fromEntries(new FormData(event.currentTarget).entries());
    reportWindow.document.open();
    reportWindow.document.write(buildClaimReportHtml(incident, values));
    reportWindow.document.close();
  });
}

function buildClaimReportHtml(incident, values) {
  const field = value => escapeHtml(String(value || 'Not provided'));
  const snapshotUrl = incident.snapshot_path
    ? `${window.location.origin}/snapshots/${encodeURIComponent(incident.snapshot_path)}`
    : '';
  const snapshot = snapshotUrl
    ? `<img class="evidence-image" src="${escapeHtml(snapshotUrl)}" alt="Incident snapshot">`
    : '<p>No incident snapshot is available.</p>';
  const detectedAt = field(incident.detected_at);
  const sourceVideo = field(incident.video_name);
  const videoTimestamp = `${Number(incident.timestamp_in_video || 0).toFixed(2)} seconds`;
  const confidence = `${Math.round(Number(incident.confidence || 0) * 100)}%`;

  return `<!doctype html>
    <html lang="en">
    <head>
      <meta charset="utf-8">
      <meta name="viewport" content="width=device-width,initial-scale=1">
      <title>Claim Summary — Incident #${Number(incident.id)}</title>
      <style>
        body{font:15px/1.5 Arial,sans-serif;color:#f5eeee;background:#0b090a;max-width:850px;margin:32px auto;padding:0 22px}
        h1{font-size:24px;margin-bottom:4px;color:#ff5367}h2{font-size:17px;margin:22px 0 8px;border-bottom:1px solid #503036;padding-bottom:5px}
        .notice{border:1px solid #a92a3d;background:#211216;padding:12px;border-radius:9px}
        table{border-collapse:collapse;width:100%}th,td{text-align:left;vertical-align:top;padding:8px;border:1px solid #402c31}th{width:32%;background:#211619}td{background:#130f10}
        .evidence-image{display:block;max-width:100%;max-height:420px;object-fit:contain;border:1px solid #503036;margin-top:10px}
        .prewrap{white-space:pre-wrap;min-height:22px}.toolbar{margin:0 0 18px}.toolbar button{padding:10px 16px;background:#e11d35;color:#fff;border:0;border-radius:7px;font-weight:700;cursor:pointer}
        @media print{body{color:#182033;background:#fff;margin:0 auto;padding:0 10mm}.toolbar{display:none}.notice{color:#182033;background:#fff;border-color:#c98b16}h1{color:#a31025}h2{border-color:#ccd3df}th,td{background:#fff;border-color:#d5dae3}th{background:#f7ecee}}
      </style>
    </head>
    <body>
      <div class="toolbar"><button type="button" onclick="window.print()">Print / Save as PDF</button></div>
      <h1>Motor Insurance Claim — Supporting Summary</h1>
      <p>Draft prepared from incident #${Number(incident.id)}. Review all entries and follow your insurer’s official claim process.</p>
      <div class="notice"><strong>Important:</strong> This is a user-prepared evidence summary, not an insurer claim form or a confirmation of fault or liability. The app’s AI only flagged a possible incident; verify the footage and correct any inaccurate details before sharing.</div>
      <h2>Claimant and policy</h2>
      <table>
        <tr><th>Insurer</th><td>${field(values.insurer)}</td></tr>
        <tr><th>Policy number</th><td>${field(values.policy_number)}</td></tr>
        <tr><th>Insured / claimant</th><td>${field(values.claimant_name)}</td></tr>
        <tr><th>Contact</th><td>${field(values.contact)}</td></tr>
      </table>
      <h2>Accident details</h2>
      <table>
        <tr><th>Claim type</th><td>${field(values.claim_type)}</td></tr>
        <tr><th>Vehicle registration</th><td>${field(values.vehicle_registration)}</td></tr>
        <tr><th>Driver</th><td>${field(values.driver_name)}</td></tr>
        <tr><th>Accident date and time</th><td>${field(values.accident_datetime)}</td></tr>
        <tr><th>Accident location</th><td>${field(values.accident_location)}</td></tr>
        <tr><th>Project location tag</th><td>${field(INCIDENT_LOCATION.name)} · <a href="${incidentMapUrl()}" target="_blank" rel="noopener noreferrer">Open map</a></td></tr>
        <tr><th>Police report / FIR reference</th><td>${field(values.police_reference)}</td></tr>
        <tr><th>Description supplied by claimant</th><td class="prewrap">${field(values.description)}</td></tr>
        <tr><th>Witnesses / other vehicles</th><td class="prewrap">${field(values.witnesses)}</td></tr>
      </table>
      <h2>App evidence (verify before use)</h2>
      <table>
        <tr><th>Incident record</th><td>#${Number(incident.id)}</td></tr>
        <tr><th>Source video</th><td>${sourceVideo}</td></tr>
        <tr><th>Timestamp in source video</th><td>${videoTimestamp}</td></tr>
        <tr><th>Recorded by app at</th><td>${detectedAt}</td></tr>
        <tr><th>Model assessment</th><td>Possible collision · ${field(incident.severity)} severity · ${confidence} model confidence · current incident status: ${field(incident.status)}</td></tr>
      </table>
      ${snapshot}
      <h2>Possible supporting documents</h2>
      <p>Check your policy and insurer’s checklist. They may request their official claim form, vehicle registration certificate, driving licence, repair estimate or invoice, and a police report where applicable. Requirements vary by claim and insurer.</p>
    </body>
    </html>`;
}

async function deleteIncidentFromUi(id, button, closeDetail = false) {
  button.disabled = true;
  try {
    await api.deleteIncident(id);
    if (closeDetail) closeModal();
    await Promise.all([loadIncidents(), loadOverview()]);
  } catch (err) {
    button.disabled = false;
    const feedback = button.closest('.incident-table-toolbar')?.querySelector('.incident-action-feedback');
    if (feedback) feedback.textContent = `Could not delete incident: ${err.message}`;
    else console.error(`Could not delete incident #${id}:`, err);
  }
}
function closeModal() { document.getElementById('modal-root').innerHTML = ''; }

// ---------------- Map ----------------
let leafletMap = null;
let incidentMapMarkers = null;
async function loadMap() {
  const rows = await api.listIncidents();
  if (!leafletMap) {
    leafletMap = L.map('map').setView([INCIDENT_LOCATION.latitude, INCIDENT_LOCATION.longitude], 16);
    L.tileLayer('https://{s}.tile.openstreetmap.org/{z}/{x}/{y}.png', {
      attribution: '&copy; OpenStreetMap contributors',
    }).addTo(leafletMap);
    incidentMapMarkers = L.layerGroup().addTo(leafletMap);
  }
  incidentMapMarkers.clearLayers();
  const count = document.getElementById('map-incident-count');
  count.textContent = `${rows.length} incident${rows.length === 1 ? '' : 's'} tagged to the fixed project location.`;
  const marker = L.marker([INCIDENT_LOCATION.latitude, INCIDENT_LOCATION.longitude]).addTo(incidentMapMarkers);
  marker.bindPopup(`<b>${escapeHtml(INCIDENT_LOCATION.name)}</b><br>${rows.length} incident${rows.length === 1 ? '' : 's'} recorded here.<br><a href="${incidentMapUrl()}" target="_blank" rel="noopener noreferrer">Open in Google Maps ↗</a>`);
  leafletMap.setView([INCIDENT_LOCATION.latitude, INCIDENT_LOCATION.longitude], 16);
  window.setTimeout(() => leafletMap.invalidateSize(), 0);
}

// ---------------- Upload flow ----------------
const uploadZone = document.getElementById('upload-zone');
const fileInput = document.getElementById('file-input');
const uploadBtn = document.getElementById('upload-btn');
let selectedFile = null;

uploadZone.addEventListener('click', () => fileInput.click());
uploadZone.addEventListener('dragover', (e) => { e.preventDefault(); uploadZone.classList.add('drag'); });
uploadZone.addEventListener('dragleave', () => uploadZone.classList.remove('drag'));
uploadZone.addEventListener('drop', (e) => {
  e.preventDefault();
  uploadZone.classList.remove('drag');
  if (e.dataTransfer.files.length) selectFile(e.dataTransfer.files[0]);
});
fileInput.addEventListener('change', () => { if (fileInput.files.length) selectFile(fileInput.files[0]); });

function selectFile(file) {
  selectedFile = file;
  document.getElementById('upload-zone-text').innerHTML =
    `✅ <b>${escapeHtml(file.name)}</b> selected<br><small>${(file.size / 1024 / 1024).toFixed(1)} MB — click to change</small>`;
  uploadBtn.disabled = false;
}

uploadBtn.addEventListener('click', async () => {
  if (!selectedFile) return;
  uploadBtn.disabled = true;
  const statusEl = document.getElementById('upload-status');
  const progressWrap = document.getElementById('progress-wrap');
  const progressFill = document.getElementById('progress-fill');

  try {
    statusEl.textContent = 'Uploading video...';
    const uploadRes = await api.uploadVideo(selectedFile, {
      location_name: INCIDENT_LOCATION.name,
      latitude: INCIDENT_LOCATION.latitude,
      longitude: INCIDENT_LOCATION.longitude,
    });
    const videoId = uploadRes.video_id;

    statusEl.textContent = 'Starting analysis...';
    await api.startProcessing(videoId);
    progressWrap.classList.remove('hidden');

    const poll = setInterval(async () => {
      const status = await api.processingStatus(videoId);
      progressFill.style.width = `${status.progress || 0}%`;
      if (status.status === 'processed') {
        clearInterval(poll);
        const found = status.incidents_found ?? '?';
        statusEl.textContent = `✅ Analysis complete — ${found} incident(s) detected.`;
        uploadBtn.disabled = false;
        loadOverview();
      } else if (status.status === 'failed') {
        clearInterval(poll);
        statusEl.textContent = `❌ Analysis failed: ${status.error || 'unknown error'}`;
        uploadBtn.disabled = false;
      } else {
        statusEl.textContent = `Analyzing video... ${status.progress || 0}%`;
      }
    }, 1500);
  } catch (err) {
    statusEl.textContent = `❌ ${err.message}`;
    uploadBtn.disabled = false;
  }
});

// ---------------- Init ----------------
loadOverview();

// ---------------- Live camera detection ----------------
let liveStream = null;
let liveSessionId = null;
let liveTimer = null;
let liveBusy = false;
let liveCanvasCtx = null;

const liveVideo = document.getElementById('live-video');
const liveCanvas = document.getElementById('live-canvas');
const startLiveBtn = document.getElementById('start-live-btn');
const startScreenBtn = document.getElementById('start-screen-btn');
const startFileBtn = document.getElementById('start-file-btn');
const liveFileInput = document.getElementById('live-file-input');
const stopLiveBtn = document.getElementById('stop-live-btn');

function setLiveUi(state, text) {
  const status = document.getElementById('live-status');
  const dot = document.getElementById('live-dot');
  const overlay = document.getElementById('live-overlay');
  status.textContent = text;
  dot.className = 'live-dot ' + (state === 'alert' ? 'alert' : state === 'active' ? 'active' : 'idle');
  overlay.className = 'camera-overlay ' + (state === 'alert' ? 'alert' : state === 'active' ? 'active' : '');
  overlay.textContent = state === 'alert' ? 'ACCIDENT DETECTED' : state === 'active' ? 'DETECTOR ACTIVE' : 'DETECTOR OFF';
}

function showLiveIncident(incident) {
  const alert = document.getElementById('live-alert');
  const smsStatus = incident.sms_alert_status || 'disabled';
  const smsMessage = smsStatus === 'queued'
    ? 'SMS notification queued for the configured provider.'
    : smsStatus === 'not_configured'
      ? 'SMS is enabled but not fully configured; the incident is still saved.'
      : smsStatus === 'failed'
        ? 'Could not queue the SMS notification; the incident is still saved.'
        : 'SMS alerts are off; the incident is still saved.';
  alert.classList.add('detected');
  alert.innerHTML = `
    <div class="incident-trigger-title">🚨 ACCIDENT DETECTED</div>
    <div>Severity: <b>${escapeHtml(incident.severity)}</b> · Confidence: <b>${Math.round(incident.confidence * 100)}%</b></div>
    <div class="hint" style="margin-top:5px">Incident #${incident.id} saved to the Incidents view. ${escapeHtml(smsMessage)}</div>
    <div class="live-incident-location">📍 ${escapeHtml(INCIDENT_LOCATION.name)} · <a class="map-link" href="${incidentMapUrl(incident)}" target="_blank" rel="noopener noreferrer">Open map ↗</a></div>`;
  setLiveUi('alert', 'Accident detected');
  setTimeout(() => {
    if (liveSessionId) setLiveUi('active', 'Temporal accident model');
  }, 3500);
}

async function loadSmsStatus() {
  const statusEl = document.getElementById('sms-status');
  if (!statusEl) return;
  try {
    const status = await api.smsNotificationStatus();
    const lastAttempt = status.last_attempt;
    if (lastAttempt && String(lastAttempt.sent_status).startsWith('failed')) {
      statusEl.textContent = `Last SMS attempt failed (${lastAttempt.sent_status}) at ${lastAttempt.sent_at}.`;
      statusEl.style.color = 'var(--red)';
    } else if (status.ready) {
      statusEl.textContent = status.provider === 'Twilio' && status.trial_mode
        ? `SMS alerts enabled via ${status.provider}'s generic trial template for ${status.recipient}.`
        : `SMS alerts enabled via ${status.provider} for ${status.recipient}.`;
      statusEl.style.color = 'var(--green)';
    } else if (status.state === 'not_configured') {
      statusEl.textContent = 'SMS is enabled but missing configuration. Complete backend/.env and restart the app.';
      statusEl.style.color = 'var(--amber)';
    } else {
      statusEl.textContent = 'SMS alerts are off. Configure backend/.env to receive live crash texts.';
      statusEl.style.color = 'var(--muted)';
    }
  } catch (err) {
    statusEl.textContent = `Could not read SMS setup: ${err.message}`;
  }
}

async function sendLiveFrame() {
  if (!liveSessionId || liveBusy || !liveVideo.videoWidth) return;
  liveBusy = true;
  try {
    const targetWidth = 640;
    const targetHeight = Math.round(targetWidth * liveVideo.videoHeight / liveVideo.videoWidth);
    liveCanvas.width = targetWidth;
    liveCanvas.height = targetHeight;
    if (!liveCanvasCtx) liveCanvasCtx = liveCanvas.getContext('2d', { alpha: false });
    liveCanvasCtx.drawImage(liveVideo, 0, 0, targetWidth, targetHeight);
    const jpeg = liveCanvas.toDataURL('image/jpeg', 0.80);
    const res = await api.sendLiveFrame(liveSessionId, jpeg);
    document.getElementById('live-frames').textContent = res.frame_number || 0;
    document.getElementById('live-incidents').textContent = res.incidents_found || 0;
    const dbg = res.debug || {};
    const scorePct = Math.round((dbg.accident_score || 0) * 100);
    const meanPct = Math.round((dbg.accident_mean || 0) * 100);
    const faceStatus = dbg.face_guard_active ? 'active' : 'clear';
    document.getElementById('live-debug').textContent = `Accident: ${scorePct}% · Avg: ${meanPct}% · Face guard: ${faceStatus}`;
    if (res.incident) {
      showLiveIncident(res.incident);
      setTimeout(loadSmsStatus, 14000);
    }
  } catch (err) {
    document.getElementById('live-error').textContent = `Live stream error: ${err.message}`;
    await stopLiveDetection(true);
  } finally {
    liveBusy = false;
  }
}

async function initLiveSession() {
  const started = await api.startLive({
    location_name: INCIDENT_LOCATION.name,
    latitude: INCIDENT_LOCATION.latitude,
    longitude: INCIDENT_LOCATION.longitude,
    fps: 10,
  });
  liveSessionId = started.session_id;
  document.getElementById('live-alert').classList.remove('detected');
  document.getElementById('live-alert').innerHTML = '<div class="incident-trigger-title">No accident detected</div><div class="hint">Play the accident clip in view of the camera or screen share.</div>';
  setLiveUi('active', 'Temporal accident model');
  startLiveBtn.disabled = true;
  if (startScreenBtn) startScreenBtn.disabled = true;
  if (startFileBtn) startFileBtn.disabled = true;
  stopLiveBtn.disabled = false;
  liveTimer = setInterval(sendLiveFrame, 100);
  await sendLiveFrame();
}

async function startLiveDetection() {
  document.getElementById('live-error').textContent = '';
  if (!navigator.mediaDevices || !navigator.mediaDevices.getUserMedia) {
    document.getElementById('live-error').textContent = 'This browser does not expose camera access. Open the app from localhost/HTTPS and allow camera permission.';
    return;
  }
  try {
    setLiveUi('idle', 'Requesting camera...');
    liveStream = await navigator.mediaDevices.getUserMedia({
      video: { width: { ideal: 1280 }, height: { ideal: 720 }, facingMode: 'user' },
      audio: false,
    });
    liveVideo.srcObject = liveStream;
    liveVideo.src = '';
    await liveVideo.play();
    await initLiveSession();
  } catch (err) {
    document.getElementById('live-error').textContent = `Could not start webcam: ${err.message}`;
    if (liveStream) liveStream.getTracks().forEach(t => t.stop());
    liveStream = null;
    setLiveUi('idle', 'Stopped');
  }
}

async function startScreenDetection() {
  document.getElementById('live-error').textContent = '';
  if (!navigator.mediaDevices || !navigator.mediaDevices.getDisplayMedia) {
    document.getElementById('live-error').textContent = 'Screen sharing is not supported in this browser.';
    return;
  }
  try {
    setLiveUi('idle', 'Select screen or tab...');
    liveStream = await navigator.mediaDevices.getDisplayMedia({
      video: { cursor: 'never' },
      audio: false,
    });
    liveVideo.srcObject = liveStream;
    liveVideo.src = '';
    await liveVideo.play();
    liveStream.getVideoTracks()[0].addEventListener('ended', () => stopLiveDetection(false));
    await initLiveSession();
  } catch (err) {
    document.getElementById('live-error').textContent = `Could not share screen: ${err.message}`;
    if (liveStream) liveStream.getTracks().forEach(t => t.stop());
    liveStream = null;
    setLiveUi('idle', 'Stopped');
  }
}

async function startFileDetection(file) {
  if (!file) return;
  document.getElementById('live-error').textContent = '';
  try {
    setLiveUi('idle', 'Loading video...');
    if (liveStream) {
      liveStream.getTracks().forEach(t => t.stop());
      liveStream = null;
    }
    liveVideo.srcObject = null;
    liveVideo.src = URL.createObjectURL(file);
    liveVideo.loop = false;
    await liveVideo.play();
    await initLiveSession();
  } catch (err) {
    document.getElementById('live-error').textContent = `Could not stream file: ${err.message}`;
    setLiveUi('idle', 'Stopped');
  }
}

async function stopLiveDetection(silent = false) {
  if (liveTimer) clearInterval(liveTimer);
  liveTimer = null;
  const sid = liveSessionId;
  liveSessionId = null;
  startLiveBtn.disabled = false;
  if (startScreenBtn) startScreenBtn.disabled = false;
  if (startFileBtn) startFileBtn.disabled = false;
  stopLiveBtn.disabled = true;
  if (liveStream) liveStream.getTracks().forEach(t => t.stop());
  liveStream = null;
  liveVideo.pause();
  liveVideo.srcObject = null;
  liveVideo.src = '';
  setLiveUi('idle', 'Stopped');
  if (sid) {
    try { await api.stopLive(sid); } catch (err) { if (!silent) document.getElementById('live-error').textContent = err.message; }
  }
}

startLiveBtn.addEventListener('click', startLiveDetection);
if (startScreenBtn) startScreenBtn.addEventListener('click', startScreenDetection);
if (startFileBtn) startFileBtn.addEventListener('click', () => liveFileInput.click());
if (liveFileInput) liveFileInput.addEventListener('change', () => {
  if (liveFileInput.files.length) startFileDetection(liveFileInput.files[0]);
});
stopLiveBtn.addEventListener('click', () => stopLiveDetection(false));
loadSmsStatus();
