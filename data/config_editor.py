import json
import os
import logging
from functools import wraps
from flask import Flask, jsonify, request, Response, session

app = Flask(__name__)
app.secret_key = os.getenv('SECRET_KEY', os.urandom(24))

UI_USER = os.getenv('UI_USER', '')
UI_PASS = os.getenv('UI_PASS', '')

logging.basicConfig(level=logging.INFO, format='%(asctime)s - %(levelname)s - %(message)s')

CONFIG_PATH = './config.json'
ACCOUNT_COMMENT = 'Query all terminals or partial, mode value below is full or include or skip'
VALID_MODES = ('full', 'include', 'skip')
CONTAINER_NAMES = {'pipeline': 'starshield.v2', 'editor': 'starshield.config-editor'}


def require_auth(f):
    @wraps(f)
    def decorated(*args, **kwargs):
        if UI_USER and not session.get('authenticated'):
            return jsonify({'error': 'unauthorized'}), 401
        return f(*args, **kwargs)
    return decorated


def load_config():
    with open(CONFIG_PATH, 'r') as f:
        return json.load(f)


def save_config(data):
    tmp = CONFIG_PATH + '.tmp'
    with open(tmp, 'w') as f:
        json.dump(data, f, indent=4)
    os.replace(tmp, CONFIG_PATH)


def get_accounts(data):
    return data['config']['authentication']['accounts']


def validate(body):
    errors = []
    if not str(body.get('account_num', '')).strip():
        errors.append('account_num is required')
    if not str(body.get('client_id', '')).strip():
        errors.append('client_id is required')
    aq = body.get('accountquery', {})
    mode = aq.get('mode', '')
    if mode not in VALID_MODES:
        errors.append('mode must be full, include, or skip')
    if mode == 'include' and not aq.get('service_lines'):
        errors.append('service_lines must be non-empty when mode is include')
    return errors


@app.route('/')
def index():
    return Response(HTML, mimetype='text/html')


@app.route('/api/login', methods=['POST'])
def login():
    body = request.get_json(force=True)
    if UI_USER and body.get('username') == UI_USER and body.get('password') == UI_PASS:
        session['authenticated'] = True
        return jsonify({'ok': True})
    return jsonify({'error': 'Invalid credentials'}), 401


@app.route('/api/logout', methods=['POST'])
def logout():
    session.clear()
    return jsonify({'ok': True})


@app.route('/api/accounts', methods=['GET'])
@require_auth
def list_accounts():
    return jsonify(get_accounts(load_config()))


@app.route('/api/accounts', methods=['POST'])
@require_auth
def add_account():
    body = request.get_json(force=True)
    errors = validate(body)
    if errors:
        return jsonify({'error': '; '.join(errors)}), 400

    data = load_config()
    accounts = get_accounts(data)
    account_num = body['account_num'].strip()

    if any(a['account_num'] == account_num for a in accounts):
        return jsonify({'error': f'{account_num} already exists'}), 409

    aq = body['accountquery']
    account = {
        'account_num': account_num,
        'client_id': body['client_id'].strip(),
        'client_secret': body.get('client_secret', '').strip() or '_secret_',
        'accountquery': {
            '_comment': ACCOUNT_COMMENT,
            'mode': aq['mode'],
            'service_lines': aq.get('service_lines', [])
        }
    }
    accounts.append(account)
    save_config(data)
    logging.info(f'Added account {account_num}')
    return jsonify(account), 201


@app.route('/api/accounts/<path:account_num>', methods=['PUT'])
@require_auth
def update_account(account_num):
    body = request.get_json(force=True)
    errors = validate(body)
    if errors:
        return jsonify({'error': '; '.join(errors)}), 400

    data = load_config()
    accounts = get_accounts(data)

    for i, a in enumerate(accounts):
        if a['account_num'] == account_num:
            aq = body['accountquery']
            accounts[i] = {
                'account_num': a['account_num'],
                'client_id': body['client_id'].strip(),
                'client_secret': body.get('client_secret', '').strip() or '_secret_',
                'accountquery': {
                    '_comment': a.get('accountquery', {}).get('_comment', ACCOUNT_COMMENT),
                    'mode': aq['mode'],
                    'service_lines': aq.get('service_lines', [])
                }
            }
            save_config(data)
            logging.info(f'Updated account {account_num}')
            return jsonify(accounts[i])

    return jsonify({'error': f'{account_num} not found'}), 404


