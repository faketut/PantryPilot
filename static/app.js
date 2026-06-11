// PantryPilot client-side JS

// ---- Cross-component refresh helper ---------------------------------
// One call site to refresh pantry table + hero card + navbar badge after
// any action that mutates pantry/waste state. Pantry rows poll on a timer
// too (every 20s, plus on tab visibility change) so the table can never
// stay stale for long even if a trigger is missed.
function _refreshStaleViews() {
  if (!window.htmx) return;
  htmx.trigger('body', 'pantrpilot:pantry-stale');
  htmx.trigger('body', 'pantrpilot:metrics-stale');
  htmx.trigger('#waste-badge', 'load');
}

// ---- Tab switching ---------------------------------------------------
function _refreshTab(tabName) {
  _refreshStaleViews();
}

document.querySelectorAll('.tab').forEach(tab => {
  tab.addEventListener('click', () => {
    document.querySelectorAll('.tab').forEach(t => t.classList.remove('active'));
    document.querySelectorAll('.tab-content').forEach(c => c.classList.remove('active'));
    tab.classList.add('active');
    document.getElementById('tab-' + tab.dataset.tab).classList.add('active');
    // Always re-load the plan when switching to it so cooked-day state from
    // server is reflected (handles refresh / multi-tab edits). renderPlan
    // rewrites #plan-output, which is fine — we lose nothing in-progress.
    if (tab.dataset.tab === 'plan') {
      _planLoadedOnce = true;
      loadLatestPlan();
    }
    _refreshTab(tab.dataset.tab);
  });
});

// ---- Receipt OCR upload (Gemini Vision) -----------------------------
async function _sendReceipt(file) {
  if (!file) return;
  const status = document.getElementById('receipt-status');
  const text = document.getElementById('receipt-text');
  const originalText = text ? text.innerHTML : '';
  if (text) text.innerHTML = '<span class="spinner"></span> Reading receipt with Gemini Vision…';
  if (status) { status.textContent = ''; status.className = 'status-text'; }

  const form = new FormData();
  form.append('file', file);
  try {
    const res = await fetch('/receipt', { method: 'POST', body: form });
    const body = await res.text();
    if (res.ok) {
      const pantryBody = document.getElementById('pantry-body');
      if (pantryBody) pantryBody.innerHTML = body;
      if (status) {
        status.textContent = '✓ Items extracted — check My Pantry tab';
        status.className = 'status-text status-success';
      }
    } else {
      let msg = body;
      try { msg = JSON.parse(body).detail || body; } catch {}
      if (status) {
        status.textContent = 'Receipt failed: ' + msg;
        status.className = 'status-text status-error';
      }
    }
  } catch (e) {
    if (status) {
      status.textContent = 'Error: ' + (e.message || 'unknown error');
      status.className = 'status-text status-error';
    }
  } finally {
    if (text) text.innerHTML = originalText;
  }
}

function triggerReceiptUpload(input) {
  const file = input.files && input.files[0];
  _sendReceipt(file).finally(() => { input.value = ''; });
}

// ---- Drag-and-drop on the upload zone --------------------------------
(function wireDragDrop() {
  const zone = document.getElementById('receipt-zone');
  if (!zone) return;
  ['dragenter', 'dragover'].forEach(ev => zone.addEventListener(ev, e => {
    e.preventDefault(); e.stopPropagation();
    zone.classList.add('is-dragover');
  }));
  ['dragleave', 'dragend', 'drop'].forEach(ev => zone.addEventListener(ev, e => {
    e.preventDefault(); e.stopPropagation();
    zone.classList.remove('is-dragover');
  }));
  zone.addEventListener('drop', e => {
    const f = e.dataTransfer && e.dataTransfer.files && e.dataTransfer.files[0];
    if (!f) return;
    if (!f.type || !f.type.startsWith('image/')) {
      const status = document.getElementById('receipt-status');
      if (status) {
        status.textContent = 'Please drop an image file (JPG/PNG).';
        status.className = 'status-text status-error';
      }
      return;
    }
    _sendReceipt(f);
  });
})();

