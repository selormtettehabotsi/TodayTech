function formatDeadlineDate(dateStr) {
  if (!dateStr) return '';
  try {
    const parts = dateStr.split('-');
    if (parts.length === 3) {
      const year = parseInt(parts[0], 10);
      const monthIndex = parseInt(parts[1], 10) - 1;
      const day = parseInt(parts[2], 10);
      const months = ['Jan', 'Feb', 'Mar', 'Apr', 'May', 'Jun', 'Jul', 'Aug', 'Sep', 'Oct', 'Nov', 'Dec'];
      return `${months[monthIndex]} ${day}, ${year}`;
    }
    return dateStr;
  } catch (e) {
    return dateStr;
  }
}

/**
 * TodayTech Public Read-Only Dashboard Logic
 * Clean Vanilla JS with SVG icons, interactive filtering, and modal controls.
 */

// SVG Icon Helpers
const ICONS = {
  award: `<svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><circle cx="12" cy="8" r="7"></circle><polyline points="8.21 13.89 7 23 12 20 17 23 15.79 13.88"></polyline></svg>`,
  mapPin: `<svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><path d="M21 10c0 7-9 13-9 13s-9-6-9-13a9 9 0 0 1 18 0z"></path><circle cx="12" cy="10" r="3"></circle></svg>`,
  calendar: `<svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><rect x="3" y="4" width="18" height="18" rx="2" ry="2"></rect><line x1="16" y1="2" x2="16" y2="6"></line><line x1="8" y1="2" x2="8" y2="6"></line><line x1="3" y1="10" x2="21" y2="10"></line></svg>`,
  externalLink: `<svg width="13" height="13" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><line x1="7" y1="17" x2="17" y2="7"></line><polyline points="7 7 17 7 17 17"></polyline></svg>`,
  cross: `<svg width="12" height="12" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2.5" stroke-linecap="round" stroke-linejoin="round"><line x1="18" y1="6" x2="6" y2="18"></line><line x1="6" y1="6" x2="18" y2="18"></line></svg>`,
  shieldCheck: `<svg width="13" height="13" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><path d="M12 22s8-4 8-10V5l-8-3-8 3v7c0 6 8 10 8 10z"></path><polyline points="9 12 11 14 15 10"></polyline></svg>`,
  document: `<svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><path d="M14 2H6a2 2 0 0 0-2 2v16a2 2 0 0 0 2 2h12a2 2 0 0 0 2-2V8z"></path><polyline points="14 2 14 8 20 8"></polyline><line x1="16" y1="13" x2="8" y2="13"></line><line x1="16" y1="17" x2="8" y2="17"></line></svg>`
};

