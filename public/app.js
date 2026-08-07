'use strict';

const state = {
  boot: null,
  customers: [],
  sort: { key: 'overdue_total', dir: -1 },
  selected: null,
  scripts: [],
  scriptModalContext: null,
  planWeek: null,
  planStops: [],
  planMap: null,
  planMarkers: [],
  drawerMap: null,
  drawerMarker: null,
  calMonth: null,
  calDays: {},
};

const $ = (id) => document.getElementById(id);
const money = new Intl.NumberFormat('en-US', { minimumFractionDigits: 0, maximumFractionDigits: 0 });
const esc = (s) => String(s ?? '').replace(/[&<>"']/g, (c) => (
  { '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;' }[c]));

function debounce(fn, ms) {
  let t;
  return (...args) => { clearTimeout(t); t = setTimeout(() => fn(...args), ms); };
}

// --------------------------------------------------------------- bootstrap

async function bootstrap() {
  const r = await fetch('/api/bootstrap');
  state.boot = await r.json();
  $('subtitle').textContent = state.boot.has_data
    ? `synced ${state.boot.last_sync ? state.boot.last_sync.synced_at.slice(0, 16).replace('T', ' ') : '—'}`
    : 'No data yet — click Refresh from Odoo';

  const statusSel = $('f-status');
  statusSel.innerHTML = '<option value="">All statuses</option>'
    + state.boot.statuses.map((s) => `<option value="${s.key}">${esc(s.label)}</option>`).join('');

  ['d-status', 'v-status'].forEach((id) => {
    $(id).innerHTML = state.boot.statuses.map((s) => `<option value="${s.key}">${esc(s.label)}</option>`).join('');
  });
  $('v-channel').innerHTML = state.boot.channels.map((c) => `<option value="${c.key}">${esc(c.label)}</option>`).join('');

  const monday = new Date();
  monday.setDate(monday.getDate() - ((monday.getDay() + 6) % 7));
  state.planWeek = monday.toISOString().slice(0, 10);
  $('plan-week').value = state.planWeek;

  const now = new Date();
  state.calMonth = state.boot.today.slice(0, 7);
}

// --------------------------------------------------------------- tabs

function switchView(view) {
  document.querySelectorAll('.view').forEach((v) => v.classList.add('hidden'));
  document.querySelectorAll('.tab').forEach((t) => t.classList.toggle('active', t.dataset.view === view));
  $(`view-${view}`).classList.remove('hidden');
  if (view === 'customers') loadCustomers();
  if (view === 'plan') loadPlan();
  if (view === 'calendar') loadCalendar();
  if (view === 'scripts') loadScripts();
}

// --------------------------------------------------------------- customers

async function loadCustomers() {
  const p = new URLSearchParams();
  if ($('f-search').value) p.set('q', $('f-search').value);
  if ($('f-status').value) p.set('status', $('f-status').value);
  if ($('f-area').value) p.set('area', $('f-area').value);
  if ($('f-min').value) p.set('min', $('f-min').value);
  if ($('f-overdue').checked) p.set('has_overdue', '1');
  if ($('f-needs-visit').checked) p.set('needs_visit', '1');
  if ($('f-due').checked) p.set('due_only', '1');
  // 'all' rather than an empty string: the server's query parser
  // (urllib.parse.parse_qs) drops params with blank values entirely by
  // default, which would silently collapse this back to the 'hide' default
  // regardless of the checkbox.
  p.set('agency', $('f-show-agency').checked ? 'all' : 'hide');

  const r = await fetch('/api/customers?' + p.toString());
  const d = await r.json();
  state.customers = d.customers;

  // `totals` is the currently-filtered set; `grand_totals` is the whole
  // active book. Showing totals here means these numbers actually move as
  // filters are applied, rather than always displaying the unfiltered book
  // regardless of what's on screen. Label reflects which one is showing.
  const filtersActive = [...p.keys()].some((k) => k !== 'agency');
  $('s-count-label').textContent = filtersActive ? 'Showing' : 'Active book';
  $('s-count').textContent = d.totals.count;
  $('s-open').textContent = money.format(d.totals.total_open);
  $('s-overdue').textContent = money.format(d.totals.overdue_total);
  $('s-agency').textContent = `${d.agency.count} · ${money.format(d.agency.total_open)}`;
  $('s-agency-wrap').classList.toggle('hidden', d.agency.count === 0);

  const areaSel = $('f-area');
  const currentArea = areaSel.value;
  areaSel.innerHTML = '<option value="">All areas</option>'
    + d.areas.map((a) => `<option value="${esc(a)}">${esc(a)}</option>`).join('');
  areaSel.value = currentArea;

  renderAlerts(d.attention);
  renderTable(sortCustomers(d.customers));
  updateSortHeaders();
}

