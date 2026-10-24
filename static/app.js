'use strict';
const $ = selector => document.querySelector(selector);
let apps = [], editing = null, toastTimer, localAddress = '', addressSuggestion = '', publicHosts = [];
const editor = $('#editor');
const webPorts = new Map();
let completionOptions = [], completionIndex = 0, autoIcon = '', iconLookup = 0, iconRemoved = false, pendingIcon = null;
let dragging = null, suppressLaunch = false, editMode = false, shown = new Map();
// Inside an app's tab, Alps only appears when the app links to it or redirects to sign in.
const framed = window.top !== window;
// Installed as an app (home screen, desktop install): open apps in tabs. Loaded only there.
if (!framed && (matchMedia('(display-mode: standalone)').matches || navigator.standalone)) {
  const script = document.createElement('script'); script.src = $('meta[name=alps-shell]').content; document.head.append(script);
}

async function loadPorts(host) {
  if (webPorts.has(host)) return;
  webPorts.set(host, []);
  try { webPorts.set(host, await api('/api/ports?host=' + encodeURIComponent(host))); }
  catch (_) { webPorts.delete(host); return; }
  updateAddressCompletion();
}


async function api(path, body) {
  const response = await fetch(path, body === undefined ? {} : {
    method: 'POST', headers: {'Content-Type': 'application/json', 'X-Alps': '1'}, body: JSON.stringify(body)
  });
  const result = await response.json();
  if (!response.ok) {
    if (response.status === 401 && path !== '/api/login') showLogin();
    throw new Error(result.error || 'Could not save. Try again.');
  }
  return result;
}
function notify(message) {
  clearTimeout(toastTimer);
  $('#toast').textContent = message; $('#toast').hidden = false;
  toastTimer = setTimeout(() => { $('#toast').hidden = true; }, 3500);
}
function showLogin() {
  editMode = false;
  $('#loading').hidden = true; $('#apps-view').hidden = true;
  $('#login-view').hidden = false; $('#signout').hidden = true;
  if (editor.open) editor.close();
  window.shell?.stop();
}
async function showApps(session = api('/api/session'), savedApps = api('/api/apps')) {
  [apps, session] = await Promise.all([savedApps, session]);
  localAddress = session.address || location.hostname;
  publicHosts = session.hosts || [];
  $('#address-ip').textContent = localAddress;
  $('#address-hint').hidden = !localAddress;
  $('#app-url').placeholder = `http://${localAddress}:port`;
  $('#loading').hidden = true; $('#login-view').hidden = true;
  $('#apps-view').hidden = false; $('#signout').hidden = false;
  $('#page-error').hidden = true; render();
  const slug = new URLSearchParams(location.search).get('open');
  const exposed = apps.find(app => app.proxy === slug);
  if (exposed) { history.replaceState(null, '', '/'); location.assign(appUrl(exposed)); }
  else if (framed) parent.postMessage('alps:home', location.origin); // An app linked to Alps: its tab becomes the launcher.
  else {
    window.shell?.start();
    if (matchMedia('(hover: hover) and (pointer: fine)').matches) $('#search').focus({preventScroll:true});
  }
}
// Exposed apps listen on every Alps address; stay on the one this page was opened with.
function appUrl(app) {
  if (!app.proxy || !publicHosts.includes(location.hostname)) return app.url;
  const url = new URL(app.url); url.hostname = location.hostname; return url.href;
}
function render() {
  const grid = $('#apps-grid'); grid.replaceChildren();
  const searchText = $('#search').value.trim();
  const query = searchText.toLocaleLowerCase();
  const visible = apps.filter(app => (app.name + ' ' + app.url).toLocaleLowerCase().includes(query));
  $('#app-count').textContent = apps.length;
  $('#empty').hidden = apps.length !== 0 || Boolean(query);
  $('#reorder-hint').hidden = !editMode || apps.length < 2;
  const toggle = $('#toggle-edit');
  toggle.setAttribute('aria-pressed', String(editMode));
  toggle.querySelector('span').textContent = editMode ? 'Stop editing' : 'Edit';
  toggle.querySelector('.edit-pencil').toggleAttribute('hidden', editMode);
  toggle.querySelector('.edit-close').toggleAttribute('hidden', !editMode);
  grid.classList.toggle('editing', editMode);
  $('#clear-search').hidden = !$('#search').value;
  $('#no-results').hidden = !query || visible.length !== 0;
  $('#web-search').href = 'https://www.google.com/search?q=' + encodeURIComponent(searchText);
  $('#web-search').textContent = `Search the web for “${searchText}” ↗`;
  for (const app of visible) {
    const tile = document.createElement('article');
    tile.className = `app-tile tone-${apps.indexOf(app) % 4}${app.small ? ' small' : ''}${query && app === visible[0] ? ' match' : ''}`;
    tile.dataset.id = app.id;
    const href = appUrl(app);
    const link = document.createElement('a'); link.className = 'app-link'; link.href = href;
    const icon = document.createElement('span'); icon.className = 'app-icon'; icon.setAttribute('aria-hidden', 'true');
    icon.textContent = [...app.name][0].toLocaleUpperCase();
    if (app.icon) {
      const img = new Image(); img.alt = ''; img.src = '/icons/' + encodeURIComponent(app.icon);
      img.addEventListener('error', () => { icon.textContent = [...app.name][0].toLocaleUpperCase(); });
      icon.replaceChildren(img);
    }
    const name = document.createElement('span'); name.className = 'app-name'; name.textContent = app.name;
    const address = document.createElement('span'); address.className = 'app-address';
    const parsed = new URL(href); address.textContent = parsed.host + (parsed.pathname === '/' ? '' : parsed.pathname);
    address.title = href;
    const arrow = document.createElement('a'); arrow.className = 'launch-arrow'; arrow.textContent = '↗';
    arrow.href = href; arrow.target = '_blank'; arrow.rel = 'noopener noreferrer';
    arrow.setAttribute('aria-label', `Open ${app.name} in a new tab`);
    arrow.title = `Open ${app.name} in a new tab`;
    const text = document.createElement('span'); text.className = 'app-text'; text.append(name, address);
    link.append(icon, text);
    link.draggable = false;
    link.addEventListener('click', event => { if (suppressLaunch) event.preventDefault(); });
    tile.append(link, arrow); grid.append(tile);
    window.shell?.tile(app, tile, link, arrow);
    if (!editMode) continue;
    const edit = document.createElement('button'); edit.className = 'edit-app'; edit.setAttribute('aria-label', `Edit ${app.name}`);
    edit.innerHTML = '<svg viewBox="0 0 24 24" aria-hidden="true"><path d="m15 5 4 4M4 20l5-1L20 8a2.8 2.8 0 0 0-4-4L5 15l-1 5Z"/></svg>';
    edit.addEventListener('click', () => openEditor(app));
    const move = document.createElement('button'); move.className = 'reorder-app';
    move.setAttribute('aria-label', `Move ${app.name}`);
    move.title = 'Drag to move; arrow keys move one step';
    move.innerHTML = '<svg viewBox="0 0 24 24" aria-hidden="true"><path d="M12 3v18M3 12h18M8 7l4-4 4 4M8 17l4 4 4-4M7 8l-4 4 4 4M17 8l4 4-4 4"/></svg>';
    move.addEventListener('pointerdown', event => beginDrag(event, tile));
    tile.addEventListener('pointerdown', event => {
      if (event.pointerType === 'mouse' && event.target.closest('.app-link')) beginDrag(event, tile);
    });
    tile.addEventListener('pointermove', dragMove);
    tile.addEventListener('pointerup', endDrag);
    tile.addEventListener('pointercancel', cancelDrag);
    move.addEventListener('keydown', event => {
      const [dx, dy] = {ArrowLeft:[-1,0], ArrowUp:[0,-1], ArrowRight:[1,0], ArrowDown:[0,1]}[event.key] || [];
      if (dx === undefined || $('#search').value.trim()) return;
      event.preventDefault();
      const {x, y} = shown.get(app.id), cell = clampCell(x + dx, y + dy, app.id);
      if (cell.x === x && cell.y === y) return;
      layoutTiles(new Map(shown), {id: app.id, ...cell});
      saveLayout(app.id);
    });
    move.disabled = Boolean($('#search').value.trim()) || apps.length < 2;
    const size = document.createElement('button'); size.className = 'size-app';
    size.setAttribute('aria-label', `${app.small ? 'Enlarge' : 'Shrink'} ${app.name}`);
    size.title = app.small ? 'Make large tile' : 'Make small tile';
    size.innerHTML = app.small
      ? '<svg viewBox="0 0 24 24" aria-hidden="true"><path d="M14 4h6v6M10 20H4v-6M20 4l-6 6M4 20l6-6"/></svg>'
      : '<svg viewBox="0 0 24 24" aria-hidden="true"><path d="M4 14h6v6M20 10h-6V4M14 10l6-6M4 20l6-6"/></svg>';
    size.addEventListener('click', () => resize(app));
    tile.append(move, edit, size);
  }
  layoutTiles();
}
function gridMetrics() {
  const grid = $('#apps-grid'), style = getComputedStyle(grid);
  // Old placements add implicit columns; clear them so only the stylesheet's columns are counted.
  for (const tile of grid.children) tile.style.gridArea = '';
  return {grid, columns: style.gridTemplateColumns.split(' ').length, row: parseFloat(style.getPropertyValue('--row')),
    gapX: parseFloat(style.columnGap), gapY: parseFloat(style.rowGap)};
}
// The grid's rows are half a large tile tall. Each app keeps the cell it was dragged to. Search
// results, and screens too narrow for the saved arrangement, flow left to right in the same order.
// `wanted` maps ids to cells; `pinned` claims its cell first and anything in the way moves down.
function layoutTiles(wanted = null, pinned = null) {
  const {grid, columns} = gridMetrics();
  const tiles = [...grid.children].filter(tile => !tile.classList.contains('dragging'));
  const items = tiles.map(tile => ({tile, id: tile.dataset.id, span: tile.classList.contains('small') ? 1 : 2}));
  if (!wanted) {
    wanted = new Map(apps.filter(app => Number.isInteger(app.x)).map(app => [app.id, {x: app.x, y: app.y}]));
    if ($('#search').value.trim() || [...wanted.values()].some(cell => cell.x >= columns)) wanted.clear();
  }
  const taken = new Set(), fits = (x, y, span) => x < columns && (span === 1 || !taken.has(`${x},${y+1}`)) && !taken.has(`${x},${y}`);
  let cursor = 0;
  const put = (item, x, y) => {
    for (let i = 0; i < item.span; i++) taken.add(`${x},${y+i}`);
    shown.set(item.id, {x, y}); cursor = Math.max(cursor, y * columns + x + 1);
  };
  shown = new Map();
  if (pinned) put(items.find(item => item.id === pinned.id), pinned.x, pinned.y);
  const placed = items.filter(item => wanted.has(item.id) && item.id !== pinned?.id)
    .sort((a, b) => wanted.get(a.id).y - wanted.get(b.id).y || wanted.get(a.id).x - wanted.get(b.id).x);
  for (const item of placed) {
    let {x, y} = wanted.get(item.id);
    while (!fits(x, y, item.span)) y++;
    put(item, x, y);
  }
  // Apps without a saved cell (new ones, or all of them when flowing) take the next free cells.
  for (const item of items.filter(item => !wanted.has(item.id) && item.id !== pinned?.id)) {
    while (!fits(cursor % columns, Math.floor(cursor / columns), item.span)) cursor++;
    put(item, cursor % columns, Math.floor(cursor / columns));
  }
  for (const item of items) {
    const {x, y} = shown.get(item.id);
    item.tile.style.gridArea = `${y + 1} / ${x + 1} / span ${item.span}`;
  }
}
function clampCell(x, y, id) {
  // Any column, and any row down to just below the lowest tile.
  const small = $('#apps-grid').querySelector(`[data-id="${id}"]`).classList.contains('small');
  let bottom = 0;
  for (const tile of $('#apps-grid').children) {
    const cell = shown.get(tile.dataset.id);
    if (tile.dataset.id !== id && cell) bottom = Math.max(bottom, cell.y + (tile.classList.contains('small') ? 1 : 2));
  }
  const columns = getComputedStyle($('#apps-grid')).gridTemplateColumns.split(' ').length;
  return {x: Math.min(Math.max(x, 0), columns - 1), y: Math.min(Math.max(y, 0), bottom)};
}
function openEditor(app = null) {
  editing = app;
  iconLookup++; pendingIcon = null; iconRemoved = false; autoIcon = app && !app.icon_path ? app.icon : '';
  $('#icon-preview').hidden = !app?.icon;
  if (app?.icon) $('#suggested-icon').src = '/icons/' + encodeURIComponent(app.icon);
  $('#app-form').reset(); $('#save-error').textContent = ''; $('#remove-confirm').hidden = true;
  $('#editor-title').textContent = app ? 'Edit app' : 'Add an app';
  $('#save-app').textContent = app ? 'Save changes' : 'Add app';
  $('#remove-app').hidden = !app || Boolean(app.proxy);
  $('#app-url').readOnly = Boolean(app?.proxy);
  $('#proxy-hint').hidden = !app?.proxy;
  if (app) {
    $('#app-name').value = app.name; $('#app-url').value = app.url; $('#app-icon').value = app.icon_path;
  }
  updateAddressCompletion();
  webPorts.clear(); loadPorts(localAddress);
  editor.showModal();
}
function updateAddressCompletion() {
  const input = $('#app-url'), prefix = input.value.trim();
  completionOptions = [];
  if (prefix && input.selectionStart === input.selectionEnd && input.selectionEnd === input.value.length) {
    const match = prefix.match(/^(https?:\/\/)?([^/:]+)(?::(\d*))?$/i);
    if (match) {
      const scheme = match[1]?.slice(0,-3).toLowerCase(), host = match[2], portPrefix = match[3];
      const exactHost = [localAddress, '127.0.0.1'].includes(host);
      if (exactHost) {
        loadPorts(host);
        completionOptions = (webPorts.get(host) || [])
          .filter(item => (!scheme ? item.scheme === 'http' : item.scheme === scheme) && String(item.port).startsWith(portPrefix || ''))
          .sort((a,b) => b.port-a.port)
          .map(item => `${scheme ? scheme + '://' : ''}${host}:${item.port}`)
          .filter(url => url !== prefix);
      } else if (portPrefix === undefined) {
        completionOptions = [`http://${localAddress}`, `https://${localAddress}`, localAddress]
          .filter(url => url.length > prefix.length && url.toLowerCase().startsWith(prefix.toLowerCase()));
      }
    }
  }
  completionIndex = 0;
  renderCompletions();
}
function renderCompletions() {
  const container = $('#address-completions'); container.replaceChildren();
  const visible = completionOptions.slice(0, 12);
  addressSuggestion = visible[completionIndex] || '';
  container.hidden = !visible.length;
  visible.forEach((url, index) => {
    const button = document.createElement('button'); button.type = 'button'; button.className = 'address-completion';
    button.classList.toggle('selected', index === completionIndex);
    const text = document.createElement('span'); text.textContent = url; button.append(text);
    if (index === completionIndex) { const key = document.createElement('kbd'); key.textContent = 'Tab'; button.append(key); }
    button.addEventListener('click', () => { addressSuggestion = url; completeAddress(); });
    container.append(button);
  });
}
function completeAddress() {
  if (!addressSuggestion) return;
  const input = $('#app-url'); input.value = addressSuggestion; input.focus();
  input.setSelectionRange(input.value.length, input.value.length); updateAddressCompletion();
}
async function lookupIcon() {
  const url = $('#app-url').value.trim();
  if (editing?.proxy || !url || $('#app-icon').value.trim() || autoIcon || iconRemoved) return;
  const request = ++iconLookup;
  try {
    const result = await api('/api/favicon', {url});
    if (request !== iconLookup || $('#app-url').value.trim() !== url || $('#app-icon').value.trim() || autoIcon || iconRemoved || !editor.open) return;
    autoIcon = result.icon;
    $('#icon-preview').hidden = !autoIcon;
    if (autoIcon) $('#suggested-icon').src = '/icons/' + encodeURIComponent(autoIcon);
  } catch (_) { /* No favicon is normal; keep the initial. */ }
}
$('#app-url').addEventListener('input', () => { iconLookup++; pendingIcon = null; updateAddressCompletion(); });
$('#app-url').addEventListener('click', updateAddressCompletion);
$('#app-url').addEventListener('blur', event => {
  if (!event.relatedTarget?.closest('#address-completions')) pendingIcon = lookupIcon();
});
$('#app-icon').addEventListener('input', () => { iconLookup++; $('#icon-preview').hidden = true; if ($('#app-icon').value.trim()) autoIcon = ''; });
$('#clear-icon').addEventListener('click', () => { iconLookup++; autoIcon = ''; iconRemoved = true; $('#app-icon').value = ''; $('#icon-preview').hidden = true; });
$('#app-url').addEventListener('keydown', event => {
  if (event.key === 'Tab' && !event.shiftKey && !event.isComposing && addressSuggestion) {
    event.preventDefault(); completeAddress();
  } else if (['ArrowDown', 'ArrowUp'].includes(event.key) && completionOptions.length) {
    event.preventDefault(); completionIndex = (completionIndex + (event.key === 'ArrowDown' ? 1 : -1) + Math.min(12,completionOptions.length)) % Math.min(12,completionOptions.length); renderCompletions();
  } else if (event.key === 'Escape' && addressSuggestion) {
    event.preventDefault(); event.stopPropagation(); completionOptions = []; renderCompletions();
  }
});