@app.route('/api/accounts/<path:account_num>', methods=['DELETE'])
@require_auth
def delete_account(account_num):
    data = load_config()
    accounts = get_accounts(data)

    for i, a in enumerate(accounts):
        if a['account_num'] == account_num:
            del accounts[i]
            save_config(data)
            logging.info(f'Deleted account {account_num}')
            return jsonify({'deleted': account_num})

    return jsonify({'error': f'{account_num} not found'}), 404


@app.route('/api/logs/stream')
@require_auth
def stream_logs():
    key = request.args.get('container', 'pipeline')
    container_name = CONTAINER_NAMES.get(key)
    if not container_name:
        return jsonify({'error': 'unknown container'}), 400

    def generate():
        import docker
        client = docker.from_env()
        try:
            container = client.containers.get(container_name)
            for chunk in container.logs(stream=True, follow=True, tail=200):
                line = chunk.decode('utf-8', errors='replace').rstrip('\n')
                for part in line.split('\n'):
                    part = part.strip()
                    if part:
                        yield f'data: {part}\n\n'
        except GeneratorExit:
            pass
        except Exception as e:
            yield f'data: [error: {e}]\n\n'
        finally:
            try:
                client.close()
            except Exception:
                pass

    return Response(generate(), mimetype='text/event-stream',
                    headers={'Cache-Control': 'no-cache', 'X-Accel-Buffering': 'no'})