function sortCustomers(list) {
  const { key, dir } = state.sort;
  return [...list].sort((a, b) => {
    let av = a[key], bv = b[key];
    if (typeof av === 'string') { av = av || ''; bv = bv || ''; return av.localeCompare(bv) * dir; }
    return ((av ?? -Infinity) - (bv ?? -Infinity)) * dir;
  });
}

function updateSortHeaders() {
  document.querySelectorAll('#thead-row th.sortable').forEach((th) => {
    th.classList.toggle('sort-active', th.dataset.sort === state.sort.key);
    th.querySelector('.arrow')?.remove();
    if (th.dataset.sort === state.sort.key) {
      th.insertAdjacentHTML('beforeend', `<span class="arrow">${state.sort.dir === 1 ? '▲' : '▼'}</span>`);
    }
  });
}

function renderAlerts(attention) {
  const el = $('alerts');
  const cards = [];
  if (attention.broken_promises.length) {
    cards.push(`<div class="alert-card danger"><h4>Broken promises (${attention.broken_promises.length})</h4>
      <ul>${attention.broken_promises.slice(0, 8).map((b) =>
        `<li data-open="${b.partner_id}"><span>${esc(b.name)}</span><span>${b.date}</span></li>`).join('')}</ul></div>`);
  }
  if (attention.due_actions.length) {
    cards.push(`<div class="alert-card"><h4>Follow-ups due (${attention.due_actions.length})</h4>
      <ul>${attention.due_actions.slice(0, 8).map((b) =>
        `<li data-open="${b.partner_id}"><span>${esc(b.name)}</span><span>${b.date}</span></li>`).join('')}</ul></div>`);
  }
  el.innerHTML = cards.join('');
  el.querySelectorAll('[data-open]').forEach((li) => {
    li.addEventListener('click', () => openDrawer(Number(li.dataset.open)));
  });
}

function statusPill(c) {
  const label = (state.boot.statuses.find((s) => s.key === c.status) || {}).label || c.status;
  return `<span class="pill status-${c.status}">${esc(label)}</span>`;
}

function renderTable(customers) {
  const tbody = $('tbody');
  $('empty').classList.toggle('hidden', customers.length > 0);
  tbody.innerHTML = customers.map((c) => `
    <tr data-id="${c.partner_id}" class="${c.agency ? 'row-agency' : ''}">
      <td class="al-right cell-customer">${c.agency ? '<span class="pill pill-agency">agency</span> ' : ''}${c.needs_visit ? '<span class="needs-visit-flag">●</span> ' : ''}<bdi>${esc(c.name)}</bdi></td>
      <td class="al-center">${esc(c.area)}</td>
      <td class="al-center">${c.oldest_days > 0 ? c.oldest_days + 'd' : 'Not due'}</td>
      <td class="al-right nums">${money.format(c.total_open)}</td>
      <td class="al-right nums">${c.overdue_total > 0 ? money.format(c.overdue_total) : '—'}</td>
      <td class="al-center">${statusPill(c)}</td>
      <td class="al-center">${c.next_action_date || '—'}</td>
      <td class="al-center">${c.lat != null ? '📍' : ''}</td>
    </tr>`).join('');
  tbody.querySelectorAll('tr').forEach((tr) => {
    tr.addEventListener('click', () => openDrawer(Number(tr.dataset.id)));
  });
}

// --------------------------------------------------------------- drawer