async function saveLayout(focusId) {
  const positions = apps.map(app => ({id: app.id, ...shown.get(app.id)}));
  try { apps = await api('/api/apps/layout', {positions}); render(); }
  catch (error) { render(); notify(error.message); }
  $('#apps-grid').querySelector(`[data-id="${focusId}"] .reorder-app`)?.focus({preventScroll:true});
}
async function resize(app) {
  try { apps = await api('/api/apps/size', {id: app.id, small: !app.small}); render(); }
  catch (error) { render(); notify(error.message); }
  $('#apps-grid').querySelector(`[data-id="${app.id}"] .size-app`)?.focus({preventScroll:true});
}
function beginDrag(event, tile) {
  if (!editMode || event.button !== 0 || $('#search').value.trim()) return;
  if (event.pointerType === 'mouse') event.preventDefault();
  const handle = event.target.closest('.app-link') || event.currentTarget;
  dragging = {tile, handle, x:event.clientX, y:event.clientY, active:false, pointer:event.pointerId};
  handle.setPointerCapture(event.pointerId);
}
function dragMove(event) {
  if (!dragging || event.pointerId !== dragging.pointer) return;
  const d = dragging, dx = event.clientX-d.x, dy = event.clientY-d.y;
  if (!d.active && Math.hypot(dx,dy)<7) return;
  if (!d.active) {
    d.active = true; const bounds = d.tile.getBoundingClientRect();
    d.slot = document.createElement('div'); d.slot.className = 'drop-slot'; d.slot.dataset.id = d.tile.dataset.id;
    d.slot.classList.toggle('small', d.tile.classList.contains('small')); d.tile.before(d.slot);
    d.tile.style.width = bounds.width+'px'; d.tile.style.height = bounds.height+'px';
    d.tile.style.left = bounds.left+'px'; d.tile.style.top = bounds.top+'px';
    d.tile.classList.add('dragging');
    d.start = new Map(shown); d.cell = shown.get(d.tile.dataset.id);
    suppressLaunch = true;
  }
  d.tile.style.transform = `translate(${dx}px,${dy}px)`;
  // Snap the tile's top-left corner to the nearest cell.
  const {grid, row, gapX, gapY, columns} = gridMetrics(), area = grid.getBoundingClientRect(), tile = d.tile.getBoundingClientRect();
  const width = (area.width - gapX * (columns - 1)) / columns;
  const cell = clampCell(Math.round((tile.left - area.left) / (width + gapX)), Math.round((tile.top - area.top) / (row + gapY)), d.tile.dataset.id);
  d.moved ||= cell.x !== d.cell.x || cell.y !== d.cell.y;
  d.cell = cell;
  layoutTiles(d.start, {id: d.tile.dataset.id, ...cell});
}
function restoreDrag() {
  const d = dragging; dragging = null;
  if (d?.active) { d.slot.replaceWith(d.tile); d.tile.classList.remove('dragging'); d.tile.removeAttribute('style'); layoutTiles(new Map(shown)); }
  requestAnimationFrame(() => { suppressLaunch = false; });
  return d;
}
function endDrag(event) {
  if (!dragging || event.pointerId !== dragging.pointer) return;
  const d = restoreDrag();
  if (d.moved) saveLayout(d.tile.dataset.id);
}
function cancelDrag() { if (dragging) { restoreDrag(); render(); } }
$('#toggle-edit').addEventListener('click', () => {
  cancelDrag(); editMode = !editMode; render();
});
$('#add-app').addEventListener('click', () => openEditor());
$('#empty-add').addEventListener('click', () => openEditor());
$('#close-editor').addEventListener('click', () => editor.close());
$('#search').addEventListener('input', render);
addEventListener('resize', () => { if (!dragging) layoutTiles(); });
$('#search').addEventListener('keydown', event => {
  if (event.key !== 'Enter' || event.isComposing || !event.target.value.trim()) return;
  event.preventDefault(); ($('#apps-grid .match .app-link') ?? $('#web-search')).click(); clearSearch();
});
function clearSearch() { $('#search').value = ''; render(); $('#search').focus({preventScroll:true}); }
$('#clear-search').addEventListener('click', clearSearch);
$('#remove-app').addEventListener('click', () => { $('#remove-confirm').hidden = false; $('#cancel-remove').focus(); });
$('#cancel-remove').addEventListener('click', () => { $('#remove-confirm').hidden = true; $('#remove-app').focus(); });
$('#confirm-remove').addEventListener('click', async () => {
  const button = $('#confirm-remove'); button.disabled = true;
  try { apps = await api('/api/apps/delete', {id: editing.id}); editor.close(); render(); }
  catch (error) { $('#save-error').textContent = error.message; }
  finally { button.disabled = false; }
});
$('#app-form').addEventListener('submit', async event => {
  event.preventDefault(); const button = $('#save-app'); button.disabled = true; $('#save-error').textContent = '';
  try {
    if (!pendingIcon) pendingIcon = lookupIcon();
    await pendingIcon;
    apps = await api('/api/apps/save', {id: editing?.id, name: $('#app-name').value, url: $('#app-url').value, icon_path: $('#app-icon').value, auto_icon: autoIcon});
    editor.close(); $('#search').value = ''; render();
  } catch (error) { $('#save-error').textContent = error.message; }
  finally { button.disabled = false; }
});
$('#login-form').addEventListener('submit', async event => {
  event.preventDefault(); const button = event.submitter; button.disabled = true; $('#login-error').textContent = '';
  try {
    await api('/api/login', {username: $('#username').value, password: $('#password').value});
    $('#password').value = ''; await showApps();
  } catch (error) { $('#login-error').textContent = error.message; }
  finally { button.disabled = false; }
});
$('#forget-device').addEventListener('click', async () => {
  try { await api('/api/forget-device', {}); $('#username').value = ''; $('#forget-device').hidden = true; $('#username').focus(); notify('This device is forgotten.'); }
  catch (error) { notify(error.message); }
});
const signoutDialog = $('#signout-dialog');
$('#signout').addEventListener('click', () => { signoutDialog.returnValue = ''; signoutDialog.showModal(); });
signoutDialog.addEventListener('close', async () => {
  const forget = signoutDialog.returnValue === 'forget';
  if (!signoutDialog.returnValue) return;
  try {
    await api('/api/logout', {forget}); apps = []; $('#apps-grid').replaceChildren(); window.shell?.stop(true); showLogin();
    if (forget) $('#username').value = '';
    $('#forget-device').hidden = forget;
    $(forget ? '#username' : '#password').focus();
  } catch (error) { notify(error.message); }
});
(async () => {
  try {
    // Fetch both at once: one round trip instead of two when signed in.
    const session = api('/api/session'), savedApps = api('/api/apps');
    savedApps.catch(() => {}); // Signed out: the 401 is expected.
    if ((await session).authenticated) await showApps(session, savedApps);
    else if (framed) parent.postMessage('alps:signin', location.origin); // The launcher signs in, never a tab.
    else { $('#username').value = (await session).username; $('#forget-device').hidden = !$('#username').value; showLogin(); }
  } catch (error) {
    $('#loading').hidden = true; $('#page-error').textContent = 'Could not load Alps. Check the connection and reload the page.';
    $('#page-error').hidden = false;
  }
})();