// ---- Plan generation (streaming) -------------------------------------
const _planBtnLabel = '<span class="icon icon-sm"><svg viewBox="0 0 24 24"><polygon points="12 2 15.09 8.26 22 9.27 17 14.14 18.18 21.02 12 17.77 5.82 21.02 7 14.14 2 9.27 8.91 8.26 12 2"/></svg></span> Generate Plan';
const _planBtnLoading = '<span class="spinner"></span> Generating…';
let _planLoadedOnce = false;
let _currentPlan = null;
let _currentPlanId = null;
let _cookedDays = new Set();

function _toolLabel(name) {
  return ({
    read_pantry: 'Reading pantry',
    save_meal_plan: 'Saving meal plan',
    get_waste_stats: 'Fetching impact stats',
  })[name] || name;
}

function _renderSkeleton(days) {
  const n = Math.max(1, Math.min(days || 3, 5));
  let html = '<div class="plan-grid"><div>';
  for (let i = 0; i < n; i++) {
    html += `<div class="skeleton-day">
      <div class="skeleton-bar w-30"></div>
      <div class="skeleton-bar w-80"></div>
      <div class="skeleton-bar w-60"></div>
    </div>`;
  }
  html += '</div><div><div class="shopping-card"><h4>Shopping List</h4>'
       + '<div class="skeleton-bar w-80"></div><div class="skeleton-bar w-60"></div>'
       + '<div class="skeleton-bar w-80"></div></div></div></div>'
       + '<div class="stream-log" id="stream-log"></div>';
  return html;
}

function _appendStreamLog(text) {
  const el = document.getElementById('stream-log');
  if (!el) return;
  const step = document.createElement('div');
  step.className = 'step';
  step.innerHTML = `<span class="dot"></span><span>${text}</span>`;
  el.appendChild(step);
  el.scrollTop = el.scrollHeight;
}

async function triggerPlan() {
  const daysSel = document.getElementById('days-select');
  const days = parseInt(daysSel.value, 10) || 5;
  const status = document.getElementById('plan-status');
  const output = document.getElementById('plan-output');
  const btn = document.getElementById('plan-btn');

  btn.disabled = true;
  btn.innerHTML = _planBtnLoading;
  status.className = 'status-text';
  status.textContent = 'Generating meal plan…';
  output.innerHTML = _renderSkeleton(days);

  try {
    const res = await fetch(`/plan/stream?days=${days}`, { method: 'POST' });
    if (!res.ok || !res.body) {
      const err = await res.json().catch(() => ({}));
      status.textContent = err.detail || 'Plan generation failed.';
      status.className = 'status-text status-error';
      output.innerHTML = '';
      btn.disabled = false; btn.innerHTML = _planBtnLabel;
      return;
    }

    const reader = res.body.getReader();
    const decoder = new TextDecoder();
    let buf = '';
    let finalPlan = null;
    let planId = null;
    let cooked = [];

    while (true) {
      const { value, done } = await reader.read();
      if (done) break;
      buf += decoder.decode(value, { stream: true });
      let idx;
      while ((idx = buf.indexOf('\n')) >= 0) {
        const line = buf.slice(0, idx).trim();
        buf = buf.slice(idx + 1);
        if (!line) continue;
        let ev;
        try { ev = JSON.parse(line); } catch { continue; }
        if (ev.event === 'started') {
          _appendStreamLog(`Started planning ${ev.days} days`);
        } else if (ev.event === 'tool_call') {
          _appendStreamLog(`→ ${_toolLabel(ev.name)}`);
        } else if (ev.event === 'tool_result') {
          _appendStreamLog(`✓ ${_toolLabel(ev.name)}`);
        } else if (ev.event === 'final') {
          finalPlan = ev.plan;
        } else if (ev.event === 'plan_id') {
          planId = ev.plan_id;
          cooked = ev.cooked_days || [];
        } else if (ev.event === 'error') {
          status.textContent = 'Plan failed: ' + (ev.detail || 'unknown');
          status.className = 'status-text status-error';
        }
      }
    }

    if (finalPlan) {
      _currentPlan = finalPlan;
      _currentPlanId = planId;
      _cookedDays = new Set(cooked);
      status.textContent = finalPlan.summary || 'Plan generated.';
      status.className = 'status-text status-success';
      output.innerHTML = renderPlan(finalPlan);
      _refreshStaleViews();
    }
  } catch (err) {
    status.textContent = 'Network error — is the server running?';
    status.className = 'status-text status-error';
    output.innerHTML = '';
  }

  btn.disabled = false;
  btn.innerHTML = _planBtnLabel;
}