async function openDrawer(partnerId) {
  const r = await fetch(`/api/customers/${partnerId}`);
  if (!r.ok) { return; }
  const d = await r.json();
  state.selected = d;

  $('d-name').textContent = d.customer.name;
  $('d-sub').textContent = `${d.customer.phone || 'no phone'} · ${d.customer.city || ''} · ${d.customer.area}`;
  $('d-open').textContent = money.format(d.customer.total_open);
  $('d-overdue-amt').textContent = money.format(d.customer.overdue_total);
  $('d-oldest').textContent = d.customer.oldest_days > 0 ? d.customer.oldest_days + 'd' : 'Not due';
  $('d-odoo-link').href = d.odoo_url;

  $('d-status').value = d.customer.status;
  $('d-needs-visit').checked = d.customer.needs_visit;
  $('d-promise-date').value = d.customer.promise_date || '';
  $('d-promise-amount').value = d.customer.promise_amount || '';
  $('d-next-action').value = d.customer.next_action_date || '';
  $('d-agency').checked = d.customer.agency;
  $('d-agency-note').value = d.customer.agency_note || '';
  $('d-agency-since').textContent = d.customer.agency && d.customer.agency_date
    ? `Handed off ${d.customer.agency_date}` : '';

  renderContacts(d.contacts);
  renderVisits(d.visits);
  renderReconciliations(d.reconciliations);
  renderDocuments(d.documents);
  renderDrawerMap(d.customer.lat, d.customer.lng);

  const contactSel = $('v-contact');
  contactSel.innerHTML = '<option value="">— not specified —</option>'
    + d.contacts.map((c) => `<option value="${c.id}">${esc(c.name)}${c.position ? ' · ' + esc(c.position) : ''}</option>`).join('');

  $('scrim').classList.remove('hidden');
  $('drawer').classList.remove('hidden');
  history.replaceState(null, '', `#c=${partnerId}`);
}

function closeDrawer() {
  $('scrim').classList.add('hidden');
  $('drawer').classList.add('hidden');
  history.replaceState(null, '', location.pathname);
}

function renderContacts(contacts) {
  $('d-contacts').innerHTML = contacts.map((c) => `
    <div class="contact-card">
      <button class="del-btn" data-del-contact="${c.id}">remove</button>
      <div class="name">${esc(c.name)}${c.position ? ' — ' + esc(c.position) : ''}</div>
      ${c.phone ? `<div>${esc(c.phone)}</div>` : ''}
      ${c.notes ? `<div>${esc(c.notes)}</div>` : ''}
    </div>`).join('') || '<p class="sub">No contacts logged yet.</p>';
  $('d-contacts').querySelectorAll('[data-del-contact]').forEach((b) => {
    b.addEventListener('click', async () => {
      await fetch(`/api/contacts/${b.dataset.delContact}/delete`, { method: 'POST' });
      openDrawer(state.selected.customer.partner_id);
    });
  });
}

function renderVisits(visits) {
  $('d-visits').innerHTML = visits.map((v) => `
    <div class="visit-card">
      <div class="name">${esc(v.channel)} — ${esc((state.boot.statuses.find((s) => s.key === v.status) || {}).label || v.status)}</div>
      <div class="meta">${v.created_at.slice(0, 16).replace('T', ' ')}${v.contact_name ? ' · with ' + esc(v.contact_name) : ''}${v.contact_position ? ' (' + esc(v.contact_position) + ')' : ''}</div>
      ${v.outcome ? `<div class="outcome">${esc(v.outcome)}</div>` : ''}
      ${v.promise_date ? `<div class="meta">Promised ${money.format(v.promise_amount)} by ${v.promise_date}</div>` : ''}
      ${v.notes ? `<div class="meta">${esc(v.notes)}</div>` : ''}
    </div>`).join('') || '<p class="sub">No calls or visits logged yet.</p>';
}

function renderReconciliations(recs) {
  $('d-reconciliations').innerHTML = recs.map((r) => `
    <div class="recon-card">
      <div class="name">${money.format(r.amount)} — signed ${r.reconciled_date}</div>
      <div class="meta">${esc(r.signed_by || 'unnamed')}${r.signed_position ? ' · ' + esc(r.signed_position) : ''}</div>
      ${r.notes ? `<div>${esc(r.notes)}</div>` : ''}
      ${r.has_image ? `<img src="/api/reconciliations/${r.id}/image" alt="signed document" loading="lazy">` : ''}
    </div>`).join('') || '<p class="sub">No signed reconciliation on file yet.</p>';
  // The list/detail payload never carries the image itself (kept out of the
  // customer JSON so opening a customer stays fast) — each thumbnail lazily
  // fetches its own bytes from /api/reconciliations/:id/image.
  $('d-reconciliations').querySelectorAll('img').forEach((img) => {
    img.addEventListener('click', () => window.open(img.src, '_blank'));
  });
}

