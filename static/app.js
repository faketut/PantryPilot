// PantryPilot client-side JS

// ---- Tab switching ---------------------------------------------------
document.querySelectorAll('.tab').forEach(tab => {
  tab.addEventListener('click', () => {
    document.querySelectorAll('.tab').forEach(t => t.classList.remove('active'));
    document.querySelectorAll('.tab-content').forEach(c => c.classList.remove('active'));
    tab.classList.add('active');
    document.getElementById('tab-' + tab.dataset.tab).classList.add('active');
  });
});

// ---- URL import (Bright Data MCP) -----------------------------------
function onUrlInput(input) {
  const btn = document.getElementById('url-import-btn');
  if (btn) btn.disabled = !input.value.trim().startsWith('http');
}

async function triggerUrlImport(btn) {
  const urlInput = document.getElementById('import-url');
  const url = urlInput ? urlInput.value.trim() : '';
  if (!url) return;
  const statusEl = document.getElementById('url-import-status');
  const orig = btn.innerHTML;
  btn.disabled = true;
  btn.innerHTML = '<span class="spinner"></span> Scraping & extracting…';
  try {
    const res = await fetch(`/import/url?url=${encodeURIComponent(url)}`, { method: 'POST' });
    const html = await res.text();
    if (res.ok) {
      const pantryBody = document.getElementById('pantry-body');
      if (pantryBody) pantryBody.innerHTML = html;
      if (statusEl) {
        statusEl.textContent = '✓ Items extracted — check My Pantry tab';
        statusEl.className = 'status-text status-success';
      }
    } else {
      let msg = html;
      try { msg = JSON.parse(html).detail || html; } catch {}
      if (statusEl) {
        statusEl.textContent = 'Import failed: ' + msg;
        statusEl.className = 'status-text status-error';
      }
    }
  } catch (e) {
    if (statusEl) {
      statusEl.textContent = 'Error: ' + (e.message || 'unknown error');
      statusEl.className = 'status-text status-error';
    }
  } finally {
    btn.disabled = false;
    btn.innerHTML = orig;
  }
}

// ---- Plan generation -------------------------------------------------
const _planBtnLabel = '<span class="icon icon-sm"><svg viewBox="0 0 24 24"><polygon points="12 2 15.09 8.26 22 9.27 17 14.14 18.18 21.02 12 17.77 5.82 21.02 7 14.14 2 9.27 8.91 8.26 12 2"/></svg></span> Generate Plan';
const _planBtnLoading = '<span class="spinner"></span> Generating…';

async function triggerPlan() {
  const days = document.getElementById("days-select").value;
  const status = document.getElementById("plan-status");
  const output = document.getElementById("plan-output");
  const btn = document.getElementById("plan-btn");

  btn.disabled = true;
  btn.innerHTML = _planBtnLoading;
  status.className = "status-text";
  status.textContent = "Generating meal plan… (this may take 20–40s)";
  output.innerHTML = "";

  try {
    const res = await fetch(`/plan?days=${days}`, { method: "POST" });
    if (!res.ok) {
      const err = await res.json().catch(() => ({}));
      status.textContent = err.detail || "Plan generation failed. Please try again.";
      status.className = "status-text status-error";
    } else {
      const plan = await res.json();
      status.textContent = plan.summary || "Plan generated successfully.";
      status.className = "status-text status-success";
      output.innerHTML = renderPlan(plan);
      htmx.trigger("#waste-badge", "load");
    }
  } catch (err) {
    status.textContent = "Network error — is the server running?";
    status.className = "status-text status-error";
  }

  btn.disabled = false;
  btn.innerHTML = _planBtnLabel;
}