function renderPlan(plan) {
  let daysHtml = '';
  (plan.plan || []).forEach(day => {
    const dayNum = day.day;
    const isCooked = _cookedDays.has(dayNum);
    const dayIngredients = [];
    (day.meals || []).forEach(m => (m.ingredients || []).forEach(i => dayIngredients.push(i)));

    daysHtml += `<div class="day-card ${isCooked ? 'is-cooked' : ''}" data-day="${dayNum}">`;
    daysHtml += `<h4>Day ${dayNum}${isCooked ? '<span class="cooked-badge">✓ cooked</span>' : ''}</h4>`;
    (day.meals || []).forEach(meal => {
      daysHtml += `<div class="meal-item"><span class="meal-label">${meal.meal}:</span> <span class="meal-recipe">${meal.recipe}</span>`;
      if (meal.ingredients && meal.ingredients.length) {
        daysHtml += `<div class="meal-ingredients">${meal.ingredients.join(', ')}</div>`;
      }
      daysHtml += '</div>';
    });

    if (!isCooked && dayIngredients.length) {
      const itemIds = day.pantry_item_ids || [];
      daysHtml += `<button class="btn btn-secondary" style="margin-top:0.6rem;font-size:0.78rem;padding:0.35rem 0.75rem"
        data-ingredients="${encodeURIComponent(JSON.stringify(dayIngredients))}"
        data-item-ids="${encodeURIComponent(JSON.stringify(itemIds))}"
        data-day="${dayNum}"
        onclick="markDayUsed(this)">
        <span class="icon icon-sm"><svg viewBox="0 0 24 24"><polyline points="20 6 9 17 4 12"/></svg></span>
        Mark day as cooked
      </button>`;
    }
    daysHtml += '</div>';
  });

  let sideHtml = '';
  if (plan.missing_ingredients && plan.missing_ingredients.length) {
    sideHtml += '<div class="shopping-card"><h4>Shopping List</h4><ul class="shopping-list">';
    plan.missing_ingredients.forEach(item => {
      sideHtml += `<li>${item}</li>`;
    });
    sideHtml += '</ul>';
    sideHtml += `<button class="btn btn-secondary" style="margin-top:0.75rem" onclick='copyShoppingList(${JSON.stringify(plan.missing_ingredients)})'><span class="icon icon-sm"><svg viewBox="0 0 24 24"><rect x="9" y="9" width="13" height="13" rx="2" ry="2"/><path d="M5 15H4a2 2 0 0 1-2-2V4a2 2 0 0 1 2-2h9a2 2 0 0 1 2 2v1"/></svg></span> Copy list</button>`;
    sideHtml += '</div>';
  } else {
    sideHtml += '<div class="shopping-card"><h4>All Set</h4><p style="font-size:0.85rem;color:var(--green-700)">Your pantry has everything you need!</p></div>';
  }

  let bannerHtml = '';
  const projected = plan.projected_waste_saved_grams ?? plan.waste_saved_grams;
  if (projected) {
    const lbs = (projected / 453.6).toFixed(2);
    bannerHtml = `<div class="waste-saved-banner"><span class="ws-icon icon"><svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><path d="M12 22c5.523 0 10-4.477 10-10S17.523 2 12 2 2 6.477 2 12s4.477 10 10 10z"/><path d="M8 14s1.5 2 4 2 4-2 4-2"/><line x1="9" y1="9" x2="9.01" y2="9"/><line x1="15" y1="9" x2="15.01" y2="9"/></svg></span><span class="ws-text">Potential waste avoided if you cook this plan: <span class="ws-value">~${lbs} lbs</span></span></div>`;
  }

  return `<div class="plan-grid"><div>${daysHtml}</div><div>${sideHtml}</div></div>${bannerHtml}`;
}