function renderDocuments(docs) {
  $('d-doc-count').textContent = docs.length;
  $('d-documents').innerHTML = docs.map((d) => `
    <tr>
      <td>${esc(d.doc)}</td>
      <td>${d.due_date}</td>
      <td>${d.days > 0 ? d.days : 'not due'}</td>
      <td>${money.format(d.residual)}</td>
    </tr>`).join('');
}

function renderDrawerMap(lat, lng) {
  const el = $('d-map');
  if (state.drawerMap) { state.drawerMap.remove(); state.drawerMap = null; }
  const center = (lat != null) ? [lat, lng] : [24.7136, 46.6753]; // Riyadh
  state.drawerMap = L.map(el, { attributionControl: false }).setView(center, lat != null ? 14 : 10);
  L.tileLayer('https://{s}.tile.openstreetmap.org/{z}/{x}/{y}.png').addTo(state.drawerMap);
  if (lat != null) {
    state.drawerMarker = L.marker(center).addTo(state.drawerMap);
  }
  state.drawerMap.on('click', (e) => {
    if (state.drawerMarker) state.drawerMap.removeLayer(state.drawerMarker);
    state.drawerMarker = L.marker(e.latlng).addTo(state.drawerMap);
    $('d-loc-input').value = `${e.latlng.lat.toFixed(6)}, ${e.latlng.lng.toFixed(6)}`;
  });
  setTimeout(() => state.drawerMap && state.drawerMap.invalidateSize(), 150);
}

// --------------------------------------------------------------- drawer actions

async function saveMeta() {
  const partnerId = state.selected.customer.partner_id;
  await fetch(`/api/customers/${partnerId}/meta`, {
    method: 'POST', headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({
      status: $('d-status').value,
      needs_visit: $('d-needs-visit').checked,
      promise_date: $('d-promise-date').value,
      promise_amount: $('d-promise-amount').value,
      next_action_date: $('d-next-action').value,
      agency: $('d-agency').checked,
      agency_note: $('d-agency-note').value,
    }),
  });
  await openDrawer(partnerId);
  loadCustomers();
}

async function saveLocation() {
  const partnerId = state.selected.customer.partner_id;
  const body = { text: $('d-loc-input').value };
  if (state.drawerMarker) {
    const ll = state.drawerMarker.getLatLng();
    body.lat = ll.lat; body.lng = ll.lng;
  }
  const r = await fetch(`/api/customers/${partnerId}/location`, {
    method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify(body),
  });
  if (!r.ok) { const e = await r.json(); alert(e.error); return; }
  $('d-loc-input').value = '';
  openDrawer(partnerId);
}

function useMyLocation() {
  if (!navigator.geolocation) { alert('Geolocation not available on this device.'); return; }
  navigator.geolocation.getCurrentPosition((pos) => {
    $('d-loc-input').value = `${pos.coords.latitude.toFixed(6)}, ${pos.coords.longitude.toFixed(6)}`;
  }, () => alert('Could not get your location — check location permissions.'));
}

async function addContact() {
  const partnerId = state.selected.customer.partner_id;
  const name = $('c-name').value.trim();
  if (!name) return;
  await fetch(`/api/customers/${partnerId}/contacts`, {
    method: 'POST', headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ name, position: $('c-position').value, phone: $('c-phone').value }),
  });
  $('c-name').value = ''; $('c-position').value = ''; $('c-phone').value = '';
  openDrawer(partnerId);
}

async function saveVisit() {
  const partnerId = state.selected.customer.partner_id;
  await fetch(`/api/customers/${partnerId}/visits`, {
    method: 'POST', headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({
      channel: $('v-channel').value, contact_id: $('v-contact').value || null,
      status: $('v-status').value, outcome: $('v-outcome').value,
      promise_date: $('v-promise-date').value, promise_amount: $('v-promise-amount').value,
      next_action_date: $('v-next-action').value, notes: $('v-notes').value,
    }),
  });
  ['v-outcome', 'v-notes', 'v-promise-date', 'v-promise-amount', 'v-next-action'].forEach((id) => { $(id).value = ''; });
  await openDrawer(partnerId);
  loadCustomers();
}

// Server rejects a decoded image over ~3MB (Vercel's own request-body limit
// is the real constraint — see index.py). A phone camera photo can otherwise
// clear 3MB even at moderate quality, so this steps quality down until it
// fits instead of leaving the collector to fight with photo settings in the
// field on a spotty connection.
const MAX_ENCODED_CHARS = 3 * 1024 * 1024 * 1.37; // base64 inflation + JSON overhead margin

