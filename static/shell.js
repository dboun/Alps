'use strict';
// Installed-app tabs: exposed apps open in frames under a slim tab bar, so Alps is always one tap away.
// Tabs, apps sent to the browser, and crash history stay in this device's localStorage; the server never sees them.
window.shell = (() => {
  const ios = 'standalone' in navigator, chipLabel = ios ? 'Safari' : 'Browser', where = ios ? 'Safari' : 'the browser';
  const memory = (key, value) => {
    try {
      if (value === undefined) return JSON.parse(localStorage.getItem('alps.' + key));
      localStorage.setItem('alps.' + key, JSON.stringify(value));
    } catch (_) { return null; }
  };
  const marks = memory('browser') || {}; // App id -> why it opens in the browser: 'crash' or 'moved'.
  const frames = new Map();
  let tabs = [], current = null, nextId = 1, running = false, leaving = false;

  const bar = document.createElement('nav'); bar.className = 'tabbar'; bar.setAttribute('aria-label', 'Tabs'); bar.hidden = true;
  bar.innerHTML = `<div class="tabs" role="tablist"></div>
    <button class="bar-button to-browser" type="button" aria-label="Open in ${where}" title="Open in ${where}"><svg viewBox="0 0 24 24" aria-hidden="true"><path d="M14 4h6v6M20 4l-8 8M18 14v4.5a1.5 1.5 0 0 1-1.5 1.5h-11A1.5 1.5 0 0 1 4 18.5v-11A1.5 1.5 0 0 1 5.5 6H10"/></svg></button>
    <button class="bar-button new-tab" type="button" aria-label="New tab" title="New tab"><svg viewBox="0 0 24 24" aria-hidden="true"><path d="M12 5v14M5 12h14"/></svg></button>`;
  const stage = document.createElement('div'); stage.className = 'frames';
  const menu = document.createElement('div'); menu.className = 'tab-menu'; menu.setAttribute('role', 'menu'); menu.hidden = true;
  document.body.append(bar, stage, menu);
  bar.querySelector('.new-tab').addEventListener('click', newTab);
  bar.querySelector('.to-browser').addEventListener('click', toBrowser);

  const currentTab = () => tabs.find(tab => tab.id === current);
  const appOf = tab => apps.find(app => app.id === tab?.app);
  const reachable = app => Boolean(app.proxy) && location.protocol === 'https:' && publicHosts.includes(location.hostname);
  const opensInTab = app => reachable(app) && !marks[app.id];

  function iconFor(app) {
    const icon = document.createElement('span'); icon.className = 'tab-icon'; icon.setAttribute('aria-hidden', 'true');
    icon.textContent = [...app.name][0].toLocaleUpperCase();
    if (app.icon) {
      const img = new Image(); img.alt = ''; img.src = '/icons/' + encodeURIComponent(app.icon);
      img.addEventListener('error', () => { icon.textContent = [...app.name][0].toLocaleUpperCase(); });
      icon.replaceChildren(img);
    }
    return icon;
  }
  function renderBar() {
    const list = bar.querySelector('.tabs'); list.replaceChildren();
    for (const tab of tabs) {
      const app = appOf(tab), item = document.createElement('div');
      item.className = `tab${app ? '' : ' alps'}${tab.id === current ? ' current' : ''}`;
      const main = document.createElement('button'); main.type = 'button'; main.className = 'tab-main';
      main.setAttribute('role', 'tab'); main.setAttribute('aria-selected', String(tab.id === current));
      main.addEventListener('click', () => activate(tab.id));
      item.append(main);
      if (app) {
        const name = document.createElement('span'); name.className = 'tab-name'; name.textContent = app.name;
        main.append(iconFor(app), name);
        const close = document.createElement('button'); close.type = 'button'; close.className = 'tab-close';
        close.setAttribute('aria-label', `Close ${app.name}`);
        close.innerHTML = '<svg viewBox="0 0 24 24" aria-hidden="true"><path d="m7 7 10 10M17 7 7 17"/></svg>';
        close.addEventListener('click', () => closeTab(tab.id));
        holdable(close, () => openMenu(tab.id, close));
        item.append(close);
      } else {
        main.innerHTML = '<svg viewBox="0 0 36 28" aria-hidden="true"><defs><linearGradient id="alpenglow" x2="1"><stop offset="0" class="glow-1"/><stop offset=".5" class="glow-2"/><stop offset="1" class="glow-3"/></linearGradient></defs><path d="m2 24 11-19 6 10 4-7 11 16M9 12l4 3 3-3M20 14l3 2 3-2"/></svg><span class="tab-name">Alps</span>';
      }
      list.append(item);
    }
    const shown = running && tabs.some(tab => tab.app !== null), inApp = running && Boolean(currentTab()?.app);
    bar.hidden = !shown;
    document.body.classList.toggle('shell-bar', shown);
    document.body.classList.toggle('shell-app', inApp);
    bar.querySelector('.to-browser').hidden = !inApp;
    list.querySelector('.current')?.scrollIntoView({inline: 'nearest', block: 'nearest'});
  }
  function frameFor(tab) {
    const app = appOf(tab), frame = document.createElement('iframe');
    frame.title = app.name;
    // Cross-origin, so the sandbox holds: the app can't navigate Alps away, but popups still open in the browser.
    frame.setAttribute('sandbox', 'allow-scripts allow-same-origin allow-forms allow-modals allow-downloads allow-popups allow-popups-to-escape-sandbox allow-top-navigation-to-custom-protocols');
    // Browsers save camera, microphone, location and clipboard-read grants for Alps, not the app, so one app's grant
    // would carry over to the others. Apps that need those open in the browser (the bar's arrow button).
    frame.allow = 'fullscreen; clipboard-write; publickey-credentials-get; publickey-credentials-create; screen-wake-lock; autoplay; web-share';
    frame.src = appUrl(app);
    stage.append(frame); frames.set(tab.id, frame);
  }
  function activate(id) {
    const leaving = currentTab();
    // A launcher tab left without opening an app closes, like an unused new-tab page.
    if (leaving && leaving.app === null && leaving.id !== id) tabs = tabs.filter(tab => tab !== leaving);
    current = id;
    const tab = currentTab();
    if (tab.app !== null && !frames.has(id)) frameFor(tab);
    for (const [tabId, frame] of frames) frame.classList.toggle('current', tabId === id);
    save(); renderBar();
  }
  function open(app) {
    let tab = currentTab();
    if (tab?.app === null) tab.app = app.id; // The launcher tab becomes the app, as a browser's new-tab page does.
    else tabs.push(tab = {id: nextId++, app: app.id});
    activate(tab.id);
  }
  function newTab() {
    if (currentTab()?.app === null) return;
    tabs.push({id: nextId, app: null}); activate(nextId++);
  }
  function closeTab(id) {
    const index = tabs.findIndex(tab => tab.id === id);
    frames.get(id)?.remove(); frames.delete(id); tabs.splice(index, 1);
    if (id !== current) { save(); renderBar(); return; }
    current = null;
    const next = tabs[index] || tabs[index - 1];
    if (next) activate(next.id); else newTab();
  }
  function closeWhere(test) {
    const closing = tabs.filter(test);
    for (const tab of closing) { frames.get(tab.id)?.remove(); frames.delete(tab.id); }
    tabs = tabs.filter(tab => !closing.includes(tab));
    if (tabs.some(tab => tab.id === current)) { save(); renderBar(); } else { current = null; newTab(); }
  }
  // Long press (or right-click) on a tab's close button. Taps still close just that tab.
  function holdable(button, onHold) {
    let timer, start, held = false;
    const cancel = () => clearTimeout(timer);
    const hold = () => { if (!held) { held = true; navigator.vibrate?.(8); onHold(); } };
    button.addEventListener('pointerdown', event => { held = false; start = [event.clientX, event.clientY]; timer = setTimeout(hold, 450); });
    button.addEventListener('pointermove', event => { if (start && Math.hypot(event.clientX - start[0], event.clientY - start[1]) > 10) cancel(); });
    for (const type of ['pointerup', 'pointercancel', 'pointerleave']) button.addEventListener(type, cancel);
    button.addEventListener('contextmenu', event => { event.preventDefault(); cancel(); hold(); });
    button.addEventListener('click', event => { if (held) { event.stopImmediatePropagation(); held = false; } }, true);
  }
  function openMenu(id, anchor) {
    const index = tabs.findIndex(tab => tab.id === id), last = tabs.length - 1;
    // All four always show, in the same places; the ones with nothing to close are disabled.
    const choices = [
      ['Close all but this tab', last > 0, tab => tab.id !== id],
      ['Close all to the left', index > 0, (tab, i) => i < index],
      ['Close all to the right', index < last, (tab, i) => i > index],
      ['Close all', true, () => true],
    ];
    menu.replaceChildren();
    for (const [label, enabled, test] of choices) {
      const item = document.createElement('button'); item.type = 'button'; item.setAttribute('role', 'menuitem');
      item.textContent = label; item.disabled = !enabled;
      item.addEventListener('click', () => { closeMenu(); closeWhere(tab => test(tab, tabs.indexOf(tab))); });
      menu.append(item);
    }
    menu.hidden = false;
    // Drop down from the button, kept on screen, growing out of the button it came from.
    const from = anchor.getBoundingClientRect(), width = menu.offsetWidth;
    const left = Math.min(Math.max(from.right - width, 8), innerWidth - width - 8);
    menu.style.left = left + 'px'; menu.style.top = from.bottom + 6 + 'px';
    menu.style.transformOrigin = `${from.left + from.width / 2 - left}px 0`;
    menu.querySelector('button:enabled').focus({preventScroll: true});
  }
  function closeMenu() { menu.hidden = true; }
  document.addEventListener('pointerdown', event => { if (!menu.hidden && !menu.contains(event.target)) closeMenu(); }, true);
  document.addEventListener('keydown', event => { if (event.key === 'Escape') closeMenu(); });
  addEventListener('blur', closeMenu); // A tap inside an app's frame.
  addEventListener('resize', closeMenu);
  function toBrowser() {
    const tab = currentTab(), app = appOf(tab);
    window.open(appUrl(app), '_blank', 'noopener');
    marks[app.id] = 'moved'; memory('browser', marks);
    closeTab(tab.id); render();
    notify(`${app.name} now opens in ${where}. Tap its ${chipLabel} label to bring it back.`);
  }
  function save() {
    memory('tabs', {tabs: tabs.map(tab => tab.app), current: tabs.findIndex(tab => tab.id === current)});
    remember();
  }
  // Last known state, read at the next start. A crashed page reloads at once, still on screen. Closing the app
  // yourself (or iOS freeing a backgrounded app) hides the screen first, and reopening it comes later, past the
  // heartbeat; neither counts.
  function remember() {
    memory('life', {visible: document.visibilityState === 'visible', app: running ? currentTab()?.app ?? null : null, clean: leaving, at: Date.now()});
  }
  setInterval(() => { if (running && document.visibilityState === 'visible' && currentTab()?.app) remember(); }, 2000);
  function checkCrash() {
    const life = memory('life');
    if (!life?.visible || life.clean || !life.app || !(Date.now() - life.at < 10000)) return;
    const crashes = memory('crashes') || {}, now = Date.now();
    crashes[life.app] = (crashes[life.app] || []).filter(time => now - time < 600000).concat(now);
    if (crashes[life.app].length >= 2) {
      delete crashes[life.app]; marks[life.app] = 'crash'; memory('browser', marks);
      const app = apps.find(app => app.id === life.app);
      if (app) notify(`${app.name} kept reloading in a tab, so it now opens in ${where}.`);
    }
    memory('crashes', crashes);
  }
  addEventListener('visibilitychange', () => remember());
  // Browsers differ in whether the hidden state comes before or after pagehide; a clean exit stays clean.
  addEventListener('pagehide', () => { leaving = true; remember(); });
  addEventListener('pageshow', event => { if (event.persisted) { leaving = false; remember(); } });
  // Alps loaded inside a tab. Signed out (the app's sign-in redirect): sign in here, outside any tab, so the
  // password is never typed into a tab, where an app could fake the form. Signed in: that tab becomes the launcher.
  addEventListener('message', event => {
    if (event.origin !== location.origin || !['alps:home', 'alps:signin'].includes(event.data)) return;
    const id = [...frames].find(([, frame]) => frame.contentWindow === event.source)?.[0];
    if (id === undefined) return;
    if (event.data === 'alps:signin') { showLogin(); return; }
    frames.get(id).remove(); frames.delete(id);
    tabs.find(tab => tab.id === id).app = null;
    if (id === current) current = null;
    activate(id);
  });

  function start() {
    if (!running) {
      running = true;
      checkCrash();
      const saved = memory('tabs') || {}, list = Array.isArray(saved.tabs) ? saved.tabs : [];
      let restored = null;
      tabs = [];
      list.forEach((appId, index) => {
        // Only the launcher tab that was in front survives; restored apps reopen at their start page.
        if (appId === null ? index !== saved.current : !apps.some(app => app.id === appId && opensInTab(app))) return;
        tabs.push({id: nextId, app: appId});
        if (index === saved.current) restored = nextId;
        nextId++;
      });
      current = null;
      if (restored === null) newTab(); else activate(restored);
    }
    render();
  }
  function stop(forget = false) {
    running = false; remember(); // No app in front, so signing in again on this page isn't a crash.
    for (const frame of frames.values()) frame.remove();
    frames.clear();
    if (forget) { tabs = []; memory('tabs', null); }
    renderBar();
  }
  function tile(app, tileElement, link, arrow) {
    arrow.setAttribute('aria-label', `Open ${app.name} in ${where}`); arrow.title = `Open ${app.name} in ${where}`;
    if (opensInTab(app)) {
      link.addEventListener('click', event => { if (running && !event.defaultPrevented) { event.preventDefault(); open(app); } });
      return;
    }
    link.target = '_blank'; link.rel = 'noopener noreferrer'; arrow.hidden = true;
    const mark = reachable(app) && marks[app.id], chip = document.createElement(mark ? 'button' : 'span');
    chip.className = 'browser-chip'; chip.textContent = chipLabel;
    tileElement.classList.add('has-chip');
    if (mark) {
      chip.type = 'button'; chip.setAttribute('aria-label', `Open ${app.name} in a tab again`); chip.title = 'Open in a tab again';
      chip.insertAdjacentHTML('beforeend', '<svg viewBox="0 0 24 24" aria-hidden="true"><path d="m8 8 8 8M16 8l-8 8"/></svg>');
      chip.addEventListener('click', () => { delete marks[app.id]; memory('browser', marks); render(); notify(`${app.name} opens in a tab again.`); });
    } else chip.title = `${app.name} opens in ${where}`;
    tileElement.append(chip);
  }
  return {start, stop, tile};
})();
if (!$('#apps-view').hidden) window.shell.start();