function copyShoppingList(items) {
  const text = items.map(i => `• ${i}`).join("\n");
  navigator.clipboard.writeText(text).then(() => {
    alert("Shopping list copied to clipboard!");
  });
}

async function markDayUsed(btn) {
  const day = parseInt(btn.dataset.day, 10);
  const orig = btn.innerHTML;
  btn.disabled = true;
  btn.innerHTML = '<span class="icon icon-sm"><svg viewBox="0 0 24 24"><line x1="12" y1="2" x2="12" y2="6"/><line x1="12" y1="18" x2="12" y2="22"/><line x1="4.93" y1="4.93" x2="7.76" y2="7.76"/><line x1="16.24" y1="16.24" x2="19.07" y2="19.07"/><line x1="2" y1="12" x2="6" y2="12"/><line x1="18" y1="12" x2="22" y2="12"/></svg></span> Updating…';
  let cookData = null;
  try {
    if (_currentPlanId == null || Number.isNaN(day)) {
      throw new Error('No plan loaded');
    }
    // Server-side cook: decrements the right pantry rows, records waste,
    // marks the day cooked. Single round-trip, no client-side state drift.
    const res = await fetch(`/plan/${encodeURIComponent(_currentPlanId)}/day/${day}/cooked`, { method: 'POST' });
    if (!res.ok) throw new Error('cook request failed: ' + res.status);
    cookData = await res.json().catch(() => null);
    _cookedDays.add(day);
    const card = btn.closest('.day-card');
    if (card) {
      card.classList.add('is-cooked');
      const h4 = card.querySelector('h4');
      if (h4 && !h4.querySelector('.cooked-badge')) {
        h4.insertAdjacentHTML('beforeend', '<span class="cooked-badge">✓ cooked</span>');
      }
      btn.remove();
    }
  } catch (e) {
    btn.disabled = false;
    btn.innerHTML = orig;
    if (window.console) console.warn('markDayUsed failed', e);
    return;
  }

  // Refresh pantry, hero card, and the navbar badge. We do a direct fetch
  // for the badge (not just htmx.trigger) so a stalled trigger can't hide
  // the new totals.
  _refreshStaleViews();
  try {
    const m = await fetch('/metrics');
    if (m.ok) {
      const badge = document.getElementById('waste-badge');
      if (badge) badge.innerHTML = await m.text();
    }
  } catch (_) { /* ignore */ }

  // Surface what actually happened on the server so a 0/0 response is
  // visible instead of silently leaving the badge at 0.
  if (cookData && typeof cookData.consumed === 'number') {
    if (cookData.consumed === 0) {
      _showCookToast('Cooked, but no pantry items matched this day\u2019s ingredients.');
    } else if (cookData.rescued === 0) {
      _showCookToast(`Decremented ${cookData.consumed} item(s). None were near expiry, so no waste rescued.`);
    } else {
      _showCookToast(`Day ${day} cooked: ${cookData.consumed} item(s) used, ${cookData.rescued} rescued from waste.`);
    }
  }
}