function compressImage(file, maxDim = 1600) {
  return new Promise((resolve, reject) => {
    const reader = new FileReader();
    reader.onerror = reject;
    reader.onload = (e) => {
      const img = new Image();
      img.onerror = reject;
      img.onload = () => {
        let { width, height } = img;
        if (width > maxDim || height > maxDim) {
          const scale = maxDim / Math.max(width, height);
          width = Math.round(width * scale); height = Math.round(height * scale);
        }
        const canvas = document.createElement('canvas');
        canvas.width = width; canvas.height = height;
        canvas.getContext('2d').drawImage(img, 0, 0, width, height);
        let quality = 0.72;
        let dataUrl = canvas.toDataURL('image/jpeg', quality);
        while (dataUrl.length > MAX_ENCODED_CHARS && quality > 0.25) {
          quality -= 0.12;
          dataUrl = canvas.toDataURL('image/jpeg', quality);
        }
        resolve(dataUrl);
      };
      img.src = e.target.result;
    };
    reader.readAsDataURL(file);
  });
}

async function saveReconciliation() {
  const partnerId = state.selected.customer.partner_id;
  const payload = {
    amount: $('r-amount').value, reconciled_date: $('r-date').value || state.boot.today,
    signed_by: $('r-signed-by').value, signed_position: $('r-signed-position').value,
    notes: $('r-notes').value,
  };
  const file = $('r-photo').files[0];
  if (file) {
    payload.image_base64 = await compressImage(file);
  }
  const r = await fetch(`/api/customers/${partnerId}/reconciliation`, {
    method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify(payload),
  });
  if (!r.ok) { const e = await r.json(); alert(e.error); return; }
  ['r-amount', 'r-date', 'r-signed-by', 'r-signed-position', 'r-notes', 'r-photo'].forEach((id) => { $(id).value = ''; });
  openDrawer(partnerId);
}

// --------------------------------------------------------------- script bank

async function loadScripts() {
  const r = await fetch('/api/scripts');
  const d = await r.json();
  state.scripts = d.scripts;
  const catSel = $('script-category');
  const current = catSel.value;
  catSel.innerHTML = '<option value="">All categories</option>'
    + d.categories.map((c) => `<option value="${esc(c)}">${esc(c)}</option>`).join('');
  catSel.value = current;
  renderScripts();
}

function renderScripts(targetEl, list) {
  const el = targetEl || $('script-list');
  const q = (targetEl ? $('script-modal-search').value : $('script-search').value).trim().toLowerCase();
  const cat = targetEl ? '' : $('script-category').value;
  let items = list || state.scripts;
  if (cat) items = items.filter((s) => s.category === cat);
  if (q) items = items.filter((s) =>
    s.category.toLowerCase().includes(q) || s.trigger_text.toLowerCase().includes(q)
    || s.script_ar.includes(q) || (s.tip || '').toLowerCase().includes(q));

  el.innerHTML = items.map((s) => `
    <div class="script-card">
      ${s.is_custom ? `<button class="del-btn" data-del-script="${s.id}">remove</button>` : ''}
      <div class="cat">${esc(s.category)}</div>
      <div class="trigger">${esc(s.trigger_text)}</div>
      <div class="ar">${esc(s.script_ar)}</div>
      ${s.tip ? `<div class="tip">${esc(s.tip)}</div>` : ''}
      <button class="btn btn-ghost btn-sm copy-btn" data-copy="${s.id}">Copy</button>
    </div>`).join('') || '<p class="sub">No scripts match.</p>';

  el.querySelectorAll('[data-copy]').forEach((b) => {
    b.addEventListener('click', () => {
      const s = items.find((x) => String(x.id) === b.dataset.copy);
      navigator.clipboard?.writeText(s.script_ar);
      b.textContent = 'Copied!';
      setTimeout(() => { b.textContent = 'Copy'; }, 1200);
    });
  });
  el.querySelectorAll('[data-del-script]').forEach((b) => {
    b.addEventListener('click', async () => {
      await fetch(`/api/scripts/${b.dataset.delScript}/delete`, { method: 'POST' });
      loadScripts();
    });
  });
}

function openScriptModal() {
  $('script-modal').classList.remove('hidden');
  $('script-modal-search').value = '';
  renderScripts($('script-modal-list'));
}

// --------------------------------------------------------------- weekly plan