HTML = r"""<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="UTF-8">
<meta name="viewport" content="width=device-width, initial-scale=1.0">
<title>Starshield Account Manager</title>
<style>
  * { box-sizing: border-box; margin: 0; padding: 0; }
  body { font-family: 'Segoe UI', sans-serif; background: #0f1923; color: #c9d1d9; height: 100vh; display: flex; flex-direction: column; overflow: hidden; }
  /* Header */
  header { background: #161b22; border-bottom: 1px solid #30363d; padding: 0 20px; height: 52px; display: flex; align-items: center; gap: 10px; flex-shrink: 0; }
  header h1 { font-size: 16px; font-weight: 600; color: #e6edf3; }
  .header-meta { font-size: 12px; color: #8b949e; margin-left: 8px; }
  .header-right { margin-left: auto; display: flex; align-items: center; gap: 10px; }
  /* Main layout */
  .main { display: flex; flex: 1; overflow: hidden; }
  /* Left panel — accounts */
  .left-panel { display: flex; flex-direction: column; flex: 0 0 62%; min-width: 220px; border-right: none; overflow: hidden; }
  /* Resizer */
  .resizer { width: 5px; background: #21262d; cursor: col-resize; flex-shrink: 0; transition: background .15s; }
  .resizer:hover, .resizer.dragging { background: #388bfd; }
  .panel-toolbar { display: flex; align-items: center; justify-content: space-between; padding: 10px 16px; border-bottom: 1px solid #30363d; flex-shrink: 0; background: #161b22; }
  .panel-toolbar h2 { font-size: 13px; color: #8b949e; font-weight: 400; }
  .table-wrap { flex: 1; overflow-y: auto; }
  table { width: 100%; border-collapse: collapse; }
  th { background: #161b22; color: #8b949e; font-size: 11px; font-weight: 600; text-transform: uppercase; letter-spacing: .5px; padding: 9px 14px; text-align: left; border-bottom: 1px solid #30363d; position: sticky; top: 0; z-index: 1; }
  td { padding: 9px 14px; font-size: 13px; border-bottom: 1px solid #21262d; vertical-align: middle; }
  tr:last-child td { border-bottom: none; }
  tr:hover td { background: #1c2128; }
  .error-msg { color: #f85149; font-size: 12px; padding: 8px 16px; display: none; }
  /* Right panel — logs */
  .right-panel { display: flex; flex-direction: column; flex: 1; overflow: hidden; background: #0d1117; }
  .log-toolbar { display: flex; align-items: center; gap: 8px; padding: 10px 14px; border-bottom: 1px solid #30363d; flex-shrink: 0; background: #161b22; flex-wrap: wrap; }
  .log-toolbar-title { font-size: 13px; color: #8b949e; font-weight: 400; flex: 1; }
  .log-output { flex: 1; overflow-y: auto; padding: 8px 14px; font-family: 'Consolas', 'Courier New', monospace; font-size: 12px; line-height: 1.6; color: #c9d1d9; }
  .log-output .err { color: #f85149; }
  .log-output .warn { color: #d29922; }
  /* Shared buttons */
  button { cursor: pointer; border: none; border-radius: 6px; font-size: 13px; padding: 5px 12px; font-family: inherit; transition: background .15s; }
  .btn-primary { background: #238636; color: #fff; }
  .btn-primary:hover { background: #2ea043; }
  .btn-sm { font-size: 11px; padding: 3px 9px; }
  .btn-edit { background: #1f6feb; color: #fff; }
  .btn-edit:hover { background: #388bfd; }
  .btn-delete { background: #b91c1c; color: #fff; }
  .btn-delete:hover { background: #dc2626; }
  .btn-ghost { background: #21262d; color: #c9d1d9; }
  .btn-ghost:hover { background: #30363d; }
  .btn-connect { background: #238636; color: #fff; }
  .btn-connect:hover { background: #2ea043; }
  .btn-disconnect { background: #6e7681; color: #fff; }
  .btn-disconnect:hover { background: #8b949e; }
  .actions { display: flex; gap: 5px; }
  /* Log selector pills */
  .log-selector { display: flex; gap: 4px; }
  .log-selector button { font-size: 11px; padding: 3px 10px; background: #21262d; color: #8b949e; border-radius: 12px; }
  .log-selector button.active { background: #1f6feb; color: #fff; }
  /* Status dot */
  .log-status { font-size: 11px; color: #8b949e; }
  .log-status.live::before { content: '● '; color: #3fb950; }
  /* Badges */
  .badge { display: inline-block; padding: 2px 8px; border-radius: 12px; font-size: 11px; font-weight: 600; }
  .badge-full { background: #0d4429; color: #3fb950; }
  .badge-include { background: #0c2d6b; color: #79c0ff; }
  .badge-skip { background: #3d1f00; color: #d29922; }
  .count { color: #8b949e; font-size: 12px; }
  /* Overlays & modals */
  .overlay { display: none; position: fixed; inset: 0; background: rgba(0,0,0,.65); z-index: 200; align-items: center; justify-content: center; }
  .overlay.active { display: flex; }
  .modal { background: #161b22; border: 1px solid #30363d; border-radius: 10px; width: 460px; max-width: 95vw; padding: 24px; }
  .modal h3 { font-size: 16px; margin-bottom: 18px; color: #e6edf3; }
  .field { margin-bottom: 14px; }
  label { display: block; font-size: 12px; color: #8b949e; margin-bottom: 5px; font-weight: 500; }
  input[type=text], input[type=password], select, textarea {
    width: 100%; background: #0d1117; border: 1px solid #30363d; border-radius: 6px;
    color: #e6edf3; font-size: 13px; padding: 7px 10px; font-family: monospace;
  }
  input[type=text]:focus, input[type=password]:focus, select:focus, textarea:focus { outline: none; border-color: #388bfd; }
  textarea { resize: vertical; min-height: 80px; }
  select option { background: #161b22; }
  .modal-footer { display: flex; justify-content: flex-end; gap: 8px; margin-top: 20px; }
  .modal-err { color: #f85149; font-size: 12px; margin-top: 8px; display: none; }
  #slField { display: none; }
  /* Login modal specifics */
  .login-modal { width: 340px; }
  .login-modal h3 { text-align: center; margin-bottom: 6px; }
  .login-subtitle { text-align: center; font-size: 12px; color: #8b949e; margin-bottom: 22px; }
  .login-modal .modal-footer { justify-content: stretch; }
  .login-modal .modal-footer button { flex: 1; padding: 8px; font-size: 14px; }
</style>
</head>
<body>

<header>
  <svg width="18" height="18" viewBox="0 0 24 24" fill="none" stroke="#58a6ff" stroke-width="2"><circle cx="12" cy="12" r="10"/><line x1="2" y1="12" x2="22" y2="12"/><path d="M12 2a15.3 15.3 0 0 1 4 10 15.3 15.3 0 0 1-4 10 15.3 15.3 0 0 1-4-10 15.3 15.3 0 0 1 4-10z"/></svg>
  <h1>Starshield Account Manager</h1>
  <span class="header-meta" id="acctCount"></span>
  <div class="header-right">
    <button class="btn-ghost btn-sm" id="btnLogout" onclick="logout()" style="display:none">Sign out</button>
  </div>
</header>

<div class="main">

  <!-- Left: accounts -->
  <div class="left-panel">
    <div class="panel-toolbar">
      <h2 id="tableLabel">Loading accounts...</h2>
      <button class="btn-primary btn-sm" onclick="openAdd()">+ Add Account</button>
    </div>
    <div class="table-wrap">
      <table>
        <thead>
          <tr>
            <th>Account #</th>
            <th>Client ID</th>
            <th>Mode</th>
            <th>SL</th>
            <th>Actions</th>
          </tr>
        </thead>
        <tbody id="tbody"></tbody>
      </table>
    </div>
    <div class="error-msg" id="tableErr"></div>
  </div>

  <div class="resizer" id="resizer"></div>

  <!-- Right: logs -->
  <div class="right-panel">
    <div class="log-toolbar">
      <span class="log-toolbar-title">Logs</span>
      <div class="log-selector">
        <button id="btnPipeline" class="active" onclick="switchContainer('pipeline')">Pipeline</button>
        <button id="btnEditor" onclick="switchContainer('editor')">Config Editor</button>
      </div>
      <span class="log-status" id="logStatus">Disconnected</span>
      <button class="btn-connect btn-sm" id="btnConnect" onclick="connectLog()">Connect</button>
      <button class="btn-disconnect btn-sm" id="btnDisconnect" onclick="disconnectLog()" style="display:none">Disconnect</button>
      <button class="btn-ghost btn-sm" onclick="clearLog()">Clear</button>
    </div>
    <div class="log-output" id="logOutput"></div>
  </div>

</div>

<!-- Login overlay -->
<div class="overlay active" id="loginOverlay">
  <div class="modal login-modal">
    <h3>Sign In</h3>
    <p class="login-subtitle">Starshield Account Manager</p>
    <div class="field">
      <label>Username</label>
      <input type="text" id="lUser" autocomplete="username" onkeydown="if(event.key==='Enter')doLogin()">
    </div>
    <div class="field">
      <label>Password</label>
      <input type="password" id="lPass" autocomplete="current-password" onkeydown="if(event.key==='Enter')doLogin()">
    </div>
    <div class="modal-err" id="loginErr"></div>
    <div class="modal-footer">
      <button class="btn-primary" onclick="doLogin()">Sign In</button>
    </div>
  </div>
</div>

<!-- Delete confirmation modal -->
<div class="overlay" id="delOverlay">
  <div class="modal" style="width:380px">
    <h3 style="color:#f85149">Delete Account</h3>
    <p style="margin:14px 0 6px;font-size:13px;color:#8b949e">This will permanently remove:</p>
    <p id="delAcctNum" style="font-family:monospace;color:#e6edf3;font-size:13px;margin-bottom:18px;word-break:break-all"></p>
    <div class="modal-footer">
      <button class="btn-ghost" onclick="closeDelModal()">Cancel</button>
      <button class="btn-delete" onclick="confirmDelete()">Delete</button>
    </div>
  </div>
</div>

<!-- Add / Edit modal -->
<div class="overlay" id="overlay">
  <div class="modal">
    <h3 id="modalTitle">Add Account</h3>
    <div class="field">
      <label>Account #</label>
      <input type="text" id="fAccountNum" placeholder="ACC-XXXXXXX-XXXXX-XX">
    </div>
    <div class="field">
      <label>Client ID</label>
      <input type="text" id="fClientId" placeholder="UUID">
    </div>
    <div class="field">
      <label>Client Secret</label>
      <input type="text" id="fClientSecret" placeholder="_secret_">
    </div>
    <div class="field">
      <label>Mode</label>
      <select id="fMode" onchange="onModeChange()">
        <option value="full">full</option>
        <option value="include">include</option>
        <option value="skip">skip</option>
      </select>
    </div>
    <div class="field" id="slField">
      <label>Service Lines (one per line)</label>
      <textarea id="fServiceLines" placeholder="SL-XXXXXXXX-XXXXX-XX"></textarea>
    </div>
    <div class="modal-err" id="modalErr"></div>
    <div class="modal-footer">
      <button class="btn-ghost" onclick="closeModal()">Cancel</button>
      <button class="btn-primary" onclick="saveAccount()">Save</button>
    </div>
  </div>
</div>

<script>
let editing = null;

// ---- Auth ----
async function doLogin() {
  const username = document.getElementById('lUser').value;
  const password = document.getElementById('lPass').value;
  showErr('loginErr', '');
  try {
    const r = await fetch('/api/login', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ username, password })
    });
    const d = await r.json();
    if (!r.ok) { showErr('loginErr', d.error || 'Login failed'); return; }
    document.getElementById('loginOverlay').classList.remove('active');
    document.getElementById('btnLogout').style.display = '';
    loadAccounts();
  } catch (e) {
    showErr('loginErr', 'Request failed: ' + e.message);
  }
}

async function logout() {
  await fetch('/api/logout', { method: 'POST' });
  disconnectLog();
  document.getElementById('tbody').innerHTML = '';
  document.getElementById('tableLabel').textContent = '';
  document.getElementById('acctCount').textContent = '';
  document.getElementById('btnLogout').style.display = 'none';
  document.getElementById('lPass').value = '';
  showErr('loginErr', '');
  document.getElementById('loginOverlay').classList.add('active');
  setTimeout(() => document.getElementById('lUser').focus(), 50);
}

// ---- Accounts ----
async function loadAccounts() {
  try {
    const r = await fetch('/api/accounts');
    if (r.status === 401) {
      document.getElementById('loginOverlay').classList.add('active');
      setTimeout(() => document.getElementById('lUser').focus(), 50);
      return;
    }
    if (!r.ok) throw new Error(await r.text());
    const accounts = await r.json();
    renderTable(accounts);
    document.getElementById('acctCount').textContent = accounts.length + ' accounts';
    document.getElementById('tableLabel').textContent = accounts.length + ' accounts configured';
    document.getElementById('btnLogout').style.display = '';
  } catch (e) {
    showErr('tableErr', 'Failed to load: ' + e.message);
  }
}

function badgeClass(mode) {
  return { full: 'badge-full', include: 'badge-include', skip: 'badge-skip' }[mode] || '';
}

function renderTable(accounts) {
  const tbody = document.getElementById('tbody');
  if (!accounts.length) {
    tbody.innerHTML = '<tr><td colspan="5" style="text-align:center;color:#8b949e;padding:24px">No accounts configured</td></tr>';
    return;
  }
  tbody.innerHTML = accounts.map(a => `
    <tr>
      <td style="font-family:monospace;font-size:12px">${esc(a.account_num)}</td>
      <td style="font-family:monospace;font-size:12px;color:#8b949e">${esc(a.client_id)}</td>
      <td><span class="badge ${badgeClass(a.accountquery.mode)}">${esc(a.accountquery.mode)}</span></td>
      <td class="count">${a.accountquery.service_lines.length || '—'}</td>
      <td><div class="actions">
        <button class="btn-edit btn-sm" onclick='openEdit(${JSON.stringify(a)})'>Edit</button>
        <button class="btn-delete btn-sm" onclick='deleteAccount(${JSON.stringify(a.account_num)})'>Delete</button>
      </div></td>
    </tr>`).join('');
}

function esc(s) {
  return String(s).replace(/&/g,'&amp;').replace(/</g,'&lt;').replace(/>/g,'&gt;');
}

function openAdd() {
  editing = null;
  document.getElementById('modalTitle').textContent = 'Add Account';
  document.getElementById('fAccountNum').value = '';
  document.getElementById('fAccountNum').disabled = false;
  document.getElementById('fClientId').value = '';
  document.getElementById('fClientSecret').value = '';
  document.getElementById('fMode').value = 'full';
  document.getElementById('fServiceLines').value = '';
  onModeChange();
  showErr('modalErr', '');
  document.getElementById('overlay').classList.add('active');
}

function openEdit(a) {
  editing = a.account_num;
  document.getElementById('modalTitle').textContent = 'Edit Account';
  document.getElementById('fAccountNum').value = a.account_num;
  document.getElementById('fAccountNum').disabled = true;
  document.getElementById('fClientId').value = a.client_id;
  document.getElementById('fClientSecret').value = a.client_secret || '_secret_';
  document.getElementById('fMode').value = a.accountquery.mode;
  document.getElementById('fServiceLines').value = (a.accountquery.service_lines || []).join('\n');
  onModeChange();
  showErr('modalErr', '');
  document.getElementById('overlay').classList.add('active');
}

function closeModal() { document.getElementById('overlay').classList.remove('active'); }

function onModeChange() {
  document.getElementById('slField').style.display =
    document.getElementById('fMode').value === 'include' ? 'block' : 'none';
}

async function saveAccount() {
  const account_num = document.getElementById('fAccountNum').value.trim();
  const client_id   = document.getElementById('fClientId').value.trim();
  const client_secret = document.getElementById('fClientSecret').value.trim() || '_secret_';
  const mode = document.getElementById('fMode').value;
  const service_lines = document.getElementById('fServiceLines').value.split('\n').map(s => s.trim()).filter(Boolean);
  const body = { account_num, client_id, client_secret, accountquery: { mode, service_lines } };
  const url    = editing ? `/api/accounts/${encodeURIComponent(editing)}` : '/api/accounts';
  const method = editing ? 'PUT' : 'POST';
  try {
    const r = await fetch(url, { method, headers: {'Content-Type':'application/json'}, body: JSON.stringify(body) });
    const d = await r.json();
    if (!r.ok) { showErr('modalErr', d.error || 'Save failed'); return; }
    closeModal();
    loadAccounts();
  } catch (e) {
    showErr('modalErr', 'Request failed: ' + e.message);
  }
}

let pendingDelete = null;

function deleteAccount(account_num) {
  pendingDelete = account_num;
  document.getElementById('delAcctNum').textContent = account_num;
  document.getElementById('delOverlay').classList.add('active');
}

function closeDelModal() {
  pendingDelete = null;
  document.getElementById('delOverlay').classList.remove('active');
}

async function confirmDelete() {
  if (!pendingDelete) return;
  const account_num = pendingDelete;
  closeDelModal();
  try {
    const r = await fetch(`/api/accounts/${encodeURIComponent(account_num)}`, { method: 'DELETE' });
    if (!r.ok) { const d = await r.json(); showErr('tableErr', d.error || 'Delete failed'); return; }
    loadAccounts();
  } catch (e) {
    showErr('tableErr', 'Request failed: ' + e.message);
  }
}

function showErr(id, msg) {
  const el = document.getElementById(id);
  el.textContent = msg;
  el.style.display = msg ? 'block' : 'none';
}

document.getElementById('overlay').addEventListener('click', e => {
  if (e.target === document.getElementById('overlay')) closeModal();
});
document.getElementById('delOverlay').addEventListener('click', e => {
  if (e.target === document.getElementById('delOverlay')) closeDelModal();
});

// ---- Logs ----
let logEs = null;
let logContainer = 'pipeline';
let autoScroll = true;

function switchContainer(key) {
  logContainer = key;
  document.getElementById('btnPipeline').classList.toggle('active', key === 'pipeline');
  document.getElementById('btnEditor').classList.toggle('active', key === 'editor');
  if (logEs) { disconnectLog(); connectLog(); }
}

function connectLog() {
  if (logEs) disconnectLog();
  logEs = new EventSource(`/api/logs/stream?container=${logContainer}`);
  logEs.onopen = () => setLogStatus(true);
  logEs.onerror = () => setLogStatus(false);
  logEs.onmessage = e => appendLog(e.data);
  document.getElementById('btnConnect').style.display = 'none';
  document.getElementById('btnDisconnect').style.display = '';
}

function disconnectLog() {
  if (logEs) { logEs.close(); logEs = null; }
  setLogStatus(false);
  document.getElementById('btnConnect').style.display = '';
  document.getElementById('btnDisconnect').style.display = 'none';
}

function clearLog() { document.getElementById('logOutput').innerHTML = ''; }

function setLogStatus(live) {
  const el = document.getElementById('logStatus');
  el.textContent = live ? 'Live' : 'Disconnected';
  el.className = 'log-status' + (live ? ' live' : '');
}

function appendLog(line) {
  const out = document.getElementById('logOutput');
  const div = document.createElement('div');
  const lower = line.toLowerCase();
  if (lower.includes('error') || lower.includes('exception')) div.className = 'err';
  else if (lower.includes('warning') || lower.includes('warn')) div.className = 'warn';
  div.textContent = line;
  out.appendChild(div);
  if (autoScroll) out.scrollTop = out.scrollHeight;
  while (out.children.length > 2000) out.removeChild(out.firstChild);
}

document.getElementById('logOutput').addEventListener('scroll', () => {
  const out = document.getElementById('logOutput');
  autoScroll = out.scrollTop + out.clientHeight >= out.scrollHeight - 10;
});

// ---- Resizer ----
(function() {
  const resizer   = document.getElementById('resizer');
  const leftPanel = document.querySelector('.left-panel');

  resizer.addEventListener('mousedown', e => {
    e.preventDefault();
    const startX     = e.clientX;
    const startWidth = leftPanel.getBoundingClientRect().width;
    const main       = document.querySelector('.main');
    const mainWidth  = main.getBoundingClientRect().width;

    resizer.classList.add('dragging');
    document.body.style.cursor     = 'col-resize';
    document.body.style.userSelect = 'none';

    function onMove(e) {
      const newWidth = Math.min(Math.max(220, startWidth + e.clientX - startX), mainWidth - 220);
      leftPanel.style.flex = `0 0 ${newWidth}px`;
    }

    function onUp() {
      resizer.classList.remove('dragging');
      document.body.style.cursor     = '';
      document.body.style.userSelect = '';
      document.removeEventListener('mousemove', onMove);
      document.removeEventListener('mouseup',   onUp);
    }

    document.addEventListener('mousemove', onMove);
    document.addEventListener('mouseup',   onUp);
  });
})();

// Bootstrap: try loading accounts; show login if 401
loadAccounts();
</script>
</body>
</html>"""


if __name__ == '__main__':
    app.run()