function escapeHtml(str) {
  if (!str) return '';
  return String(str)
    .replace(/&/g, '&amp;')
    .replace(/</g, '&lt;')
    .replace(/>/g, '&gt;')
    .replace(/"/g, '&quot;')
    .replace(/'/g, '&#39;');
}

function safeHtml(html) {
  if (typeof DOMPurify !== 'undefined' && DOMPurify.sanitize) {
    return DOMPurify.sanitize(html);
  }
  return escapeHtml(html);
}

let currentFilter = 'all';
let currentClosingOnly = false;
let currentTimeframe = 'all';
let currentSearch = '';
let searchDebounceTimer = null;
let opportunitiesRequestId = 0;

document.addEventListener('DOMContentLoaded', () => {
  loadStats();
  loadOpportunities();
  setupEventListeners();
});

function setupEventListeners() {
  // Search input
  const searchInput = document.getElementById('searchInput');
  if (searchInput) {
    searchInput.addEventListener('input', (e) => {
      currentSearch = e.target.value.trim();
      clearTimeout(searchDebounceTimer);
      searchDebounceTimer = setTimeout(loadOpportunities, 250);
    });
  }

  // Type Filter pills
  document.querySelectorAll('.pill-btn').forEach((btn) => {
    btn.addEventListener('click', () => {
      document.querySelectorAll('.pill-btn').forEach((b) => b.classList.remove('active'));
      btn.classList.add('active');
      currentFilter = btn.dataset.type || 'all';
      loadOpportunities();
    });
  });

  // Timeframe Horizon Filter pills
  document.querySelectorAll('.time-btn').forEach((btn) => {
    btn.addEventListener('click', () => {
      document.querySelectorAll('.time-btn').forEach((b) => b.classList.remove('active'));
      btn.classList.add('active');
      currentTimeframe = btn.dataset.timeframe || 'all';
      loadOpportunities();
    });
  });

  // Interactive Stats Chips
  document.querySelectorAll('.stat-chip').forEach((chip) => {
    chip.addEventListener('click', () => {
      document.querySelectorAll('.stat-chip').forEach((c) => c.classList.remove('active'));
      chip.classList.add('active');
      currentClosingOnly = chip.dataset.closing === 'true';
      loadOpportunities();
    });
  });

  // Refresh Button
  const refreshBtn = document.getElementById('refreshBtn');
  if (refreshBtn) {
    refreshBtn.addEventListener('click', () => {
      const text = document.getElementById('refreshBtnText');
      if (text) text.textContent = 'Reloading...';
      Promise.all([loadStats(), loadOpportunities()]).finally(() => {
        if (text) text.textContent = 'Refresh';
        showToast('Radar data reloaded');
      });
    });
  }

  // Drawer Close
  const drawerClose = document.getElementById('drawerClose');
  const drawerBackdrop = document.getElementById('drawerBackdrop');
  if (drawerClose) drawerClose.addEventListener('click', closeDetails);
  if (drawerBackdrop) drawerBackdrop.addEventListener('click', closeDetails);

  // Esc key close
  document.addEventListener('keydown', (e) => {
    if (e.key === 'Escape') closeDetails();
  });
}

async function loadStats() {
  try {
    const res = await fetch('/api/stats');
    if (!res.ok) return;
    const data = await res.json();
    const statTotal = document.getElementById('statTotal');
    const statClosing = document.getElementById('statClosing');
    if (statTotal) statTotal.textContent = data.total_opportunities ?? '--';
    if (statClosing) statClosing.textContent = data.closing_soon_count ?? '--';
  } catch (err) {
    // Non-blocking
  }
}

async function loadOpportunities() {
  const cardsGrid = document.getElementById('cardsGrid');
  if (!cardsGrid) return;

  const requestId = ++opportunitiesRequestId;
  cardsGrid.innerHTML = safeHtml(`<div style="padding: 32px; color: var(--text-muted);">Fetching opportunities...</div>`);

  try {
    const params = new URLSearchParams();
    if (currentFilter && currentFilter !== 'all') params.append('type', currentFilter);
    if (currentClosingOnly) params.append('closing_soon', 'true');
    if (currentTimeframe && currentTimeframe !== 'all') params.append('timeframe', currentTimeframe);
    if (currentSearch) params.append('query', currentSearch.substring(0, 100));

    const res = await fetch(`/api/opportunities?${params.toString()}`);
    if (requestId !== opportunitiesRequestId) return;

    if (!res.ok) {
      cardsGrid.innerHTML = safeHtml(`<div style="padding: 32px; color: var(--accent-terracotta-text);">Unable to load opportunities. Please refresh.</div>`);
      return;
    }

    const opportunities = await res.json();
    if (!Array.isArray(opportunities) || opportunities.length === 0) {
      cardsGrid.innerHTML = safeHtml(`
        <div style="padding: 48px 24px; text-align: center; color: var(--text-muted); grid-column: 1 / -1;">
          <h3 style="font-family: var(--font-serif); font-size: 20px; margin-bottom: 8px;">No matching opportunities found</h3>
          <p style="font-size: 13.5px;">Try selecting a different filter or clearing your search keywords.</p>
        </div>
      `);
      return;
    }

    renderCards(opportunities, cardsGrid);
  } catch (err) {
    if (requestId !== opportunitiesRequestId) return;
    cardsGrid.innerHTML = safeHtml(`<div style="padding: 32px; color: var(--text-muted);">An error occurred loading opportunities.</div>`);
  }
}

function renderCards(opportunities, container) {
  let cardsHtml = '';

  opportunities.forEach((opp) => {
    const isConfirmed = opp.date_status === 'confirmed';
    const isEstimated = opp.date_status === 'estimated';

    let dateBadge = '';
    if (isConfirmed && opp.days_left !== null && opp.days_left !== undefined) {
      const isUrgent = opp.days_left <= 7;
      const badgeClass = isUrgent ? 'badge-urgent' : 'badge-standard';
      dateBadge = `<span class="badge ${badgeClass}" title="Official Confirmed Deadline">${opp.days_left}d left</span>`;
    } else if (isEstimated) {
      dateBadge = `<span class="badge" style="background-color: var(--bg-surface-subtle); color: var(--text-muted); border: 1px dashed var(--border-medium);" title="Estimated Cycle Window">Estimated</span>`;
    }

    const typeColorClass = `type-${escapeHtml(opp.type || 'other')}`;
    const formattedDeadline = isConfirmed && opp.deadline ? formatDeadlineDate(opp.deadline) : null;
    const timingDisplay = isConfirmed && formattedDeadline
      ? `${ICONS.calendar} <span>Deadline: ${escapeHtml(formattedDeadline)}</span>`
      : (opp.estimated_window ? `${ICONS.calendar} <span>${escapeHtml(opp.estimated_window)}</span>` : `${ICONS.calendar} <span>Rolling / TBA</span>`);

    const verifiedDate = opp.verified_on ? formatDeadlineDate(opp.verified_on) : null;
    const verifiedBadge = verifiedDate
      ? `<span style="font-size: 11px; color: var(--text-subtle); display: inline-flex; align-items: center; gap: 4px;" title="Verified on official site">${ICONS.shieldCheck} Verified ${escapeHtml(verifiedDate)}</span>`
      : '';

    cardsHtml += `
      <article class="card" data-id="${escapeHtml(opp.id)}">
        <div class="card-top">
          <span class="type-tag ${typeColorClass}">${escapeHtml(opp.type || 'Opportunity')}</span>
          <div style="display: flex; align-items: center; gap: 8px;">
            ${dateBadge}
            <span class="score-badge" title="Match Percentage">${opp.match_percentage ?? 85}%</span>
          </div>
        </div>

        <h3 class="card-title">${escapeHtml(opp.title)}</h3>
        <div class="card-org">${escapeHtml(opp.organisation)}</div>

        <div class="card-meta">
          <div class="meta-row">${timingDisplay}</div>
          <div class="meta-row">
            ${ICONS.award}
            <span style="color: var(--text-main); font-weight: 500;">${escapeHtml(opp.funding)}</span>
          </div>
          <div class="meta-row">
            ${ICONS.mapPin}
            <span>${escapeHtml(opp.location)} • ${escapeHtml(opp.field)}</span>
          </div>
        </div>

        <div class="card-footer" style="margin-top: auto; padding-top: 14px; border-top: 1px solid var(--border-subtle); display: flex; align-items: center; justify-content: space-between;">
          ${verifiedBadge}
          <button class="btn btn-secondary btn-sm inspect-btn" data-id="${escapeHtml(opp.id)}">
            <span>Details</span>
            ${ICONS.externalLink}
          </button>
        </div>
      </article>
    `;
  });

  container.innerHTML = safeHtml(cardsHtml);

  // Attach card detail clicks
  container.querySelectorAll('.inspect-btn').forEach((btn) => {
    btn.addEventListener('click', (e) => {
      e.stopPropagation();
      const oppId = btn.dataset.id;
      if (oppId) openDetails(oppId);
    });
  });

  container.querySelectorAll('.card').forEach((card) => {
    card.addEventListener('click', () => {
      const oppId = card.dataset.id;
      if (oppId) openDetails(oppId);
    });
  });
}

async function openDetails(opportunityId) {
  const drawer = document.getElementById('drawer');
  const drawerBackdrop = document.getElementById('drawerBackdrop');
  const drawerTitle = document.getElementById('drawerTitle');
  const drawerContent = document.getElementById('drawerContent');
  const applyLink = document.getElementById('applyExternalLink');
  const sourceLink = document.getElementById('sourceExternalLink');

  if (!drawer || !drawerContent) return;

  drawerContent.innerHTML = safeHtml(`<div style="padding: 24px; color: var(--text-muted);">Loading opportunity details...</div>`);
  drawer.classList.add('open');
  if (drawerBackdrop) drawerBackdrop.classList.add('open');

  try {
    const res = await fetch(`/api/opportunity/${opportunityId}`);
    if (!res.ok) {
      drawerContent.innerHTML = safeHtml(`<div style="padding: 24px; color: var(--accent-terracotta-text);">Opportunity details could not be found.</div>`);
      return;
    }

    const opp = await res.json();
    if (drawerTitle) drawerTitle.textContent = opp.title;

    if (applyLink) {
      applyLink.href = opp.apply_url || opp.source_url || '#';
    }
    if (sourceLink) {
      sourceLink.href = opp.source_url || '#';
      sourceLink.style.display = opp.source_url ? 'inline-flex' : 'none';
    }

    const isConfirmed = opp.date_status === 'confirmed';
    const formattedDeadline = isConfirmed && opp.deadline ? formatDeadlineDate(opp.deadline) : 'Rolling / TBA';

    drawerContent.innerHTML = safeHtml(`
      <div style="display: flex; flex-direction: column; gap: 20px;">
        <div style="background-color: var(--bg-surface-subtle); border-radius: var(--radius-md); padding: 16px; border: 1px solid var(--border-subtle);">
          <div style="font-size: 12px; text-transform: uppercase; letter-spacing: 0.05em; color: var(--text-muted); margin-bottom: 4px;">Organizer & Type</div>
          <div style="font-size: 16px; font-weight: 600; color: var(--text-main);">${escapeHtml(opp.organisation)} • <span style="text-transform: capitalize;">${escapeHtml(opp.type)}</span></div>
          <div style="font-size: 13.5px; color: var(--text-muted); margin-top: 4px;">${escapeHtml(opp.location)} • ${escapeHtml(opp.field)}</div>
        </div>

        <div style="display: grid; grid-template-columns: 1fr 1fr; gap: 12px;">
          <div style="background-color: var(--bg-surface); border: 1px solid var(--border-subtle); border-radius: var(--radius-sm); padding: 12px;">
            <div style="font-size: 11px; text-transform: uppercase; color: var(--text-muted);">Deadline Status</div>
            <div style="font-size: 14px; font-weight: 600; color: ${isConfirmed ? 'var(--accent-sage-text)' : 'var(--accent-amber-text)'}; text-transform: capitalize; margin-top: 2px;">
              ${escapeHtml(opp.date_status)}
            </div>
            <div style="font-size: 12px; color: var(--text-muted); margin-top: 4px;">${escapeHtml(formattedDeadline)}</div>
          </div>

          <div style="background-color: var(--bg-surface); border: 1px solid var(--border-subtle); border-radius: var(--radius-sm); padding: 12px;">
            <div style="font-size: 11px; text-transform: uppercase; color: var(--text-muted);">Reward & Funding</div>
            <div style="font-size: 13.5px; font-weight: 600; color: var(--text-main); margin-top: 2px;">
              ${escapeHtml(opp.funding)}
            </div>
          </div>
        </div>

        ${opp.estimated_window ? `
          <div style="padding: 12px 14px; background-color: var(--accent-amber-bg); border: 1px solid var(--accent-amber-border); border-radius: var(--radius-sm); font-size: 13px; color: var(--accent-amber-text);">
            <strong>Estimated Application Window:</strong> ${escapeHtml(opp.estimated_window)}
          </div>
        ` : ''}

        <div style="background-color: var(--bg-surface-subtle); border: 1px solid var(--border-subtle); border-radius: var(--radius-sm); padding: 14px;">
          <div style="font-size: 12.5px; font-weight: 600; color: var(--text-main); margin-bottom: 4px;">Telegram Bot Application Assistant</div>
          <p style="font-size: 12.5px; color: var(--text-muted); line-height: 1.5;">
            To save this opportunity, receive deadline countdown alerts, or draft tailored application materials, message your private TodayTech Telegram bot:
          </p>
          <code style="display: block; margin-top: 8px; padding: 6px 10px; background: var(--bg-surface); border-radius: 4px; font-size: 12px; font-family: monospace;">
            /details ${escapeHtml(opp.id.substring(0, 8))}
          </code>
        </div>
      </div>
    `);
  } catch (err) {
    drawerContent.innerHTML = safeHtml(`<div style="padding: 24px; color: var(--accent-terracotta-text);">Error loading details.</div>`);
  }
}

function closeDetails() {
  const drawer = document.getElementById('drawer');
  const drawerBackdrop = document.getElementById('drawerBackdrop');
  if (drawer) drawer.classList.remove('open');
  if (drawerBackdrop) drawerBackdrop.classList.remove('open');
}

function showToast(message) {
  const container = document.getElementById('toastContainer');
  if (!container) return;

  const toast = document.createElement('div');
  toast.className = 'toast';
  toast.innerHTML = safeHtml(`<span>${escapeHtml(message)}</span>`);
  container.appendChild(toast);

  setTimeout(() => {
    toast.style.opacity = '0';
    setTimeout(() => toast.remove(), 250);
  }, 2500);
}