async function loadPlan() {
  const r = await fetch('/api/plan?week=' + state.planWeek);
  const d = await r.json();
  state.planStops = d.stops;
  renderPlanDays();
  renderPlanMap();
}

function dayLabel(offset) {
  const d = new Date(state.planWeek + 'T00:00:00');
  d.setDate(d.getDate() + offset);
  return d.toLocaleDateString('en-GB', { weekday: 'short', day: 'numeric', month: 'short' });
}

function renderPlanDays() {
  const days = [];
  for (let i = 0; i < 6; i++) {
    const date = new Date(state.planWeek + 'T00:00:00');
    date.setDate(date.getDate() + i);
    const iso = date.toISOString().slice(0, 10);
    const stops = state.planStops.filter((s) => s.planned_date === iso);
    days.push({ iso, label: dayLabel(i), stops });
  }
  $('plan-summary').textContent = `${state.planStops.length} stop(s) planned`;
  $('plan-days').innerHTML = days.map((day) => `
    <div class="plan-day">
      <h4><span>${day.label}</span><span>${day.stops.length}</span></h4>
      ${day.stops.map((s) => `
        <div class="plan-stop">
          <span class="name" data-open="${s.partner_id}">${esc(s.name || ('#' + s.partner_id))}</span>
          <select data-move="${s.id}">
            ${days.map((d2) => `<option value="${d2.iso}" ${d2.iso === day.iso ? 'selected' : ''}>${d2.label}</option>`).join('')}
          </select>
          <button class="del-btn" data-del-plan="${s.id}">✕</button>
        </div>`).join('') || '<div class="plan-stop sub">No stops.</div>'}
    </div>`).join('');

  $('plan-days').querySelectorAll('[data-open]').forEach((el) => {
    el.addEventListener('click', () => openDrawer(Number(el.dataset.open)));
  });
  $('plan-days').querySelectorAll('[data-move]').forEach((sel) => {
    sel.addEventListener('change', async () => {
      const stop = state.planStops.find((s) => String(s.id) === sel.dataset.move);
      await fetch('/api/plan', {
        method: 'POST', headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ partner_id: stop.partner_id, planned_date: sel.value, status: 'planned' }),
      });
      await fetch(`/api/plan/${stop.id}/delete`, { method: 'POST' });
      loadPlan();
    });
  });
  $('plan-days').querySelectorAll('[data-del-plan]').forEach((b) => {
    b.addEventListener('click', async () => {
      await fetch(`/api/plan/${b.dataset.delPlan}/delete`, { method: 'POST' });
      loadPlan();
    });
  });
}

function renderPlanMap() {
  if (state.planMap) { state.planMap.remove(); state.planMap = null; }
  state.planMap = L.map('plan-map', { attributionControl: false }).setView([24.7136, 46.6753], 11);
  L.tileLayer('https://{s}.tile.openstreetmap.org/{z}/{x}/{y}.png').addTo(state.planMap);
  const colors = ['#1f3864', '#b3261e', '#1c6b45', '#b7791f', '#6f42c1', '#0891b2'];
  const withLoc = state.planStops.filter((s) => s.lat != null);
  const byDate = [...new Set(withLoc.map((s) => s.planned_date))].sort();
  withLoc.forEach((s) => {
    const dayIndex = byDate.indexOf(s.planned_date);
    const color = colors[dayIndex % colors.length];
    const marker = L.circleMarker([s.lat, s.lng], {
      radius: 9, color: '#fff', weight: 2, fillColor: color, fillOpacity: 1,
    }).addTo(state.planMap);
    marker.bindPopup(`<b>${esc(s.name || '')}</b><br>${s.planned_date}`);
    marker.on('click', () => openDrawer(s.partner_id));
  });
  if (withLoc.length) {
    state.planMap.fitBounds(withLoc.map((s) => [s.lat, s.lng]), { padding: [30, 30] });
  }
  setTimeout(() => state.planMap && state.planMap.invalidateSize(), 150);
}

async function generatePlan() {
  $('btn-generate-plan').disabled = true;
  try {
    const r = await fetch('/api/plan/generate', {
      method: 'POST', headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ week: state.planWeek, days: 6 }),
    });
    const d = await r.json();
    $('plan-unplaced').classList.toggle('hidden', d.unplaced.length === 0);
    $('plan-unplaced-list').innerHTML = d.unplaced.map((c) =>
      `<li data-open="${c.partner_id}">${esc(c.name)} — ${money.format(c.overdue_total)} overdue</li>`).join('');
    $('plan-unplaced-list').querySelectorAll('[data-open]').forEach((li) => {
      li.addEventListener('click', () => openDrawer(Number(li.dataset.open)));
    });
    await loadPlan();
  } finally {
    $('btn-generate-plan').disabled = false;
  }
}