function _showCookToast(message) {
  const stack = document.getElementById('toast-stack');
  if (!stack) return;
  const toast = document.createElement('div');
  toast.className = 'toast';
  toast.innerHTML = `<span>${message}</span>`;
  stack.appendChild(toast);
  setTimeout(() => {
    toast.classList.add('is-leaving');
    setTimeout(() => toast.remove(), 220);
  }, 4000);
}

// ---- Restore latest plan on page load --------------------------------
async function loadLatestPlan() {
  try {
    const res = await fetch('/plan/latest');
    if (!res.ok) return;
    const body = await res.json();
    if (!body || !body.plan) return;
    _currentPlan = body.plan;
    _currentPlanId = body.plan_id || null;
    _cookedDays = new Set(body.cooked_days || []);
    const output = document.getElementById('plan-output');
    const status = document.getElementById('plan-status');
    if (output) output.innerHTML = renderPlan(body.plan);
    if (status) {
      status.textContent = body.plan.summary || 'Last plan loaded.';
      status.className = 'status-text status-success';
    }
  } catch (e) { /* ignore — no prior plan */ }
}

// Eager-load latest plan once the DOM is ready so users see prior work
// immediately even before they click the Meal Plan tab.
document.addEventListener('DOMContentLoaded', () => {
  loadLatestPlan().finally(() => { _planLoadedOnce = true; });
});

// ---- Undo toast for consume/delete -----------------------------------
// We listen for the X-Undo-Snapshot response header attached by
// /pantry/{id}/consume and DELETE /pantry/{id}. The header is base64(JSON)
// of the original document so the user can undo the action without us
// remembering server-side. A POST to /pantry/restore upserts the doc back.
function _showToast(message, undoSnapshot) {
  const stack = document.getElementById('toast-stack');
  if (!stack) return;
  const toast = document.createElement('div');
  toast.className = 'toast';
  toast.innerHTML = `<span>${message}</span>`;
  const btn = document.createElement('button');
  btn.type = 'button';
  btn.textContent = 'Undo';
  toast.appendChild(btn);
  stack.appendChild(toast);

  let dismissed = false;
  const dismiss = () => {
    if (dismissed) return;
    dismissed = true;
    toast.classList.add('is-leaving');
    setTimeout(() => toast.remove(), 220);
  };
  const timer = setTimeout(dismiss, 5000);

  btn.addEventListener('click', async () => {
    clearTimeout(timer);
    btn.disabled = true;
    try {
      const res = await fetch('/pantry/restore', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ item: undoSnapshot }),
      });
      if (res.ok) {
        const html = await res.text();
        const tbody = document.getElementById('pantry-body');
        if (tbody) tbody.innerHTML = html;
        _refreshStaleViews();
      }
    } catch (e) { /* swallow */ }
    dismiss();
  });
}

function _decodeSnapshotHeader(value) {
  if (!value) return null;
  try {
    const json = atob(value);
    return JSON.parse(json);
  } catch { return null; }
}

document.body.addEventListener('htmx:afterRequest', (evt) => {
  const xhr = evt.detail && evt.detail.xhr;
  const req = evt.detail && evt.detail.requestConfig;
  if (!xhr || xhr.status < 200 || xhr.status >= 300) return;
  if (!req) return;
  const path = req.path || '';
  const verb = (req.verb || '').toLowerCase();
  // Only react to single-item consume / delete from the pantry table.
  const isConsume = verb === 'patch' && /^\/pantry\/[^/]+\/consume$/.test(path);
  const isDelete = verb === 'delete' && /^\/pantry\/[^/]+$/.test(path);
  if (!isConsume && !isDelete) return;
  const snap = _decodeSnapshotHeader(xhr.getResponseHeader('X-Undo-Snapshot'));
  if (!snap) return;
  const name = snap.name || 'item';
  _showToast(isConsume ? `Used 1 ${name}` : `Removed ${name}`, snap);
  // Consume/delete already swapped #pantry-body via htmx. Refresh the
  // metrics views so the hero + badge stay in sync.
  _refreshStaleViews();
});
