/**
 * api.js — thin wrapper around the Flask REST API.
 * Attaches the JWT token from localStorage automatically.
 */
const api = (() => {
  const BASE = '';

  function authHeaders() {
    const token = localStorage.getItem('cds_token');
    return token ? { Authorization: `Bearer ${token}` } : {};
  }

  async function handle(res) {
    let body = null;
    try { body = await res.json(); } catch (e) { /* no body */ }
    if (!res.ok) {
      if (res.status === 401) {
        localStorage.removeItem('cds_token');
        localStorage.removeItem('cds_user');
        if (!location.pathname.endsWith('index.html') && location.pathname !== '/') {
          location.href = '/index.html';
        }
      }
      throw new Error((body && body.error) || `Request failed (${res.status})`);
    }
    return body;
  }

  return {
    async login(username, password) {
      const res = await fetch(`${BASE}/api/login`, {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ username, password }),
      });
      return handle(res);
    },

    async register(username, password) {
      const res = await fetch(`${BASE}/api/register`, {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ username, password }),
      });
      return handle(res);
    },

    async uploadVideo(file, meta) {
      const form = new FormData();
      form.append('video', file);
      form.append('location_name', meta.location_name || 'Unknown');
      form.append('latitude', meta.latitude || 0);
      form.append('longitude', meta.longitude || 0);
      const res = await fetch(`${BASE}/api/upload`, {
        method: 'POST',
        headers: { ...authHeaders() },
        body: form,
      });
      return handle(res);
    },

    async startProcessing(videoId) {
      const res = await fetch(`${BASE}/api/process/${videoId}`, {
        method: 'POST',
        headers: { ...authHeaders() },
      });
      return handle(res);
    },

    async processingStatus(videoId) {
      const res = await fetch(`${BASE}/api/process/${videoId}/status`, {
        headers: { ...authHeaders() },
      });
      return handle(res);
    },

    async startLive(meta = {}) {
      const res = await fetch(`${BASE}/api/live/start`, {
        method: 'POST',
        headers: { 'Content-Type': 'application/json', ...authHeaders() },
        body: JSON.stringify(meta),
      });
      return handle(res);
    },

    async sendLiveFrame(sessionId, image) {
      const res = await fetch(`${BASE}/api/live/frame`, {
        method: 'POST',
        headers: { 'Content-Type': 'application/json', ...authHeaders() },
        body: JSON.stringify({ session_id: sessionId, image }),
      });
      return handle(res);
    },

    async stopLive(sessionId) {
      const res = await fetch(`${BASE}/api/live/stop`, {
        method: 'POST',
        headers: { 'Content-Type': 'application/json', ...authHeaders() },
        body: JSON.stringify({ session_id: sessionId }),
      });
      return handle(res);
    },

    async smsNotificationStatus() {
      const res = await fetch(`${BASE}/api/notifications/sms/status`, {
        headers: { ...authHeaders() },
      });
      return handle(res);
    },

    async listIncidents(filters = {}) {
      const params = new URLSearchParams(filters).toString();
      const res = await fetch(`${BASE}/api/incidents${params ? '?' + params : ''}`, {
        headers: { ...authHeaders() },
      });
      return handle(res);
    },

    async getIncident(id) {
      const res = await fetch(`${BASE}/api/incidents/${id}`, { headers: { ...authHeaders() } });
      return handle(res);
    },

    async deleteIncident(id) {
      const res = await fetch(`${BASE}/api/incidents/${id}`, {
        method: 'DELETE',
        headers: { ...authHeaders() },
      });
      return handle(res);
    },

    async updateIncidentStatus(id, status) {
      const res = await fetch(`${BASE}/api/incidents/${id}/status`, {
        method: 'PUT',
        headers: { 'Content-Type': 'application/json', ...authHeaders() },
        body: JSON.stringify({ status }),
      });
      return handle(res);
    },

    async stats() {
      const res = await fetch(`${BASE}/api/stats`, { headers: { ...authHeaders() } });
      return handle(res);
    },

    async listVideos() {
      const res = await fetch(`${BASE}/api/videos`, { headers: { ...authHeaders() } });
      return handle(res);
    },
  };
})();