// --------------------------------------------------------------- calendar

async function loadCalendar() {
  const r = await fetch('/api/calendar?month=' + state.calMonth);
  const d = await r.json();
  state.calDays = d.days;
  renderCalendar();
}

function renderCalendar() {
  const [y, m] = state.calMonth.split('-').map(Number);
  $('cal-title').textContent = new Date(y, m - 1, 1).toLocaleDateString('en-GB', { month: 'long', year: 'numeric' });
  const first = new Date(y, m - 1, 1);
  const startOffset = first.getDay(); // 0=Sun
  const daysInMonth = new Date(y, m, 0).getDate();
  const cells = [];
  for (let i = 0; i < startOffset; i++) cells.push(null);
  for (let d = 1; d <= daysInMonth; d++) cells.push(d);
  while (cells.length % 7 !== 0) cells.push(null);

  $('cal-grid').innerHTML = cells.map((d) => {
    if (!d) return '<div class="cal-cell other-month"></div>';
    const iso = `${y}-${String(m).padStart(2, '0')}-${String(d).padStart(2, '0')}`;
    const info = state.calDays[iso] || { visits: [], promises: [], actions: [] };
    const items = [
      ...info.visits.map((v) => ({ ...v, kind: 'visit' })),
      ...info.promises.map((v) => ({ ...v, kind: 'promise' })),
      ...info.actions.map((v) => ({ ...v, kind: 'action' })),
    ].slice(0, 4);
    return `<div class="cal-cell">
      <div class="daynum">${d}</div>
      ${items.map((it) => `<div class="item" data-open="${it.partner_id}">
        <span class="dot dot-${it.kind}"></span>${esc(it.name || '')}</div>`).join('')}
    </div>`;
  }).join('');

  $('cal-grid').querySelectorAll('[data-open]').forEach((el) => {
    el.addEventListener('click', () => openDrawer(Number(el.dataset.open)));
  });
}

function shiftMonth(delta) {
  const [y, m] = state.calMonth.split('-').map(Number);
  const d = new Date(y, m - 1 + delta, 1);
  state.calMonth = `${d.getFullYear()}-${String(d.getMonth() + 1).padStart(2, '0')}`;
  loadCalendar();
}

// --------------------------------------------------------------- book / assign

async function assignBook() {
  const names = $('book-names').value.split('\n').map((n) => n.trim()).filter(Boolean);
  if (!names.length) return;
  const r = await fetch('/api/assign', {
    method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify({ names }),
  });
  const d = await r.json();
  $('book-result').innerHTML = `
    <p>${d.matched} of ${d.listed} matched.</p>
    ${d.unmatched.length ? `<p class="miss">Not found (check spelling or sync first): ${d.unmatched.map(esc).join(', ')}</p>` : ''}
  `;
  loadCustomers();
}

// --------------------------------------------------------------- sync

function banner(msg, isError) {
  const el = $('sync-banner');
  el.textContent = msg;
  el.classList.toggle('error', !!isError);
  el.classList.remove('hidden');
}
function hideBanner() { $('sync-banner').classList.add('hidden'); }

async function startSync() {
  $('btn-sync').disabled = true;
  banner('Syncing from Odoo… this can take up to a minute.');
  try {
    const r = await fetch('/api/sync', { method: 'POST' });
    const body = await r.json().catch(() => ({}));
    if (!r.ok) { banner(body.error || `Sync failed (${r.status})`, true); return; }
    banner('Sync complete.');
    setTimeout(hideBanner, 2600);
    await bootstrap();
    loadCustomers();
  } catch (err) {
    banner('Sync failed: ' + err.message, true);
  } finally {
    $('btn-sync').disabled = false;
  }
}

// --------------------------------------------------------------- theme + table resize

function initTheme() {
  const saved = localStorage.getItem('theme');
  const prefersDark = matchMedia('(prefers-color-scheme: dark)').matches;
  document.documentElement.dataset.theme = saved || (prefersDark ? 'dark' : 'light');
  $('btn-theme').addEventListener('click', () => {
    const next = document.documentElement.dataset.theme === 'dark' ? 'light' : 'dark';
    document.documentElement.dataset.theme = next;
    localStorage.setItem('theme', next);
  });
}

