/* Relay: offline, dependency-free browser client. */
(() => {
  'use strict';
  const finite = value => typeof value === 'number' && Number.isFinite(value);
  function formatBytes(value) {
    if (!finite(value) || value < 0) return '—';
    if (value === 0) return '0 B';
    const unit = Math.max(0, Math.min(4, Math.floor(Math.log(value) / Math.log(1024))));
    return `${Number((value / 1024 ** unit).toFixed(1))} ${['B', 'KiB', 'MiB', 'GiB', 'TiB'][unit]}`;
  }
  function formatDuration(value) {
    if (!finite(value) || value < 0) return '—';
    const seconds = Math.round(value);
    if (seconds < 60) return `${seconds}s`;
    if (seconds < 3600) return `${Math.floor(seconds / 60)}m ${seconds % 60}s`;
    return `${Math.floor(seconds / 3600)}h ${Math.floor((seconds % 3600) / 60)}m`;
  }
  function joinPath(directory, name) { return `${directory.replace(/\/+$/, '')}/${name}`; }
  function parentPath(path) { return path.replace(/\/+$/, '').split('/').slice(0, -1).join('/') || '/'; }
  function throughputSeries(samples) {
    const series = {plain: [], gzip: []};
    for (const sample of Array.isArray(samples) ? samples : []) {
      if (series[sample.mode] && finite(sample.elapsed) && sample.elapsed >= 0 && finite(sample.speed_bps) && sample.speed_bps >= 0) {
        series[sample.mode].push({x: sample.elapsed, y: sample.speed_bps / 1048576});
      }
    }
    for (const values of Object.values(series)) values.sort((a, b) => a.x - b.x);
    return series;
  }
  function comparisonMetrics(result) {
    const runs = result?.runs || [];
    const plain = runs.find(run => run.mode === 'plain');
    const gzip = runs.find(run => run.mode === 'gzip');
    return {
      reduction: gzip && finite(gzip.source_bytes) && gzip.source_bytes > 0 && finite(gzip.payload_bytes) ? Math.round((1 - gzip.payload_bytes / gzip.source_bytes) * 1000) / 10 : null,
      timeSaved: plain && gzip && finite(plain.total_seconds) && finite(gzip.total_seconds) ? plain.total_seconds - gzip.total_seconds : null,
      verified: Boolean(plain?.verified && gzip?.verified),
    };
  }
  function benchmarkRowValues(run) {
    const seconds = value => finite(value) ? `${value.toFixed(3)}s` : '—';
    const speed = value => finite(value) ? `${value.toFixed(2)} MiB/s` : '—';
    return [run.mode === 'plain' ? 'Original' : 'Gzip', formatBytes(run.payload_bytes), seconds(run.preparation_seconds), seconds(run.transfer_seconds), seconds(run.total_seconds), speed(run.throughput_mib_s), speed(run.effective_mib_s), run.verified ? 'Yes' : 'No'];
  }
  function createSessionGate() {
    let version = 0;
    return {
      invalidate() { version++; },
      async protect(promise) {
        const expected = version;
        try {
          const result = await promise;
          if (expected === version) return result;
        } catch (error) {
          if (expected === version) throw error;
        }
        const error = new Error('Your session changed. Please try again.');
        error.stale = true;
        throw error;
      },
    };
  }
  if (typeof module !== 'undefined' && module.exports) module.exports = {formatBytes, formatDuration, joinPath, parentPath, throughputSeries, comparisonMetrics, benchmarkRowValues, createSessionGate};
  if (typeof document === 'undefined') return;

  const $ = id => document.getElementById(id);
  const terminal = new Set(['completed', 'failed', 'cancelled']);
  const active = job => !terminal.has(job.status);
  const state = {session: null, devices: [], jobs: [], selectedId: null, path: '/', selectedFile: null, view: 'transfers', sending: false, sendGeneration: 0, staging: new Map(), sources: new Map(), requests: new Map(), filesGeneration: 0, polling: null, pollGeneration: 0, jobsGeneration: 0, benchmarkId: '', retryTarget: null};
  const sessionGate = createSessionGate();
  let noticeTimer;
  let confirmCallback = null;
  let connectDeviceId = null;
  let dialogOpener = null;

  function element(tag, className, text) {
    const node = document.createElement(tag);
    if (className) node.className = className;
    if (text !== undefined) node.textContent = String(text);
    return node;
  }
  function icon(kind) {
    const paths = {
      device: 'M4 4h16v11H4z M8 20h8 M12 15v5',
      folder: 'M3 7h7l2-3h8v16H3z',
      file: 'M6 3h8l4 4v14H6z M14 3v5h4 M9 12h6 M9 16h6',
    };
    const svg = document.createElementNS('http://www.w3.org/2000/svg', 'svg');
    svg.setAttribute('viewBox', '0 0 24 24');
    svg.setAttribute('fill', 'none');
    svg.setAttribute('stroke', 'currentColor');
    svg.setAttribute('stroke-linecap', 'round');
    svg.setAttribute('stroke-linejoin', 'round');
    svg.setAttribute('aria-hidden', 'true');
    const path = document.createElementNS(svg.namespaceURI, 'path');
    path.setAttribute('d', paths[kind] || paths.file);
    svg.append(path);
    return svg;
  }
  function actionButton(text, className, action, focusKey) {
    const button = element('button', className, text);
    button.type = 'button';
    if (focusKey) button.dataset.focusKey = focusKey;
    button.addEventListener('click', action);
    return button;
  }
  function showNotice(message, error = false) {
    clearTimeout(noticeTimer);
    $('notice').textContent = message;
    $('notice').classList.toggle('error', error);
    $('notice').hidden = false;
    noticeTimer = setTimeout(() => { $('notice').hidden = true; }, error ? 11000 : 5500);
  }
  function formError(id, message = '') { $(id).textContent = message; $(id).hidden = !message; }
  function selectedDevice() { return state.devices.find(device => String(device.id) === String(state.selectedId)); }
  async function api(path, options = {}) {
    const headers = {...options.headers};
    if (options.method && options.method !== 'GET') {
      headers['X-FTP-App'] = '1';
      if (state.session?.csrf) headers['X-CSRF-Token'] = state.session.csrf;
    }
    if (options.body !== undefined) { headers['Content-Type'] = 'application/json'; options.body = JSON.stringify(options.body); }
    const {response, data} = await sessionGate.protect((async () => {
      let response;
      try { response = await fetch(path, {...options, headers, credentials: 'same-origin'}); }
      catch { throw new Error('Relay could not reach the local app. Make sure it is still running, then try again.'); }
      const data = response.status === 204 ? {} : await response.json().catch(() => ({}));
      return {response, data};
    })());
    if (!response.ok) {
      if (response.status === 401 && !['/api/login', '/api/setup'].includes(path)) expireSession();
      const error = new Error(data.error || `The request could not be completed (${response.status}).`);
      error.status = response.status;
      throw error;
    }
    return data;
  }
  function expireSession() {
    sessionGate.invalidate();
    state.pollGeneration++;
    state.jobsGeneration++;
    state.sendGeneration++;
    clearTimeout(state.polling);
    state.session = null;
    state.sources.clear();
    state.selectedFile = null;
    state.retryTarget = null;
    state.benchmarkId = '';
    $('file-input').value = '';
    state.jobs = [];
    state.devices = [];
    state.selectedId = null;
    state.filesGeneration++;
    for (const request of state.requests.values()) request.abort();
    state.requests.clear();
    state.staging.clear();
    state.sending = false;
    for (const dialog of document.querySelectorAll('dialog[open]')) dialog.close();
    $('app-shell').hidden = true;
    $('auth-screen').hidden = false;
    configureAuth(false);
  }
  function configureAuth(setup) {
    $('auth-title').textContent = setup ? 'Make yourself at home.' : 'Welcome back.';
    $('auth-description').textContent = setup ? 'Create your local account. Your files have places to be.' : 'Sign in to pick up where your files left off.';
    $('auth-submit').replaceChildren(document.createTextNode(setup ? 'Create workspace' : 'Sign in'), element('span', '', '↗'));
    $('auth-password').autocomplete = setup ? 'new-password' : 'current-password';
    $('auth-password').minLength = setup ? 8 : 1;
    $('auth-password').placeholder = setup ? 'At least 8 characters' : 'Your password';
    $('auth-form').dataset.setup = setup ? 'true' : 'false';
    $('auth-form').hidden = false;
    $('session-retry').hidden = true;
  }
  async function loadSession() {
    $('session-retry').hidden = true;
    try {
      state.session = await api('/api/session');
      if (state.session.user) await enterWorkspace();
      else configureAuth(state.session.setup_required);
    } catch (error) {
      $('auth-title').textContent = 'A small connection hiccup.';
      $('auth-description').textContent = error.message;
      $('session-retry').hidden = false;
    }
  }
  async function enterWorkspace() {
    sessionGate.invalidate();
    $('auth-screen').hidden = true;
    $('app-shell').hidden = false;
    $('auth-password').value = '';
    $('account-name').textContent = state.session.user.username;
    formError('auth-error');
    updateFileSelection();
    try {
      const [devicesData, jobsData] = await Promise.all([api('/api/devices'), api('/api/jobs')]);
      if (!state.session?.user) return;
      state.devices = devicesData.devices;
      state.jobs = jobsData.jobs;
      state.selectedId = state.devices.find(device => device.ready)?.id ?? state.devices[0]?.id ?? null;
      state.path = '/';
      renderDevices();
      renderJobs();
      if (selectedDevice()?.ready) await loadFiles();
      else renderFilesEmpty('A place for everything.', 'Connect a device to explore its files.');
      startPolling();
    } catch (error) { if (!error.stale) { showNotice(error.message, true); if (state.session?.user) startPolling(); } }
  }
  function openDialog(dialog) {
    dialogOpener = document.activeElement;
    dialog.showModal();
    const first = dialog.querySelector('input, button');
    if (first) first.focus();
  }
  for (const dialog of document.querySelectorAll('dialog')) {
    dialog.querySelectorAll('[data-close-dialog]').forEach(button => button.addEventListener('click', () => dialog.close()));
    dialog.addEventListener('close', () => { if (dialogOpener?.isConnected) dialogOpener.focus(); });
  }
  function confirmAction(title, description, label, callback) {
    $('confirm-title').textContent = title;
    $('confirm-description').textContent = description;
    $('confirm-action').textContent = label;
    confirmCallback = callback;
    openDialog($('confirm-dialog'));
  }
  $('confirm-action').addEventListener('click', async () => {
    const callback = confirmCallback;
    $('confirm-action').disabled = true;
    try { if (callback) await callback(); $('confirm-dialog').close(); }
    catch (error) { showNotice(error.message, true); }
    finally { $('confirm-action').disabled = false; }
  });
  $('session-retry').addEventListener('click', loadSession);
  $('auth-form').addEventListener('submit', async event => {
    event.preventDefault();
    const submit = $('auth-submit');
    submit.disabled = true;
    formError('auth-error');
    try {
      const endpoint = $('auth-form').dataset.setup === 'true' ? '/api/setup' : '/api/login';
      state.session = await api(endpoint, {method: 'POST', body: {username: $('auth-username').value.trim(), password: $('auth-password').value}});
      await enterWorkspace();
    } catch (error) { formError('auth-error', error.message); }
    finally { submit.disabled = false; }
  });
  $('logout').addEventListener('click', () => {
    const logout = async () => { await api('/api/logout', {method: 'POST'}); expireSession(); $('auth-password').value = ''; $('auth-username').focus(); };
    if (state.jobs.some(active)) confirmAction('Sign out with files in motion?', 'Your active transfers may be interrupted. You can sign in again to check their status.', 'Sign out', logout);
    else logout().catch(error => showNotice(error.message, true));
  });

  async function refreshDevices() {
    state.devices = (await api('/api/devices')).devices;
    if (!selectedDevice()) state.selectedId = state.devices.find(device => device.ready)?.id ?? state.devices[0]?.id ?? null;
    renderDevices();
  }
  function updateControls() {
    const device = selectedDevice();
    const ready = Boolean(device?.ready);
    $('upload-button').disabled = !ready || !state.selectedFile || state.sending;
    $('benchmark-button').disabled = !ready || !state.selectedFile || state.sending;
    $('refresh-files').disabled = !ready;
    $('parent-folder').disabled = !ready || state.path === '/';
    $('connection-dot').classList.toggle('muted', !ready);
    $('connection-label').textContent = device ? `${ready ? 'Connected to' : 'Connect'} ${device.name}` : 'Choose a device';
    $('upload-destination').textContent = device ? `Destination: ${device.name} · ${state.path}${$('compress').checked ? ' · .gz file' : ''}` : 'Choose a connected device to get started.';
    $('current-path').textContent = state.path;
    $('current-path').title = state.path;
  }
  function renderDevices() {
    $('device-count').textContent = state.devices.length;
    $('device-list').replaceChildren();
    if (!state.devices.length) $('device-list').append(element('p', 'aside-description', 'Add your first device to get moving.'));
    for (const device of state.devices) {
      const row = element('div', 'device-row');
      const button = actionButton('', `device-item${String(device.id) === String(state.selectedId) ? ' selected' : ''}`, () => selectDevice(device), `device-${device.id}`);
      button.setAttribute('aria-pressed', String(String(device.id) === String(state.selectedId)));
      const image = element('span', 'device-icon'); image.append(icon('device'));
      const copy = element('span', 'device-copy');
      copy.append(element('strong', '', device.name), element('small', '', device.local ? 'This computer · local demo' : `${device.host}:${device.port}`));
      const dot = element('span', `status-dot${device.ready ? '' : ' muted'}`); dot.setAttribute('aria-hidden', 'true');
      button.setAttribute('aria-label', `${device.name}, ${device.ready ? 'connected' : 'not connected'}`);
      button.append(image, copy, dot);
      row.append(button);
      if (!device.local) {
        const remove = actionButton('×', 'icon-button device-remove', () => removeDevice(device));
        remove.setAttribute('aria-label', `Remove ${device.name}`); remove.title = `Remove ${device.name}`;
        row.append(remove);
      }
      $('device-list').append(row);
    }
    updateControls();
  }
  async function selectDevice(device) {
    state.selectedId = device.id;
    state.path = '/';
    state.filesGeneration++;
    renderDevices();
    if (device.ready) await loadFiles();
    else {
      renderFilesEmpty('Almost there.', 'Connect this device to see its files and start a transfer.');
      connectDeviceId = device.id;
      $('connect-dialog-title').textContent = `Connect ${device.name}`;
      $('connect-description').textContent = `${device.username} at ${device.host}:${device.port}`;
      $('connect-password').value = '';
      $('connect-password').required = !device.local;
      formError('connect-error');
      openDialog($('connect-dialog'));
      $('connect-password').focus();
    }
  }
  function removeDevice(device) {
    confirmAction(`Remove ${device.name}?`, 'This removes its saved connection details. Files on the device will stay where they are.', 'Remove device', async () => {
      await api(`/api/devices/${encodeURIComponent(device.id)}`, {method: 'DELETE'});
      await refreshDevices();
      state.path = '/';
      await loadFiles();
      showNotice('Device removed.');
    });
  }
  $('add-device').addEventListener('click', () => { $('device-form').reset(); formError('device-error'); openDialog($('device-dialog')); $('device-name').focus(); });
  $('device-form').addEventListener('submit', async event => {
    event.preventDefault();
    const submit = $('device-form').querySelector('[type=submit]');
    submit.disabled = true;
    formError('device-error');
    try {
      const {device} = await api('/api/devices', {method: 'POST', body: {name: $('device-name').value.trim(), host: $('device-host').value.trim(), port: Number($('device-port').value), username: $('device-username').value.trim()}});
      $('device-dialog').close();
      await refreshDevices();
      await selectDevice(state.devices.find(saved => String(saved.id) === String(device.id)) || device);
    } catch (error) { formError('device-error', error.message); }
    finally { submit.disabled = false; }
  });
  $('connect-form').addEventListener('submit', async event => {
    event.preventDefault();
    const submit = $('connect-form').querySelector('[type=submit]');
    submit.disabled = true;
    formError('connect-error');
    try {
      await api(`/api/devices/${encodeURIComponent(connectDeviceId)}/connect`, {method: 'POST', body: {password: $('connect-password').value}});
      $('connect-password').value = '';
      $('connect-dialog').close();
      await refreshDevices();
      await loadFiles();
      showNotice('Connected. Your files are ready.');
    } catch (error) { formError('connect-error', error.message); }
    finally { submit.disabled = false; }
  });
  $('connect-dialog').addEventListener('close', () => { $('connect-password').value = ''; });
  function renderFilesEmpty(title, description) {
    const empty = element('div', 'empty-state');
    const image = element('span', 'empty-icon', '▱'); image.setAttribute('aria-hidden', 'true');
    empty.append(image, element('h3', '', title), element('p', '', description));
    $('remote-files').replaceChildren(empty);
    $('files-count').textContent = 'No files to show';
    updateControls();
  }
  async function loadFiles(path = state.path) {
    const device = selectedDevice();
    const generation = ++state.filesGeneration;
    if (!device?.ready) { renderFilesEmpty('A place for everything.', 'Choose a connected device to explore its files.'); return; }
    renderFilesEmpty('Opening your folder…', 'Just a moment.');
    $('refresh-files').disabled = true;
    try {
      const data = await api(`/api/devices/${encodeURIComponent(device.id)}/files?path=${encodeURIComponent(path)}`);
      if (generation !== state.filesGeneration || !state.session?.user) return;
      state.path = data.path;
      renderFiles(data.entries);
    } catch (error) {
      if (generation === state.filesGeneration) { renderFilesEmpty('Could not open this folder.', error.message); showNotice(error.message, true); }
    } finally { if (generation === state.filesGeneration) updateControls(); }
  }
  function renderFiles(entries) {
    $('remote-files').replaceChildren();
    if (!entries.length) renderFilesEmpty('Room for something new.', 'This folder is empty. Send a file to make yourself at home.');
    const ordered = [...entries].sort((a, b) => Number(b.directory) - Number(a.directory) || a.name.localeCompare(b.name));
    for (const entry of ordered) {
      const row = element('div', 'file-row');
      const image = element('span', 'file-kind'); image.append(icon(entry.directory ? 'folder' : 'file'));
      const copy = element('span', 'file-copy');
      copy.append(element('span', 'file-name', entry.name), element('small', '', entry.directory ? 'Folder' : formatBytes(entry.size)));
      row.append(image);
      if (entry.directory) {
        const open = actionButton('', 'folder-button', () => loadFiles(joinPath(state.path, entry.name)));
        open.setAttribute('aria-label', `Open folder ${entry.name}`);
        open.append(copy); row.append(open, element('span', '', '›'));
      } else {
        const download = actionButton('↓', 'icon-button', () => startDownload(entry));
        download.setAttribute('aria-label', `Download ${entry.name}`); download.title = `Download ${entry.name}`;
        row.append(copy, download);
      }
      $('remote-files').append(row);
    }
    $('files-count').textContent = `${entries.length} ${entries.length === 1 ? 'item' : 'items'}`;
    updateControls();
  }
  $('refresh-files').addEventListener('click', () => loadFiles());
  $('parent-folder').addEventListener('click', () => loadFiles(parentPath(state.path)));

  function chooseFile(file) {
    if (!file) return;
    state.selectedFile = file;
    updateFileSelection();
    if (state.retryTarget) {
      const original = state.retryTarget;
      state.retryTarget = null;
      if (file.name !== original.name || file.size !== original.size) { showNotice(`To retry, choose the original ${original.name} (${formatBytes(original.size)}).`, true); return; }
      sendFile(original.kind, original);
    }
  }
  function updateFileSelection() {
    const file = state.selectedFile;
    $('dropzone').classList.toggle('has-file', Boolean(file));
    $('drop-title').textContent = file ? file.name : 'A new home for your files';
    $('drop-description').textContent = file ? 'All picked out. Ready when you are.' : 'Drop a file here, or choose one below';
    $('selected-file-meta').textContent = file ? `${formatBytes(file.size)} · ${file.type || 'File'}` : 'Audio, video, documents — make it yours.';
    updateControls();
  }
  $('file-input').addEventListener('change', () => { chooseFile($('file-input').files[0]); $('file-input').value = ''; });
  $('file-input').addEventListener('cancel', () => { state.retryTarget = null; });
  $('dropzone').addEventListener('dragover', event => { event.preventDefault(); $('dropzone').classList.add('drag-over'); });
  $('dropzone').addEventListener('dragleave', event => { if (!$('dropzone').contains(event.relatedTarget)) $('dropzone').classList.remove('drag-over'); });
  $('dropzone').addEventListener('drop', event => {
    event.preventDefault(); $('dropzone').classList.remove('drag-over');
    const files = event.dataTransfer.files;
    if (files.length > 1) { showNotice('Choose one file at a time. You can send another once this file has been staged.'); return; }
    chooseFile(files[0]);
  });
  // Prevent a dropped file outside the drop area from navigating away.
  document.addEventListener('dragover', event => { if (event.dataTransfer.types.includes('Files')) event.preventDefault(); });
  document.addEventListener('drop', event => { if (event.dataTransfer.types.includes('Files')) event.preventDefault(); });
  $('compress').addEventListener('change', updateControls);
  $('upload-button').addEventListener('click', () => sendFile('upload'));
  $('benchmark-button').addEventListener('click', () => sendFile('benchmark'));

  function upsertJob(job) {
    state.jobsGeneration++;
    const index = state.jobs.findIndex(item => String(item.id) === String(job.id));
    if (index >= 0) state.jobs[index] = job; else state.jobs.unshift(job);
  }
  function streamSource(job, file) {
    return sessionGate.protect(new Promise((resolve, reject) => {
      const xhr = new XMLHttpRequest();
      const id = String(job.id);
      state.requests.set(id, xhr);
      state.staging.set(id, {done: 0, total: file.size, started: performance.now(), speed: 0});
      xhr.open('PUT', `/api/jobs/${encodeURIComponent(job.id)}/source`);
      xhr.setRequestHeader('Content-Type', 'application/octet-stream');
      xhr.setRequestHeader('X-FTP-App', '1');
      xhr.setRequestHeader('X-CSRF-Token', state.session.csrf);
      xhr.withCredentials = true;
      xhr.upload.addEventListener('progress', event => {
        const stage = state.staging.get(id);
        if (!stage) return;
        stage.done = event.loaded;
        stage.total = event.lengthComputable ? event.total : file.size;
        stage.speed = event.loaded / Math.max((performance.now() - stage.started) / 1000, .001);
        renderJobs();
      });
      const cleanup = () => { state.requests.delete(id); state.staging.delete(id); };
      xhr.addEventListener('load', () => {
        cleanup();
        let data = {};
        try { data = JSON.parse(xhr.responseText); } catch { /* server errors may lack JSON */ }
        if (xhr.status >= 200 && xhr.status < 300) resolve(data.job);
        else { if (xhr.status === 401) expireSession(); reject(new Error(data.error || `The file could not be staged (${xhr.status}).`)); }
      });
      xhr.addEventListener('error', () => { cleanup(); reject(new Error('The connection was interrupted while preparing your file. You can retry from its transfer card.')); });
      xhr.addEventListener('abort', () => { cleanup(); const error = new Error('Transfer cancelled.'); error.cancelled = true; reject(error); });
      xhr.send(file);
      renderJobs();
    }));
  }
  async function sendFile(kind, original = null, sourceFile = null) {
    const device = original ? state.devices.find(item => String(item.id) === String(original.device_id)) : selectedDevice();
    const file = sourceFile || state.selectedFile;
    if (!file || state.sending) return;
    if (!device?.ready) { showNotice('Connect this file’s destination device before retrying.', true); return; }
    const generation = ++state.sendGeneration;
    state.sending = true; updateControls();
    let created;
    try {
      const request = original ? {device_id: original.device_id, kind: original.kind, name: file.name, size: file.size, remote_path: original.remote_path, compress: original.compress, unpack: original.unpack} : {device_id: device.id, kind, name: file.name, size: file.size, remote_path: state.path, compress: kind === 'upload' && $('compress').checked, unpack: false};
      created = (await api('/api/jobs', {method: 'POST', body: request})).job;
      state.sources.set(String(created.id), file);
      upsertJob(created);
      if (kind === 'benchmark') { state.benchmarkId = String(created.id); switchView('performance'); }
      renderJobs(); startPolling();
      const job = await streamSource(created, file);
      if (job && state.session?.user) upsertJob(job);
      showNotice(kind === 'benchmark' ? 'File prepared. Comparing both transfer methods.' : 'File prepared. Sending to your device.');
      await refreshJobs();
    } catch (error) {
      if (!error.cancelled && !error.stale) showNotice(error.message, true);
      if (created && !error.stale && state.session?.user) {
        try {
          const current = (await api(`/api/jobs/${encodeURIComponent(created.id)}`)).job;
          // Failed staging can leave a job waiting for a source; cancel it so retry is available.
          if (current.status === 'awaiting_upload') await api(`/api/jobs/${encodeURIComponent(created.id)}/cancel`, {method: 'POST'});
          await refreshJobs();
        } catch { /* polling will recover the server state */ }
      }
    } finally { if (generation === state.sendGeneration) { state.sending = false; updateControls(); renderJobs(); } }
  }
  async function startDownload(entry) {
    const device = selectedDevice();
    if (!device?.ready) return;
    try {
      const {job} = await api('/api/jobs', {method: 'POST', body: {device_id: device.id, kind: 'download', name: entry.name, size: entry.size, remote_path: joinPath(state.path, entry.name), compress: false, unpack: $('unpack').checked && entry.name.toLowerCase().endsWith('.gz')}});
      upsertJob(job); renderJobs(); startPolling();
      showNotice('Getting your file from the device. Save it when it is ready.');
    } catch (error) { showNotice(error.message, true); }
  }
  async function cancelJob(job) {
    try {
      await api(`/api/jobs/${encodeURIComponent(job.id)}/cancel`, {method: 'POST'});
      state.requests.get(String(job.id))?.abort();
      await refreshJobs();
      showNotice('Cancellation requested.');
    } catch (error) { showNotice(error.message, true); }
  }
  async function retryJob(job) {
    if (job.kind === 'download') {
      try {
        const {job: next} = await api('/api/jobs', {method: 'POST', body: {device_id: job.device_id, kind: 'download', name: job.name, size: job.size, remote_path: job.remote_path, compress: job.compress, unpack: job.unpack}});
        upsertJob(next); renderJobs(); startPolling(); switchView('transfers');
      } catch (error) { showNotice(error.message, true); }
      return;
    }
    const file = state.sources.get(String(job.id));
    if (file) { await sendFile(job.kind, job, file); return; }
    state.retryTarget = job;
    showNotice(`Choose the original ${job.name} to retry.`);
    $('file-input').click();
  }
  async function deleteJob(job) {
    confirmAction('Remove this history item?', 'This removes the record and its local download or report. Files on your FTP device stay where they are.', 'Remove record', async () => {
      await api(`/api/jobs/${encodeURIComponent(job.id)}`, {method: 'DELETE'});
      state.sources.delete(String(job.id));
      await refreshJobs();
    });
  }
  function attachment(job, suffix, label) {
    const link = element('a', 'button secondary', label);
    link.href = `/api/jobs/${encodeURIComponent(job.id)}/${suffix}`;
    link.setAttribute('download', '');
    link.dataset.focusKey = `${suffix}-${job.id}`;
    return link;
  }
  function jobCard(job, history = false) {
    const stage = state.staging.get(String(job.id));
    const isActive = active(job);
    const card = element('article', 'job-card');
    const heading = element('div', 'job-top');
    const symbol = element('span', 'job-icon', job.kind === 'download' ? '↓' : job.kind === 'benchmark' ? '↔' : '↑'); symbol.setAttribute('aria-hidden', 'true');
    const title = element('div', 'job-title');
    const date = new Date(job.created);
    const dateLabel = Number.isNaN(date.getTime()) ? '' : date.toLocaleString(undefined, {month: 'short', day: 'numeric', hour: '2-digit', minute: '2-digit'});
    title.append(element('h3', '', job.name), element('p', '', `${job.kind === 'benchmark' ? 'Compression test' : job.kind === 'download' ? 'Download' : 'Upload'} · ${job.device_name || 'Device'}${history && dateLabel ? ` · ${dateLabel}` : ''}`));
    const statusNames = {awaiting_upload: 'Preparing', queued: 'Queued', running: 'In progress', completed: 'Completed', failed: 'Interrupted', cancelled: 'Cancelled'};
    heading.append(symbol, title, element('span', `job-status ${job.status}`, stage ? 'Preparing' : statusNames[job.status] || job.status)); card.append(heading);
    const done = stage ? stage.done : Number(job.done) || 0;
    const total = stage ? stage.total : Number(job.total) || 0;
    const percent = stage ? (total ? done / total * 100 : 0) : finite(job.percent) ? job.percent : total ? done / total * 100 : 0;
    const speed = stage ? stage.speed : job.speed_bps;
    const eta = stage ? (speed > 0 ? (total - done) / speed : null) : job.eta_seconds;
    if (isActive || job.status === 'completed') {
      const progress = element('div', 'progress-track');
      progress.setAttribute('role', 'progressbar'); progress.setAttribute('aria-label', `${job.name}: ${stage ? 'preparing file' : job.phase || 'transfer'}`);
      progress.setAttribute('aria-valuemin', '0'); progress.setAttribute('aria-valuemax', '100');
      progress.setAttribute('aria-valuenow', String(job.status === 'completed' ? 100 : Math.max(0, Math.min(100, Math.round(percent)))));
      const fill = element('span', `progress-fill${stage ? ' staging' : ''}`); fill.style.width = `${job.status === 'completed' ? 100 : Math.max(0, Math.min(100, percent))}%`; progress.append(fill); card.append(progress);
    }
    const metrics = element('div', 'job-metrics');
    metrics.append(element('span', '', stage ? 'Preparing file for transfer' : job.phase || statusNames[job.status] || job.status));
    metrics.append(element('span', '', `${formatBytes(done)} / ${formatBytes(total)}`));
    if (isActive) {
      metrics.append(element('strong', '', finite(speed) && speed > 0 ? `${formatBytes(speed)}/s` : 'Measuring speed…'));
      metrics.append(element('span', '', finite(eta) ? `${formatDuration(eta)} remaining` : 'Estimating time…'));
    } else if (finite(job.elapsed_seconds)) metrics.append(element('span', '', `${formatDuration(job.elapsed_seconds)} elapsed`));
    card.append(metrics);
    if (job.error) card.append(element('p', 'job-error', job.error));
    const buttons = element('div', 'job-buttons');
    if (isActive) buttons.append(actionButton('Cancel', 'button text', () => cancelJob(job), `cancel-${job.id}`));
    if (job.status === 'completed' && job.kind === 'download') buttons.append(attachment(job, 'download', 'Save file ↓'));
    if (job.status === 'completed' && job.kind === 'benchmark') buttons.append(actionButton('View results ↗', 'button secondary', () => { state.benchmarkId = String(job.id); switchView('performance'); renderPerformance(); }, `results-${job.id}`));
    if (job.status === 'failed' || job.status === 'cancelled') buttons.append(actionButton('Retry ↗', 'button secondary', () => retryJob(job), `retry-${job.id}`));
    if (history) buttons.append(actionButton('Remove record', 'button text', () => deleteJob(job), `remove-${job.id}`));
    if (buttons.childElementCount) card.append(buttons);
    return card;
  }
  function renderJobs() {
    if (!state.session?.user) return;
    const focusKey = document.activeElement?.dataset.focusKey;
    const activeJobs = state.jobs.filter(active);
    const finishedJobs = state.jobs.filter(job => !active(job));
    $('active-count').textContent = activeJobs.length;
    $('history-count').textContent = finishedJobs.length;
    $('active-jobs').replaceChildren();
    // Keep a few recent results visible so a finished download's Save button stays easy to find.
    const current = [...activeJobs, ...finishedJobs.slice(0, 2)];
    if (!current.length) $('active-jobs').append(element('div', 'job-empty', 'Nothing in motion just yet. Your next file is a good place to start.'));
    for (const job of current) $('active-jobs').append(jobCard(job));
    $('history-jobs').replaceChildren();
    if (!finishedJobs.length) $('history-jobs').append(element('div', 'job-empty', 'A fresh start. Your finished transfers will appear here.'));
    for (const job of finishedJobs) $('history-jobs').append(jobCard(job, true));
    if (state.view === 'performance') renderPerformance();
    if (focusKey) {
      for (const button of document.querySelectorAll('[data-focus-key]')) if (button.dataset.focusKey === focusKey && button.closest('[hidden]') === null) { button.focus({preventScroll: true}); break; }
    }
  }
  async function refreshJobs() {
    const generation = ++state.jobsGeneration;
    const previous = new Map(state.jobs.map(job => [String(job.id), job]));
    const data = await api('/api/jobs');
    if (!state.session?.user || generation !== state.jobsGeneration) return;
    state.jobs = data.jobs;
    const retryableSources = new Set(state.jobs.filter(job => job.status !== 'completed').map(job => String(job.id)));
    for (const id of state.sources.keys()) if (!retryableSources.has(id)) state.sources.delete(id);
    let refreshDirectory = false;
    for (const job of state.jobs) {
      const old = previous.get(String(job.id));
      if (old && active(old) && terminal.has(job.status)) {
        if (job.status === 'completed') {
          showNotice(job.kind === 'download' ? `${job.name} is ready. Select Save file to download it.` : job.kind === 'benchmark' ? `${job.name}: comparison complete.` : `${job.name} arrived at its destination.`);
          if (job.kind === 'upload' && String(job.device_id) === String(state.selectedId)) refreshDirectory = true;
        } else if (job.status === 'failed') showNotice(`${job.name}: ${job.error || 'Transfer interrupted. You can retry.'}`, true);
      }
    }
    renderJobs();
    if (refreshDirectory) await loadFiles();
  }
  function startPolling() {
    const generation = ++state.pollGeneration;
    clearTimeout(state.polling);
    if (!state.session?.user) return;
    state.polling = setTimeout(async () => {
      try { await refreshJobs(); }
      catch (error) { if (!error.stale && state.session?.user && generation === state.pollGeneration) showNotice(error.message, true); }
      finally { if (generation === state.pollGeneration) startPolling(); }
    }, state.jobs.some(active) || state.sending ? 1000 : 8000);
  }
  $('refresh-history').addEventListener('click', () => refreshJobs().catch(error => showNotice(error.message, true)));
  function switchView(view) {
    state.view = view;
    for (const button of document.querySelectorAll('[data-view]')) {
      const chosen = button.dataset.view === view;
      button.classList.toggle('active', chosen);
      if (chosen) button.setAttribute('aria-current', 'page'); else button.removeAttribute('aria-current');
    }
    for (const name of ['transfers', 'history', 'performance']) $(`view-${name}`).hidden = name !== view;
    if (view === 'performance') renderPerformance();
  }
  for (const button of document.querySelectorAll('[data-view]')) button.addEventListener('click', () => switchView(button.dataset.view));

  function svgNode(tag, attrs = {}, text) {
    const node = document.createElementNS('http://www.w3.org/2000/svg', tag);
    for (const [name, value] of Object.entries(attrs)) node.setAttribute(name, String(value));
    if (text !== undefined) node.textContent = String(text);
    return node;
  }
  function chartFrame(label) {
    const svg = svgNode('svg', {viewBox: '0 0 400 230', role: 'img', 'aria-label': label});
    svg.append(svgNode('title', {}, label));
    return svg;
  }
  function chartLegend() {
    const legend = element('div', 'chart-legend');
    for (const [mode, name] of [['plain', 'Original'], ['gzip', 'Gzip']]) { const label = element('span'); label.append(element('i', `legend-dot ${mode}`), document.createTextNode(name)); legend.append(label); }
    return legend;
  }
  function throughputChart(job) {
    const card = element('section', 'card chart-card');
    card.append(element('h3', '', 'Transfer speed over time'), element('p', '', 'FTP payload throughput · MiB/s'));
    const series = throughputSeries(job.samples);
    const all = [...series.plain, ...series.gzip];
    if (!all.length) { card.append(element('p', 'job-empty', 'Speed samples will appear as the test runs.')); return card; }
    const svg = chartFrame('Actual FTP throughput over elapsed seconds, comparing original and gzip transfers');
    const maxX = Math.max(1, ...all.map(point => point.x));
    const maxY = Math.max(.01, ...all.map(point => point.y)) * 1.1;
    const left = 44, top = 17, width = 337, height = 166;
    for (let step = 0; step <= 4; step++) {
      const y = top + height * step / 4;
      svg.append(svgNode('line', {x1: left, y1: y, x2: left + width, y2: y, stroke: '#eaf0e4', 'stroke-width': 1}));
      svg.append(svgNode('text', {x: left - 9, y: y + 3, 'text-anchor': 'end'}, Number((maxY * (1 - step / 4)).toFixed(2))));
    }
    for (let step = 0; step <= 4; step++) svg.append(svgNode('text', {x: left + width * step / 4, y: top + height + 20, 'text-anchor': 'middle'}, `${Number((maxX * step / 4).toFixed(1))}s`));
    for (const [mode, values] of Object.entries(series)) {
      if (!values.length) continue;
      const color = mode === 'plain' ? '#237562' : '#b5ca91';
      const points = values.map(point => `${left + point.x / maxX * width},${top + height - point.y / maxY * height}`).join(' ');
      svg.append(svgNode('polyline', {points, fill: 'none', stroke: color, 'stroke-width': 2.5, 'stroke-linejoin': 'round', 'stroke-linecap': 'round'}));
      if (values.length === 1) svg.append(svgNode('circle', {cx: left + values[0].x / maxX * width, cy: top + height - values[0].y / maxY * height, r: 3.5, fill: color}));
    }
    card.append(svg, chartLegend());
    return card;
  }
  function payloadChart(runs) {
    const card = element('section', 'card chart-card');
    card.append(element('h3', '', 'A smaller package?'), element('p', '', 'Actual bytes sent · original vs gzip'));
    const svg = chartFrame('Comparison of actual original and gzip payload file sizes');
    const maxBytes = Math.max(1, ...runs.map(run => Number(run.payload_bytes) || 0));
    for (let index = 0; index < runs.length; index++) {
      const run = runs[index];
      const y = 45 + index * 85;
      const width = Math.max(0, Number(run.payload_bytes) || 0) / maxBytes * 270;
      svg.append(svgNode('text', {x: 10, y: y - 12}, run.mode === 'plain' ? 'Original file' : 'Gzip file'));
      svg.append(svgNode('rect', {x: 10, y, width: 300, height: 24, rx: 5, fill: '#f0f4eb'}));
      svg.append(svgNode('rect', {x: 10, y, width, height: 24, rx: 5, fill: run.mode === 'plain' ? '#237562' : '#b5ca91'}));
      svg.append(svgNode('text', {x: 10, y: y + 43}, formatBytes(run.payload_bytes)));
    }
    card.append(svg, chartLegend());
    return card;
  }
  function metric(label, value, detail) {
    const card = element('div', 'metric-card');
    card.append(element('div', 'metric-label', label), element('div', 'metric-value', value), element('div', 'metric-detail', detail));
    return card;
  }
  function renderPerformance() {
    const benchmarks = state.jobs.filter(job => job.kind === 'benchmark');
    const selector = $('benchmark-select');
    const selection = benchmarks.find(job => String(job.id) === state.benchmarkId) || benchmarks.find(job => job.status === 'completed') || benchmarks[0];
    state.benchmarkId = selection ? String(selection.id) : '';
    const optionsKey = JSON.stringify(benchmarks.map(job => [job.id, job.name, job.status]));
    if (selector.dataset.optionsKey !== optionsKey) {
      selector.replaceChildren();
      if (!benchmarks.length) selector.append(element('option', '', 'No tests yet'));
      for (const job of benchmarks) {
        const option = element('option', '', `${job.name} · ${job.status === 'completed' ? 'Complete' : job.status}`);
        option.value = String(job.id); selector.append(option);
      }
      selector.dataset.optionsKey = optionsKey;
    }
    if (selection) selector.value = state.benchmarkId;
    selector.disabled = !benchmarks.length;
    $('performance-results').replaceChildren();
    if (!selection) { $('performance-results').append(element('div', 'job-empty', 'A little curiosity goes a long way. Run your first comparison to see real results.')); return; }
    if (selection.status !== 'completed') {
      $('performance-results').append(jobCard(selection));
      if (selection.samples?.length) $('performance-results').append(throughputChart(selection));
      return;
    }
    const runs = selection.result?.runs || [];
    const measured = comparisonMetrics(selection.result);
    const metrics = element('div', 'metric-grid');
    metrics.append(metric('FILE SIZE CHANGE', measured.reduction === null ? '—' : `${Math.abs(measured.reduction)}%`, measured.reduction === null ? 'Not measured' : measured.reduction >= 0 ? 'Smaller with gzip' : 'Larger with gzip'));
    metrics.append(metric('END-TO-END TIME', measured.timeSaved === null ? '—' : `${Math.abs(measured.timeSaved).toFixed(2)}s`, measured.timeSaved === null ? 'Not measured' : measured.timeSaved >= 0 ? 'Saved with gzip, including preparation' : 'Longer with gzip, including preparation'));
    metrics.append(metric('CONTENT CHECK', measured.verified ? 'Verified' : 'Check failed', measured.verified ? 'Both downloaded files match their SHA-256' : 'One or more files did not verify'));
    const charts = element('div', 'chart-grid'); charts.append(throughputChart(selection), payloadChart(runs));
    const summary = element('div', 'benchmark-summary');
    const table = element('table'); const caption = element('caption', 'visually-hidden', 'Measured benchmark results'); table.append(caption);
    const head = element('thead'); const headRow = element('tr');
    for (const name of ['Method', 'Payload', 'Preparation', 'FTP time', 'Total', 'FTP speed', 'Effective speed', 'Verified']) { const cell = element('th', '', name); cell.scope = 'col'; headRow.append(cell); }
    head.append(headRow); table.append(head);
    const body = element('tbody');
    for (const run of runs) {
      const row = element('tr');
      const values = benchmarkRowValues(run);
      for (const value of values) row.append(element('td', '', value));
      body.append(row);
    }
    table.append(body); summary.append(table);
    const actions = element('div', 'report-actions'); actions.append(element('p', '', 'Preparation includes connection setup and gzip work. Effective speed uses original bytes and total time.'), attachment(selection, 'report.csv', 'Download results .csv ↓')); summary.append(actions);
    $('performance-results').append(metrics, charts, summary);
  }
  $('benchmark-select').addEventListener('change', () => { state.benchmarkId = $('benchmark-select').value; renderPerformance(); });
  loadSession();
})();
