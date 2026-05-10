"""
Self-contained HTML dashboard for ChatGPT DOM Agent Bridge.
Provides real-time system status, metrics, and session monitoring.
"""


DASHBOARD_HTML = """<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="UTF-8">
<meta name="viewport" content="width=device-width, initial-scale=1.0">
<meta http-equiv="refresh" content="5">
<title>ChatGPT DOM Agent Bridge — Dashboard</title>
<style>
  * { box-sizing: border-box; margin: 0; padding: 0; }
  body {
    font-family: 'Segoe UI', -apple-system, BlinkMacSystemFont, sans-serif;
    background: #0d1117;
    color: #e6edf3;
    padding: 24px;
  }
  .container { max-width: 1400px; margin: 0 auto; }
  h1 { font-size: 1.5rem; margin-bottom: 8px; color: #58a6ff; }
  .subtitle { font-size: 0.9rem; color: #8b949e; margin-bottom: 20px; }

  .status-badge {
    display: inline-block;
    padding: 4px 12px;
    border-radius: 4px;
    font-size: 0.8rem;
    font-weight: 600;
    margin-bottom: 20px;
  }
  .healthy { background: #1a4a2e; color: #3fb950; }
  .degraded { background: #3d2a00; color: #d29922; }
  .unhealthy { background: #3d0000; color: #f85149; }

  .grid {
    display: grid;
    grid-template-columns: repeat(auto-fit, minmax(200px, 1fr));
    gap: 16px;
    margin-bottom: 24px;
  }
  .card {
    background: #161b22;
    border: 1px solid #30363d;
    border-radius: 8px;
    padding: 20px;
    transition: border-color 0.2s;
  }
  .card:hover { border-color: #58a6ff; }
  .card h3 {
    font-size: 0.75rem;
    color: #8b949e;
    margin-bottom: 10px;
    text-transform: uppercase;
    letter-spacing: 0.5px;
  }
  .card .value {
    font-size: 2rem;
    font-weight: 700;
    color: #58a6ff;
  }
  .card .sub {
    font-size: 0.75rem;
    color: #8b949e;
    margin-top: 4px;
  }

  .section-title {
    font-size: 1.1rem;
    color: #e6edf3;
    margin-bottom: 16px;
    margin-top: 32px;
    border-bottom: 1px solid #30363d;
    padding-bottom: 12px;
  }

  table {
    width: 100%;
    border-collapse: collapse;
    background: #161b22;
    border-radius: 8px;
    overflow: hidden;
    margin-bottom: 24px;
  }
  thead { background: #21262d; }
  th {
    color: #8b949e;
    padding: 12px 16px;
    text-align: left;
    font-size: 0.8rem;
    font-weight: 600;
    border-bottom: 1px solid #30363d;
  }
  td {
    padding: 12px 16px;
    border-bottom: 1px solid #30363d;
    font-size: 0.85rem;
  }
  tr:last-child td { border-bottom: none; }

  code {
    background: #0d1117;
    padding: 2px 6px;
    border-radius: 3px;
    font-family: 'Courier New', monospace;
    font-size: 0.8rem;
  }

  .status-idle { color: #3fb950; }
  .status-busy { color: #58a6ff; }
  .status-error { color: #f85149; }

  .loading { color: #8b949e; font-style: italic; }
  .error-message { color: #f85149; }

  footer {
    margin-top: 32px;
    font-size: 0.75rem;
    color: #8b949e;
    border-top: 1px solid #30363d;
    padding-top: 16px;
  }

  .metrics-row { display: flex; gap: 12px; margin-bottom: 12px; }
  .metric-item { flex: 1; background: #0d1117; padding: 8px 12px; border-radius: 4px; }
  .metric-label { font-size: 0.7rem; color: #8b949e; }
  .metric-value { font-size: 1rem; color: #58a6ff; font-weight: 600; }
</style>
</head>
<body>
<div class="container">
  <h1>⚡ ChatGPT DOM Agent Bridge</h1>
  <p class="subtitle">Real-time system monitoring and session management</p>

  <div id="status-container"></div>

  <div class="grid" id="metrics-grid">
    <div class="card"><h3>Status</h3><div class="value" id="status">—</div><div class="sub">System health</div></div>
    <div class="card"><h3>Uptime</h3><div class="value" id="uptime">—</div><div class="sub">Hours running</div></div>
    <div class="card"><h3>Total Requests</h3><div class="value" id="total-requests">—</div><div class="sub">All time</div></div>
    <div class="card"><h3>Success Rate</h3><div class="value" id="success-rate">—</div><div class="sub">%</div></div>
    <div class="card"><h3>Avg Latency</h3><div class="value" id="avg-latency">—</div><div class="sub">ms</div></div>
    <div class="card"><h3>P95 Latency</h3><div class="value" id="p95-latency">—</div><div class="sub">ms</div></div>
    <div class="card"><h3>Queue Length</h3><div class="value" id="queue-length">—</div><div class="sub">Pending</div></div>
    <div class="card"><h3>Active Sessions</h3><div class="value" id="active-sessions">—</div><div class="sub">Browser tabs</div></div>
  </div>

  <h2 class="section-title">Browser Sessions</h2>
  <table id="sessions-table">
    <thead>
      <tr>
        <th>Session ID</th>
        <th>Status</th>
        <th>Conversation</th>
        <th>Requests</th>
        <th>Errors</th>
        <th>Last Activity</th>
      </tr>
    </thead>
    <tbody id="sessions-body">
      <tr><td colspan="6" style="text-align:center;color:#8b949e">Loading sessions...</td></tr>
    </tbody>
  </table>

  <h2 class="section-title">Queue Status</h2>
  <div class="metrics-row">
    <div class="metric-item">
      <div class="metric-label">Pending Requests</div>
      <div class="metric-value" id="queue-pending">—</div>
    </div>
    <div class="metric-item">
      <div class="metric-label">Completed</div>
      <div class="metric-value" id="queue-completed">—</div>
    </div>
    <div class="metric-item">
      <div class="metric-label">Failed</div>
      <div class="metric-value" id="queue-failed">—</div>
    </div>
  </div>

  <h2 class="section-title">Recovery Events</h2>
  <div class="metrics-row">
    <div class="metric-item">
      <div class="metric-label">Total Recoveries</div>
      <div class="metric-value" id="recovery-total">—</div>
    </div>
    <div class="metric-item">
      <div class="metric-label">Redis Connected</div>
      <div class="metric-value" id="redis-status">—</div>
    </div>
  </div>

  <footer>
    <p>Dashboard auto-refreshes every 5 seconds</p>
    <p>ChatGPT DOM Agent Bridge v1.0.0 · Built with FastAPI and Prometheus</p>
  </footer>
</div>

<script>
async function loadDashboard() {
  try {
    // Fetch both status and metrics in parallel
    const [statusResp, metricsResp] = await Promise.all([
      fetch('/status').then(r => r.json()).catch(e => (console.error('Status fetch error:', e), null)),
      fetch('/metrics').then(r => r.json()).catch(e => (console.error('Metrics fetch error:', e), null)),
    ]);

    // Update status badge
    if (statusResp) {
      const badge = document.getElementById('status-container');
      badge.innerHTML = `<div class="status-badge ${statusResp.status || 'unhealthy'}">${(statusResp.status || 'Unknown').toUpperCase()}</div>`;
      document.getElementById('status').textContent = statusResp.status || '—';
      document.getElementById('uptime').textContent = ((statusResp.uptime_s || 0) / 3600).toFixed(1) + 'h';
    }

    if (metricsResp) {
      document.getElementById('total-requests').textContent = metricsResp.total_requests || 0;
      document.getElementById('success-rate').textContent = ((metricsResp.success_rate || 0) * 100).toFixed(1);
      document.getElementById('avg-latency').textContent = (metricsResp.average_latency_ms || 0).toFixed(0);
      document.getElementById('p95-latency').textContent = (metricsResp.p95_latency_ms || 0).toFixed(0);
      document.getElementById('queue-length').textContent = metricsResp.queue_length || 0;
      document.getElementById('active-sessions').textContent = metricsResp.active_sessions || 0;
      document.getElementById('recovery-total').textContent = metricsResp.recovery_events || 0;
    }

    // Fetch sessions separately with retries
    try {
      const sessionsResp = await fetch('/sessions').then(r => r.json());
      const tbody = document.getElementById('sessions-body');
      if (sessionsResp && sessionsResp.sessions && Array.isArray(sessionsResp.sessions)) {
        tbody.innerHTML = sessionsResp.sessions.map(s => `
          <tr>
            <td><code>${s.session_id || '—'}</code></td>
            <td><span class="status-${s.status || 'unknown'}">${s.status || '—'}</span></td>
            <td><code>${s.conversation_id || '—'}</code></td>
            <td>${s.request_count || 0}</td>
            <td>${s.error_count || 0}</td>
            <td>${s.last_activity ? new Date(s.last_activity).toLocaleTimeString() : '—'}</td>
          </tr>
        `).join('');
      } else {
        tbody.innerHTML = '<tr><td colspan="6" class="loading">No sessions</td></tr>';
      }
    } catch (e) {
      console.error('Sessions fetch error:', e);
      document.getElementById('sessions-body').innerHTML = '<tr><td colspan="6" class="error-message">Failed to load sessions</td></tr>';
    }

    // Update Redis status
    if (statusResp) {
      document.getElementById('redis-status').textContent = statusResp.redis_connected ? '✓ Connected' : '✗ Disconnected';
      document.getElementById('redis-status').style.color = statusResp.redis_connected ? '#3fb950' : '#f85149';
    }

  } catch (e) {
    console.error('Dashboard load error:', e);
  }
}

// Initial load
loadDashboard();
// Refresh every 5 seconds
setInterval(loadDashboard, 5000);
</script>
</body>
</html>"""


def get_dashboard_html() -> str:
    """
    Get the complete dashboard HTML.

    Returns:
        Self-contained HTML string ready for rendering
    """
    return DASHBOARD_HTML