function renderPlan(plan) {
  let daysHtml = '';
  (plan.plan || []).forEach(day => {
    // Collect all ingredient names for this day's meals
    const dayIngredients = [];
    (day.meals || []).forEach(m => (m.ingredients || []).forEach(i => dayIngredients.push(i)));

    daysHtml += `<div class="day-card"><h4>Day ${day.day}</h4>`;
    (day.meals || []).forEach(meal => {
      daysHtml += `<div class="meal-item"><span class="meal-label">${meal.meal}:</span> <span class="meal-recipe">${meal.recipe}</span>`;
      if (meal.ingredients && meal.ingredients.length) {
        daysHtml += `<div class="meal-ingredients">${meal.ingredients.join(', ')}</div>`;
      }
      daysHtml += '</div>';
    });

    if (dayIngredients.length) {
      daysHtml += `<button class="btn btn-secondary" style="margin-top:0.6rem;font-size:0.78rem;padding:0.35rem 0.75rem"
        data-ingredients="${encodeURIComponent(JSON.stringify(dayIngredients))}"
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
    sideHtml += `<button class="btn btn-secondary" style="margin-top:0.75rem" onclick="copyShoppingList(${JSON.stringify(plan.missing_ingredients)})"><span class="icon icon-sm"><svg viewBox="0 0 24 24"><rect x="9" y="9" width="13" height="13" rx="2" ry="2"/><path d="M5 15H4a2 2 0 0 1-2-2V4a2 2 0 0 1 2-2h9a2 2 0 0 1 2 2v1"/></svg></span> Copy list</button>`;
    sideHtml += '</div>';
  } else {
    sideHtml += '<div class="shopping-card"><h4>All Set</h4><p style="font-size:0.85rem;color:var(--green-700)">Your pantry has everything you need!</p></div>';
  }

  let bannerHtml = '';
  if (plan.waste_saved_grams) {
    const lbs = (plan.waste_saved_grams / 453.6).toFixed(2);
    bannerHtml = `<div class="waste-saved-banner"><span class="ws-icon icon"><svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><path d="M12 22c5.523 0 10-4.477 10-10S17.523 2 12 2 2 6.477 2 12s4.477 10 10 10z"/><path d="M8 14s1.5 2 4 2 4-2 4-2"/><line x1="9" y1="9" x2="9.01" y2="9"/><line x1="15" y1="9" x2="15.01" y2="9"/></svg></span><span class="ws-text">Food waste avoided: <span class="ws-value">~${lbs} lbs</span></span></div>`;
  }

  return `<div class="plan-grid"><div>${daysHtml}</div><div>${sideHtml}</div></div>${bannerHtml}`;
}

function copyShoppingList(items) {
  const text = items.map(i => `• ${i}`).join("\n");
  navigator.clipboard.writeText(text).then(() => {
    alert("Shopping list copied to clipboard!");
  });
}

async function markDayUsed(btn) {  const ingredients = JSON.parse(decodeURIComponent(btn.dataset.ingredients || '[]'));
  const orig = btn.innerHTML;
  btn.disabled = true;
  btn.innerHTML = '<span class="icon icon-sm"><svg viewBox="0 0 24 24"><line x1="12" y1="2" x2="12" y2="6"/><line x1="12" y1="18" x2="12" y2="22"/><line x1="4.93" y1="4.93" x2="7.76" y2="7.76"/><line x1="16.24" y1="16.24" x2="19.07" y2="19.07"/><line x1="2" y1="12" x2="6" y2="12"/><line x1="18" y1="12" x2="22" y2="12"/></svg></span> Updating…';
  try {
    await fetch('/pantry/consume-batch', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ ingredients })
    });
    // Reload pantry table if it exists in the DOM
    const pantryBody = document.getElementById('pantry-body');
    if (pantryBody) htmx.trigger(pantryBody, 'load');
    btn.innerHTML = '<span class="icon icon-sm"><svg viewBox="0 0 24 24"><polyline points="20 6 9 17 4 12"/></svg></span> Cooked!';
  } catch (e) {
    btn.disabled = false;
    btn.innerHTML = orig;
  }
}