function initTableResize() {
  const el = $('table-scroll');
  const saved = Number(localStorage.getItem('tableHeight'));
  if (saved) el.style.height = `${saved}px`;
  new ResizeObserver(debounce(() => {
    localStorage.setItem('tableHeight', Math.round(el.getBoundingClientRect().height));
  }, 300)).observe(el);
}

// --------------------------------------------------------------- wiring

function init() {
  initTheme();
  initTableResize();

  document.querySelectorAll('.tab').forEach((t) => t.addEventListener('click', () => switchView(t.dataset.view)));
  document.querySelectorAll('#thead-row th.sortable').forEach((th) => {
    th.addEventListener('click', () => {
      if (state.sort.key === th.dataset.sort) {
        state.sort.dir *= -1;
      } else {
        state.sort = { key: th.dataset.sort, dir: 1 };
      }
      renderTable(sortCustomers(state.customers));
      updateSortHeaders();
    });
  });
  $('btn-sync').addEventListener('click', startSync);
  $('drawer-close').addEventListener('click', closeDrawer);
  $('scrim').addEventListener('click', closeDrawer);
  document.addEventListener('keydown', (e) => { if (e.key === 'Escape') { closeDrawer(); $('script-modal').classList.add('hidden'); $('book-modal').classList.add('hidden'); $('add-script-modal').classList.add('hidden'); } });

  const reload = debounce(loadCustomers, 220);
  ['f-search', 'f-status', 'f-area', 'f-min', 'f-overdue', 'f-needs-visit', 'f-due', 'f-show-agency'].forEach((id) => {
    $(id).addEventListener('input', reload);
  });
  $('f-reset').addEventListener('click', () => {
    $('f-search').value = ''; $('f-status').value = ''; $('f-area').value = ''; $('f-min').value = '';
    $('f-overdue').checked = false; $('f-needs-visit').checked = false; $('f-due').checked = false;
    $('f-show-agency').checked = false;
    loadCustomers();
  });

  $('btn-save-meta').addEventListener('click', saveMeta);
  $('btn-save-location').addEventListener('click', saveLocation);
  $('btn-use-location').addEventListener('click', useMyLocation);
  $('btn-add-contact').addEventListener('click', addContact);
  $('btn-save-visit').addEventListener('click', saveVisit);
  $('btn-save-recon').addEventListener('click', saveReconciliation);

  $('btn-suggest-script').addEventListener('click', openScriptModal);
  $('script-modal-close').addEventListener('click', () => $('script-modal').classList.add('hidden'));
  $('script-modal-search').addEventListener('input', () => renderScripts($('script-modal-list')));

  $('script-search').addEventListener('input', debounce(() => renderScripts(), 150));
  $('script-category').addEventListener('change', () => renderScripts());
  $('btn-add-script').addEventListener('click', () => $('add-script-modal').classList.remove('hidden'));
  $('add-script-close').addEventListener('click', () => $('add-script-modal').classList.add('hidden'));
  $('btn-save-script').addEventListener('click', async () => {
    await fetch('/api/scripts', {
      method: 'POST', headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({
        category: $('as-category').value, trigger_text: $('as-trigger').value,
        script_ar: $('as-script').value, tip: $('as-tip').value,
      }),
    });
    ['as-category', 'as-trigger', 'as-script', 'as-tip'].forEach((id) => { $(id).value = ''; });
    $('add-script-modal').classList.add('hidden');
    loadScripts();
  });

  $('btn-manage-book').addEventListener('click', () => $('book-modal').classList.remove('hidden'));
  $('book-modal-close').addEventListener('click', () => $('book-modal').classList.add('hidden'));
  $('btn-assign').addEventListener('click', assignBook);

  $('plan-week').addEventListener('change', () => { state.planWeek = $('plan-week').value; loadPlan(); });
  $('btn-generate-plan').addEventListener('click', generatePlan);

  $('cal-prev').addEventListener('click', () => shiftMonth(-1));
  $('cal-next').addEventListener('click', () => shiftMonth(1));

  bootstrap().then(() => {
    const hash = location.hash.match(/c=(\d+)/);
    loadCustomers();
    if (hash) openDrawer(Number(hash[1]));
  });
}

document.addEventListener('DOMContentLoaded', init);
