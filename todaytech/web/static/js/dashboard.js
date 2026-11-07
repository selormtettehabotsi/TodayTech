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
 * TodayTech Dashboard Logic
 * Clean Vanilla JS with SVG icons, interactive filtering, TechHot Digest, and modal controls.
 */

// SVG Icon Helpers
const ICONS = {
  award: `<svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><circle cx="12" cy="8" r="7"></circle><polyline points="8.21 13.89 7 23 12 20 17 23 15.79 13.88"></polyline></svg>`,
  mapPin: `<svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><path d="M21 10c0 7-9 13-9 13s-9-6-9-13a9 9 0 0 1 18 0z"></path><circle cx="12" cy="10" r="3"></circle></svg>`,
  calendar: `<svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><rect x="3" y="4" width="18" height="18" rx="2" ry="2"></rect><line x1="16" y1="2" x2="16" y2="6"></line><line x1="8" y1="2" x2="8" y2="6"></line><line x1="3" y1="10" x2="21" y2="10"></line></svg>`,
  bookmarkOutline: `<svg width="13" height="13" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><path d="M19 21l-7-5-7 5V5a2 2 0 0 1 2-2h10a2 2 0 0 1 2 2z"></path></svg>`,
  bookmarkFilled: `<svg width="13" height="13" viewBox="0 0 24 24" fill="currentColor" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><path d="M19 21l-7-5-7 5V5a2 2 0 0 1 2-2h10a2 2 0 0 1 2 2z"></path></svg>`,
  check: `<svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2.5" stroke-linecap="round" stroke-linejoin="round"><polyline points="20 6 9 17 4 12"></polyline></svg>`,
  alert: `<svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><circle cx="12" cy="12" r="10"></circle><line x1="12" y1="8" x2="12" y2="12"></line><line x1="12" y1="16" x2="12.01" y2="16"></line></svg>`,
  externalLink: `<svg width="13" height="13" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><line x1="7" y1="17" x2="17" y2="7"></line><polyline points="7 7 17 7 17 17"></polyline></svg>`,
  copy: `<svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><rect x="9" y="9" width="13" height="13" rx="2" ry="2"></rect><path d="M5 15H4a2 2 0 0 1-2-2V4a2 2 0 0 1 2-2h9a2 2 0 0 1 2 2v1"></path></svg>`,
  cross: `<svg width="12" height="12" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2.5" stroke-linecap="round" stroke-linejoin="round"><line x1="18" y1="6" x2="6" y2="18"></line><line x1="6" y1="6" x2="18" y2="18"></line></svg>`,
  shieldCheck: `<svg width="13" height="13" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><path d="M12 22s8-4 8-10V5l-8-3-8 3v7c0 6 8 10 8 10z"></path><polyline points="9 12 11 14 15 10"></polyline></svg>`,
  document: `<svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><path d="M14 2H6a2 2 0 0 0-2 2v16a2 2 0 0 0 2 2h12a2 2 0 0 0 2-2V8z"></path><polyline points="14 2 14 8 20 8"></polyline><line x1="16" y1="13" x2="8" y2="13"></line><line x1="16" y1="17" x2="8" y2="17"></line></svg>`
};

function safeHtml(html) {
  if (typeof DOMPurify !== 'undefined' && DOMPurify.sanitize) {
    return DOMPurify.sanitize(html);
  }
  return escapeHtml(html);
}

let currentFilter = 'all';
let currentStatus = 'radar';
let currentClosingOnly = false;
let currentTimeframe = 'all';
let currentSearch = '';
let searchDebounceTimer = null;
let opportunitiesRequestId = 0;
let activeOpportunity = null;

let currentDigestTimeframe = 'day';
let latestDigestData = null;

document.addEventListener('DOMContentLoaded', () => {
  loadStats();
  loadOpportunities();
  loadPipeline();
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
      
      currentStatus = chip.dataset.status || 'radar';
      currentClosingOnly = chip.dataset.closing === 'true';
      loadOpportunities();
    });
  });

  // Drawer close events
  const drawerClose = document.getElementById('drawerClose');
  const drawerBackdrop = document.getElementById('drawerBackdrop');
  if (drawerClose) drawerClose.addEventListener('click', closeDrawer);
  if (drawerBackdrop) drawerBackdrop.addEventListener('click', closeDrawer);

  // Scan button
  const scanBtn = document.getElementById('scanBtn');
  if (scanBtn) {
    scanBtn.addEventListener('click', triggerScan);
  }

  // Settings Modal events
  const settingsBtn = document.getElementById('settingsBtn');
  const settingsModalClose = document.getElementById('settingsModalClose');
  const settingsCancelBtn = document.getElementById('settingsCancelBtn');
  const settingsSaveBtn = document.getElementById('settingsSaveBtn');
  const settingsModalBackdrop = document.getElementById('settingsModalBackdrop');

  if (settingsBtn) settingsBtn.addEventListener('click', openSettingsModal);
  if (settingsModalClose) settingsModalClose.addEventListener('click', closeSettingsModal);
  if (settingsCancelBtn) settingsCancelBtn.addEventListener('click', closeSettingsModal);
  if (settingsSaveBtn) settingsSaveBtn.addEventListener('click', saveSettings);
  if (settingsModalBackdrop) {
    settingsModalBackdrop.addEventListener('click', (e) => {
      if (e.target === settingsModalBackdrop) closeSettingsModal();
    });
  }

  // TechHot Digest Modal events
  const digestBtn = document.getElementById('digestBtn');
  const digestModalClose = document.getElementById('digestModalClose');
  const digestCloseBtn = document.getElementById('digestCloseBtn');
  const digestModalBackdrop = document.getElementById('digestModalBackdrop');
  const copyDigestBtn = document.getElementById('copyDigestBtn');
  const telegramDigestBtn = document.getElementById('telegramDigestBtn');

  if (digestBtn) digestBtn.addEventListener('click', openDigestModal);
  if (digestModalClose) digestModalClose.addEventListener('click', closeDigestModal);
  if (digestCloseBtn) digestCloseBtn.addEventListener('click', closeDigestModal);
  if (copyDigestBtn) copyDigestBtn.addEventListener('click', copyDigestMarkdown);
  if (telegramDigestBtn) telegramDigestBtn.addEventListener('click', sendDigestToTelegram);
  if (digestModalBackdrop) {
    digestModalBackdrop.addEventListener('click', (e) => {
      if (e.target === digestModalBackdrop) closeDigestModal();
    });
  }

  // Digest Modal timeframe tabs
  document.querySelectorAll('.digest-time-btn').forEach((btn) => {
    btn.addEventListener('click', () => {
      document.querySelectorAll('.digest-time-btn').forEach((b) => b.classList.remove('active'));
      btn.classList.add('active');
      currentDigestTimeframe = btn.dataset.timeframe || 'day';
      loadDigest(currentDigestTimeframe);
    });
  });
}

async function loadStats() {
  try {
    const res = await fetch('/api/stats');
    if (!res.ok) return;
    const data = await res.json();
    
    document.getElementById('statTotal').textContent = data.total_opportunities || '0';
    document.getElementById('statClosing').textContent = data.closing_soon_count || '0';
    document.getElementById('statSaved').textContent = data.saved_count || '0';
    document.getElementById('statDrafting').textContent = data.drafting_count || '0';
  } catch (err) {
    // Error loading stats
  }
}

async function loadOpportunities() {
  const container = document.getElementById('cardsGrid');
  if (!container) return;

  // Ignore responses from requests superseded by a newer filter/search change
  const requestId = ++opportunitiesRequestId;

  try {
    let url = `/api/opportunities?status=${currentStatus}`;
    if (currentClosingOnly) {
      url += `&closing_soon=true`;
    }
    if (currentFilter !== 'all') {
      url += `&type=${encodeURIComponent(currentFilter)}`;
    }
    if (currentTimeframe !== 'all') {
      url += `&timeframe=${encodeURIComponent(currentTimeframe)}`;
    }
    if (currentSearch) {
      url += `&query=${encodeURIComponent(currentSearch)}`;
    }

    const res = await fetch(url);
    if (!res.ok) throw new Error('API error');
    const items = await res.json();
    if (requestId !== opportunitiesRequestId) return;

    if (items.length === 0) {
      container.innerHTML = `
        <div style="grid-column: 1 / -1; padding: 48px 24px; text-align: center; background: #fff; border-radius: 12px; border: 1px solid var(--border-subtle);">
          <h3 style="font-family: var(--font-serif); font-size: 22px; margin-bottom: 8px;">No opportunities found</h3>
          <p style="color: var(--text-muted); font-size: 14px;">Try loosening your filters or horizon timeframe.</p>
        </div>
      `;
      return;
    }

    container.innerHTML = items.map((opp) => renderCard(opp)).join('');
  } catch (err) {
    if (requestId !== opportunitiesRequestId) return;
    container.innerHTML = `<p style="color: var(--text-muted); padding: 24px;">Failed to load opportunities.</p>`;
  }
}

async function loadPipeline() {
  try {
    const res = await fetch('/api/opportunities?status=pipeline');
    if (!res.ok) return;
    const items = await res.json();
    updatePipeline(items);
  } catch (err) {
    // Pipeline load error
  }
}

function renderCard(opp) {
  const typeClass = `type-${(opp.type || 'other').toLowerCase()}`;
  const dateStatus = (opp.date_status || 'unknown').toLowerCase();
  
  // Task 5: Only confirmed items count toward days-left badge with countdown
  let deadlineBadge = '';
  if (dateStatus === 'confirmed' && opp.deadline) {
    const formattedDate = formatDeadlineDate(opp.deadline);
    if (opp.days_left !== null && opp.days_left !== undefined) {
      if (opp.days_left < 0) {
        deadlineBadge = `<span class="tag-urgent" style="background:#FEE2E2; color:#B91C1C; border-color:#FECACA;">${ICONS.calendar} Closed (${formattedDate})</span>`;
      } else if (opp.days_left <= 3) {
        deadlineBadge = `<span class="tag-urgent">${ICONS.calendar} ${opp.days_left}d left • ${formattedDate} (GMT)</span>`;
      } else if (opp.days_left <= 7) {
        deadlineBadge = `<span class="tag-urgent" style="background:#FDF7ED; color:#C88A32; border-color:#F7E8CE;">${ICONS.calendar} ${opp.days_left}d left • ${formattedDate} (GMT)</span>`;
      } else {
        deadlineBadge = `<span class="tag-comfortable">${ICONS.calendar} ${formattedDate} (GMT) • ${opp.days_left}d left</span>`;
      }
    } else {
      deadlineBadge = `<span class="tag-comfortable">${ICONS.calendar} ${formattedDate} (GMT)</span>`;
    }
  } else if (dateStatus === 'estimated') {
    const windowText = opp.estimated_window || 'Expected window TBA';
    deadlineBadge = `<span class="tag-estimated" style="background:#ECFDF5; color:#047857; border:1px solid #A7F3D0; border-radius:4px; padding:2px 7px; font-size:12px; font-weight:500;">${ICONS.calendar} Estimated: ${escapeHtml(windowText)}</span>`;
  } else if (dateStatus === 'rolling') {
    deadlineBadge = `<span class="tag-comfortable">${ICONS.calendar} Rolling / Open Enrollment</span>`;
  } else {
    deadlineBadge = `<span class="tag-comfortable" style="background:#F3F4F6; color:#4B5563;">${ICONS.calendar} Date Unknown / Pending Verification</span>`;
  }

  const isSaved = opp.status === 'saved' || opp.status === 'in_progress' || opp.status === 'applied';
  const saveIcon = isSaved ? ICONS.bookmarkFilled : ICONS.bookmarkOutline;
  const saveLabel = isSaved ? 'Saved' : 'Save';
  const saveClass = isSaved ? 'btn-link btn-saved' : 'btn-link';
  const saveTitle = isSaved ? 'Click to unsave (remove from pipeline)' : 'Save to pipeline';

  const verifiedBadge = opp.verified_on ? `Verified ${formatDeadlineDate(opp.verified_on)}` : 'Pending Verification';
  const sourceLinkHtml = opp.source_url ? `
    <a href="${escapeHtml(opp.source_url)}" target="_blank" rel="noopener noreferrer" class="source-link" title="Open official organizer verification page" style="display:inline-flex; align-items:center; gap:4px; color:var(--text-muted); font-size:12px; text-decoration:none;">
      ${ICONS.externalLink}
      <span style="text-decoration:underline;">Source</span>
    </a>
  ` : '';

  return `
    <article class="card">
      <div class="card-top" data-action="open-assistant" data-id="${opp.id}" style="cursor: pointer;">
        <span class="type-tag ${typeClass}">${escapeHtml(opp.type)}</span>
        <span class="badge-score">
          ${ICONS.check}
          ${opp.fit_score}% Match
        </span>
      </div>

      <h2 class="card-title" data-action="open-assistant" data-id="${opp.id}" title="Click to view requirements and prepare drafts">
        ${escapeHtml(opp.title)}
      </h2>
      <p class="card-org">${escapeHtml(opp.organizer)}</p>

      <div class="card-metadata" data-action="open-assistant" data-id="${opp.id}" style="cursor: pointer;">
        <div class="meta-row">
          <span class="meta-icon">${ICONS.award}</span>
          <span class="meta-highlight">${escapeHtml(opp.reward || 'Recognition & Experience')}</span>
        </div>
        <div class="meta-row">
          <span class="meta-icon">${ICONS.mapPin}</span>
          <span>${escapeHtml(opp.mode)} • ${escapeHtml(opp.location)}</span>
        </div>
        <div class="meta-row">
          <span class="meta-icon" style="color: inherit;"></span>
          ${deadlineBadge}
        </div>
        <div class="meta-row meta-verification" style="display:flex; justify-content:space-between; align-items:center; margin-top:6px; padding-top:6px; border-top:1px dashed var(--border-subtle); font-size:12px; color:var(--text-muted);">
          <span style="display:inline-flex; align-items:center; gap:4px;">
            ${ICONS.shieldCheck}
            <span>${verifiedBadge}</span>
          </span>
          ${sourceLinkHtml}
        </div>
      </div>

      <p class="card-reason">${escapeHtml(opp.fit_reason || 'Strong match for your profile')}</p>

      <div class="card-actions">
        <button class="btn btn-primary btn-sm" data-action="open-assistant" data-id="${opp.id}">
          Prepare Application
        </button>
        <button class="${saveClass}" data-action="toggle-save" data-id="${opp.id}" data-status="${opp.status}" title="${saveTitle}" style="display: inline-flex; align-items: center; gap: 5px;">
          ${saveIcon}
          <span>${saveLabel}</span>
        </button>
      </div>
    </article>
  `;
}

function updatePipeline(items) {
  const pipelineContainer = document.getElementById('pipelineList');
  if (!pipelineContainer) return;

  const activeItems = items.filter(i => i.status === 'saved' || i.status === 'in_progress' || i.status === 'applied');
  
  if (activeItems.length === 0) {
    pipelineContainer.innerHTML = `
      <p style="color: var(--text-muted); font-size: 13px; font-style: italic;">
        No targets saved yet. Click "Save" on any opportunity card to track it here.
      </p>
    `;
    return;
  }

  pipelineContainer.innerHTML = activeItems.map(item => {
    const isConfirmed = (item.date_status || '').toLowerCase() === 'confirmed';
    let dateLine = '';
    if (isConfirmed && item.deadline) {
      dateLine = `${ICONS.calendar} ${formatDeadlineDate(item.deadline)} (GMT)${item.days_left !== null && item.days_left !== undefined ? ' • ' + item.days_left + 'd left' : ''}`;
    } else if ((item.date_status || '').toLowerCase() === 'estimated') {
      dateLine = `${ICONS.calendar} Estimated: ${escapeHtml(item.estimated_window || 'TBA')}`;
    } else {
      dateLine = `${ICONS.calendar} Rolling / Open Enrollment`;
    }

    return `
      <div class="pipeline-item" data-action="open-assistant" data-id="${item.id}" title="Click to open application studio">
        <div class="pipeline-item-top">
          <span class="pipeline-item-title" title="${escapeHtml(item.title)}">${escapeHtml(item.title)}</span>
          <div class="pipeline-item-actions">
            <span class="status-badge status-${item.status}">${item.status.replace('_', ' ')}</span>
            <button class="pipeline-remove-btn" data-action="unsave" data-id="${item.id}" title="Unsave / Remove from pipeline" aria-label="Unsave opportunity">
              ${ICONS.cross}
            </button>
          </div>
        </div>
        <div style="font-size: 12px; color: var(--text-muted); display: flex; align-items: center; gap: 5px; margin-top: 4px;">
          ${dateLine}
        </div>
      </div>
    `;
  }).join('');
}

async function unsaveOpportunity(id) {
  try {
    await fetch(`/api/opportunity/${id}/state`, {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ status: 'radar' })
    });
    showToast('Removed from active pipeline');
    loadStats();
    loadOpportunities();
    loadPipeline();
    if (activeOpportunity && activeOpportunity.id === id) {
      openAssistant(id);
    }
  } catch (err) {
    showToast('Failed to update pipeline');
  }
}

async function toggleSave(id, currentStatus) {
  const isCurrentlySaved = currentStatus === 'saved' || currentStatus === 'in_progress' || currentStatus === 'applied';
  const nextStatus = isCurrentlySaved ? 'radar' : 'saved';
  try {
    await fetch(`/api/opportunity/${id}/state`, {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ status: nextStatus })
    });
    showToast(nextStatus === 'saved' ? 'Saved to your active pipeline' : 'Removed from active pipeline');
    loadStats();
    loadOpportunities();
    loadPipeline();
    if (activeOpportunity && activeOpportunity.id === id) {
      openAssistant(id);
    }
  } catch (err) {
    showToast('Failed to update opportunity state');
  }
}

async function toggleSaveFromDrawer(id, currentStatus) {
  await toggleSave(id, currentStatus);
}

async function openAssistant(id) {
  const drawer = document.getElementById('drawer');
  const backdrop = document.getElementById('drawerBackdrop');
  const content = document.getElementById('drawerContent');

  drawer.classList.add('active');
  backdrop.classList.add('active');
  content.innerHTML = `<div style="padding: 40px; text-align: center; color: var(--text-muted);">Loading opportunity details...</div>`;

  try {
    const res = await fetch(`/api/opportunity/${encodeURIComponent(id)}`);
    if (!res.ok) {
      throw new Error(`Server returned ${res.status} ${res.statusText}`);
    }
    const data = await res.json();
    activeOpportunity = data;

    document.getElementById('drawerTitle').textContent = data.title || 'Opportunity Details';

    const isSaved = (data.status === 'saved' || data.status === 'in_progress' || data.status === 'applied');
    const statusText = data.status === 'in_progress' ? 'Application in Progress' : (data.status === 'applied' ? 'Applied' : (data.status === 'saved' ? 'Saved in Pipeline' : 'On Radar'));
    const toggleBtnText = isSaved ? `${ICONS.cross} Remove from Pipeline (Unsave)` : `${ICONS.bookmarkOutline} Save to Pipeline`;
    const typeLower = (data.type || 'opportunity').toLowerCase();

    content.innerHTML = `
      <!-- Drawer Status Action Bar -->
      <div class="drawer-status-bar">
        <div class="drawer-status-info">
          <span style="font-weight: 500; color: var(--text-muted);">Status:</span>
          <span class="status-badge status-${data.status || 'radar'}">${statusText}</span>
        </div>
        <button class="btn btn-secondary btn-sm" data-action="drawer-save" data-id="${data.id}" data-status="${data.status || 'radar'}">
          ${toggleBtnText}
        </button>
      </div>

      <div class="studio-section">
        <div style="display: flex; gap: 8px; margin-bottom: 16px; flex-wrap: wrap;">
          <span class="type-tag type-${typeLower}">${escapeHtml(data.type || 'Opportunity')}</span>
          <span class="badge-score">${ICONS.check} ${data.fit_score ?? 80}% Match</span>
          <span class="tag-comfortable">Organized by ${escapeHtml(data.organizer || 'Independent')}</span>
        </div>
        <p style="font-size: 14px; color: var(--text-muted); margin-bottom: 16px;">
          ${escapeHtml(data.fit_reason || 'Matched based on your skills and preferences.')}
        </p>
      </div>

      <div class="studio-section">
        <h3 class="studio-section-title">Eligibility Criteria Breakdown</h3>
        <div class="checklist-box">
          <div class="checklist-item">
            <span style="color: var(--accent-sage-text);">${ICONS.check}</span>
            <span><strong>Verdict:</strong> ${escapeHtml(data.eligibility_verdict || 'Eligible')} — ${escapeHtml(data.eligibility_reason || 'Meets criteria')}</span>
          </div>
          <div class="checklist-item">
            <span style="color: var(--text-muted);">${ICONS.mapPin}</span>
            <span><strong>Mode & Location:</strong> ${escapeHtml(data.mode || 'Online')} (${escapeHtml(data.location || 'Global')})</span>
          </div>
          <div class="checklist-item">
            <span style="color: var(--text-muted);">${ICONS.award}</span>
            <span><strong>Fee:</strong> ${data.cost && data.cost > 0 ? '$' + data.cost : 'No application fee'}</span>
          </div>
          <div class="checklist-item">
            <span style="color: var(--text-muted);">${ICONS.calendar}</span>
            <span><strong>Date Status:</strong> ${escapeHtml((data.date_status || 'unknown').toUpperCase())} ${data.deadline ? '— ' + formatDeadlineDate(data.deadline) + ' (GMT 0:00)' + (data.days_left !== null && data.days_left !== undefined ? ' (' + (data.days_left < 0 ? 'Closed' : data.days_left + ' days left') + ')' : '') : (data.estimated_window ? '— Estimated: ' + escapeHtml(data.estimated_window) : '— Rolling / Open Enrollment')}</span>
          </div>
          <div class="checklist-item">
            <span style="color: var(--text-muted);">${ICONS.shieldCheck}</span>
            <span><strong>Verification:</strong> ${data.verified_on ? 'Verified on ' + formatDeadlineDate(data.verified_on) : 'Pending Verification'} ${data.source_url ? `• <a href="${escapeHtml(data.source_url)}" target="_blank" rel="noopener noreferrer" style="color:var(--text-color); text-decoration:underline;">Official Source Page</a>` : ''}</span>
          </div>
        </div>
      </div>

      <div class="studio-section">
        <div style="display: flex; align-items: center; justify-content: space-between; margin-bottom: 12px;">
          <h3 class="studio-section-title" style="margin: 0;">Tailored Application Studio</h3>
          <button class="btn btn-primary btn-sm" id="generateDraftBtn" data-action="generate-drafts" data-id="${data.id}">
            Generate Tailored Draft
          </button>
        </div>
        <div id="draftContainer">
          <p style="font-size: 13.5px; color: var(--text-muted); line-height: 1.6;">
            Click <strong>Generate Tailored Draft</strong> to run TodayTech's application assistant. It analyzes the opportunity requirements and synthesizes your profile into a focused personal statement or project pitch.
          </p>
        </div>
      </div>
    `;

    document.getElementById('applyExternalLink').href = data.application_url || '#';
    fetchDrafts(data.id);
  } catch (err) {
    content.innerHTML = `
      <div style="padding: 24px;">
        <p style="color: var(--accent-terracotta-text); font-weight: 500; margin-bottom: 8px;">
          Failed to load opportunity details (${escapeHtml(err.message || 'Error')}).
        </p>
        <p style="color: var(--text-muted); font-size: 13px;">
          Please ensure your TodayTech container is rebuilt with latest updates and refresh your browser.
        </p>
      </div>
    `;
  }
}

async function fetchDrafts(id) {
  const draftContainer = document.getElementById('draftContainer');
  if (!draftContainer) return;

  try {
    const res = await fetch(`/api/opportunity/${id}/drafts`);
    const data = await res.json();
    if (data.has_drafts && data.files && data.files.length > 0) {
      renderDraftFiles(id, data.files, data.content);
    }
  } catch (err) {
    // Drafts not ready
  }
}

function renderDraftFiles(id, files, content) {
  const draftContainer = document.getElementById('draftContainer');
  if (!draftContainer) return;

  let downloadButtons = '<div style="display: flex; gap: 8px; flex-wrap: wrap; margin-bottom: 14px;">';
  files.forEach(f => {
    downloadButtons += `
      <a href="${f.download_url}" download="${f.name}" class="btn btn-secondary btn-sm" 
         style="display: inline-flex; align-items: center; gap: 6px; text-decoration: none; font-weight: 500;">
        <span>${ICONS.document}</span>
        <span>Download ${f.name}</span>
        <svg width="12" height="12" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2">
          <path d="M21 15v4a2 2 0 0 1-2 2H5a2 2 0 0 1-2-2v-4"></path>
          <polyline points="7 10 12 15 17 10"></polyline>
          <line x1="12" y1="15" x2="12" y2="3"></line>
        </svg>
      </a>
    `;
  });
  downloadButtons += '</div>';

  const displayText = content && content.trim() ? content : 'Draft files generated successfully. Use the download buttons above to get Word .docx and .md files on your device.';

  draftContainer.innerHTML = `
    <div style="display: flex; align-items: center; justify-content: space-between; margin-bottom: 10px;">
      <span style="font-size: 12.5px; font-weight: 600; color: var(--accent-sage-text);">Materials Ready to Download</span>
      <button class="btn-link" data-action="copy-draft" style="display: inline-flex; align-items: center; gap: 5px; cursor: pointer; border: none; background: transparent;">
        ${ICONS.copy}
        <span id="copyBtnLabel">Copy Text</span>
      </button>
    </div>
    ${downloadButtons}
    <div class="draft-box" id="draftTextBox">${escapeHtml(displayText)}</div>
  `;
}

async function generateDrafts(id) {
  const draftContainer = document.getElementById('draftContainer');
  const generateBtn = document.getElementById('generateDraftBtn');
  
  generateBtn.disabled = true;
  generateBtn.textContent = 'Generating draft...';
  draftContainer.innerHTML = `
    <div style="padding: 24px; text-align: center; color: var(--text-muted); background: var(--bg-surface-subtle); border-radius: 8px;">
      <p style="font-weight: 500; margin-bottom: 6px;">Synthesizing requirements and profile experience...</p>
      <p style="font-size: 12.5px;">Generating Markdown and Word DOCX drafts...</p>
    </div>
  `;

  try {
    const res = await fetch(`/api/opportunity/${id}/apply`, { method: 'POST' });
    const result = await res.json();

    await fetchDrafts(id);
    showToast('Application drafts created and ready to download!');
    loadStats();
    loadOpportunities();
    loadPipeline();
  } catch (err) {
    draftContainer.innerHTML = `<p style="color: var(--accent-terracotta-text);">Draft generation failed. Make sure your LLM provider key is set in .env.</p>`;
  } finally {
    generateBtn.disabled = false;
    generateBtn.textContent = 'Regenerate Draft';
  }
}

function copyDraftText() {
  const box = document.getElementById('draftTextBox');
  if (!box) return;
  const text = box.innerText || box.textContent;
  if (!text || !text.trim()) {
    showToast('No draft text to copy.');
    return;
  }

  const copyLabel = document.getElementById('copyBtnLabel');
  function onSuccess() {
    showToast('Copied draft materials to clipboard!');
    if (copyLabel) {
      const orig = copyLabel.textContent;
      copyLabel.textContent = 'Copied!';
      setTimeout(() => { copyLabel.textContent = orig; }, 2000);
    }
  }

  // 1. Modern clipboard API (works on HTTPS or localhost)
  if (navigator.clipboard && window.isSecureContext) {
    navigator.clipboard.writeText(text).then(onSuccess).catch(() => fallbackCopy(text, onSuccess));
  } else {
    // 2. Robust fallback for HTTP / EC2 IP addresses on mobile and desktop
    fallbackCopy(text, onSuccess);
  }
}

function fallbackCopy(text, onSuccess) {
  try {
    const textArea = document.createElement('textarea');
    textArea.value = text;
    textArea.style.position = 'fixed';
    textArea.style.left = '-9999px';
    textArea.style.top = '-9999px';
    textArea.style.opacity = '0';
    textArea.setAttribute('readonly', '');
    document.body.appendChild(textArea);
    textArea.focus();
    textArea.select();
    textArea.setSelectionRange(0, 999999);
    
    const successful = document.execCommand('copy');
    document.body.removeChild(textArea);
    if (successful) {
      onSuccess();
    } else {
      showToast('Please select and copy text manually');
    }
  } catch (err) {
    showToast('Please select and copy text manually');
  }
}

function closeDrawer() {
  document.getElementById('drawer').classList.remove('active');
  document.getElementById('drawerBackdrop').classList.remove('active');
}

// ──────────────────────────────────────────────────────────────────────────
// TechHot Daily Digest Modal
// ──────────────────────────────────────────────────────────────────────────

function openDigestModal() {
  const modalBackdrop = document.getElementById('digestModalBackdrop');
  if (!modalBackdrop) return;
  modalBackdrop.classList.add('active');
  loadDigest(currentDigestTimeframe);
}

function closeDigestModal() {
  const modalBackdrop = document.getElementById('digestModalBackdrop');
  if (modalBackdrop) modalBackdrop.classList.remove('active');
}

async function loadDigest(timeframe = 'day') {
  const closingList = document.getElementById('digestClosingList');
  const hotList = document.getElementById('digestHotList');
  const closingCount = document.getElementById('digestClosingCount');
  const hotCount = document.getElementById('digestHotCount');
  const dateLabel = document.getElementById('digestDateLabel');

  if (closingList) closingList.innerHTML = `<div style="padding: 24px; text-align: center; color: var(--text-muted); font-size: 13px;">Loading closing targets...</div>`;
  if (hotList) hotList.innerHTML = `<div style="padding: 24px; text-align: center; color: var(--text-muted); font-size: 13px;">Loading top recommendations...</div>`;

  try {
    const res = await fetch(`/api/digest?timeframe=${encodeURIComponent(timeframe)}`);
    if (!res.ok) throw new Error('Failed to load digest');
    const data = await res.json();
    latestDigestData = data;

    if (dateLabel && data.date) {
      dateLabel.textContent = `Curated briefing for ${data.date} • ${timeframe === 'day' ? 'Today' : (timeframe === 'week' ? 'This Week' : 'This Month')}`;
    }

    // Populate Closing Soon
    const closingItems = data.closing_soon || [];
    if (closingCount) closingCount.textContent = closingItems.length;
    if (closingList) {
      if (closingItems.length === 0) {
        closingList.innerHTML = `
          <div style="padding: 24px; text-align: center; background: var(--bg-surface-subtle); border-radius: 8px; color: var(--text-muted); font-size: 13px;">
            No applications closing within this horizon.
          </div>
        `;
      } else {
        closingList.innerHTML = closingItems.map(item => renderDigestCard(item, true)).join('');
      }
    }

    // Populate Hot Picks
    const hotItems = data.hot_picks || [];
    if (hotCount) hotCount.textContent = hotItems.length;
    if (hotList) {
      if (hotItems.length === 0) {
        hotList.innerHTML = `
          <div style="padding: 24px; text-align: center; background: var(--bg-surface-subtle); border-radius: 8px; color: var(--text-muted); font-size: 13px;">
            No opportunities found for this horizon.
          </div>
        `;
      } else {
        hotList.innerHTML = hotItems.map(item => renderDigestCard(item, false)).join('');
      }
    }
  } catch (err) {
    if (closingList) closingList.innerHTML = `<p style="color: var(--accent-terracotta-text); padding: 16px;">Failed to load digest.</p>`;
    if (hotList) hotList.innerHTML = `<p style="color: var(--accent-terracotta-text); padding: 16px;">Failed to load digest.</p>`;
  }
}

function renderDigestCard(item, isUrgent = false) {
  const typeClass = `type-${(item.type || '').toLowerCase()}`;
  let deadlineTag = `<span class="tag-comfortable">${ICONS.calendar} ${item.deadline || 'Open'}</span>`;
  if (item.days_left !== null && item.days_left !== undefined) {
    deadlineTag = `<span class="tag-urgent">${ICONS.calendar} ${item.days_left}d left</span>`;
  }

  return `
    <div class="digest-item-card" data-action="open-digest-opp" data-id="${item.id}" title="Click to view full details and application materials">
      <div class="digest-item-header">
        <div style="display: flex; align-items: center; gap: 8px;">
          <span class="type-tag ${typeClass}" style="font-size: 10px; padding: 2px 8px;">${item.type}</span>
          <span class="badge-score" style="font-size: 10.5px; padding: 2px 7px;">${item.fit_score}% Match</span>
        </div>
        ${deadlineTag}
      </div>
      <div class="digest-item-title">${escapeHtml(item.title)}</div>
      <div class="digest-item-org">${escapeHtml(item.organizer)} • ${escapeHtml(item.mode)}</div>
      <div class="digest-item-meta">
        <span style="color: var(--text-main); font-weight: 500;">${escapeHtml(item.reward)}</span>
        <span style="color: var(--accent-sage-text); font-weight: 500;">View in Studio &rarr;</span>
      </div>
    </div>
  `;
}

function openOpportunityFromDigest(id) {
  closeDigestModal();
  openAssistant(id);
}

function copyDigestMarkdown() {
  if (!latestDigestData || !latestDigestData.digest_markdown) {
    showToast('No digest content available to copy.');
    return;
  }
  navigator.clipboard.writeText(latestDigestData.digest_markdown).then(() => {
    showToast('Copied TechHot digest markdown to clipboard!');
  });
}

async function sendDigestToTelegram() {
  const btn = document.getElementById('telegramDigestBtn');
  const btnText = document.getElementById('telegramBtnText');
  if (btn) btn.disabled = true;
  if (btnText) btnText.textContent = 'Sending...';

  try {
    const res = await fetch('/api/digest/send-telegram', { method: 'POST' });
    const result = await res.json();
    if (res.ok) {
      showToast('Daily digest dispatched to your Telegram chat!');
    } else {
      showToast(result.detail || 'Failed to dispatch Telegram message. Check .env');
    }
  } catch (err) {
    showToast('Network error while dispatching to Telegram.');
  } finally {
    if (btn) btn.disabled = false;
    if (btnText) btnText.textContent = 'Send to Telegram';
  }
}

// ──────────────────────────────────────────────────────────────────────────
// Settings / Profile Modal
// ──────────────────────────────────────────────────────────────────────────

async function openSettingsModal() {
  const modalBackdrop = document.getElementById('settingsModalBackdrop');
  modalBackdrop.classList.add('active');

  try {
    const res = await fetch('/api/profile');
    if (!res.ok) return;
    const profile = await res.json();

    document.getElementById('profName').value = profile.name || '';
    document.getElementById('profCountry').value = profile.country || '';
    document.getElementById('profUniversity').value = profile.university || '';
    document.getElementById('profLevel').value = profile.level || 'undergraduate';
    document.getElementById('profField').value = profile.field_of_study || '';
    document.getElementById('profSkills').value = (profile.skills || []).join(', ');
    document.getElementById('profInterests').value = (profile.interests || []).join(', ');
  } catch (err) {
    // Profile load error
  }
}

function closeSettingsModal() {
  document.getElementById('settingsModalBackdrop').classList.remove('active');
}

async function saveSettings() {
  const saveBtn = document.getElementById('settingsSaveBtn');
  saveBtn.disabled = true;
  saveBtn.textContent = 'Saving...';

  const payload = {
    name: document.getElementById('profName').value.trim(),
    country: document.getElementById('profCountry').value.trim(),
    university: document.getElementById('profUniversity').value.trim(),
    level: document.getElementById('profLevel').value,
    field_of_study: document.getElementById('profField').value.trim(),
    skills: document.getElementById('profSkills').value.split(',').map(s => s.trim()).filter(Boolean),
    interests: document.getElementById('profInterests').value.split(',').map(i => i.trim()).filter(Boolean),
  };

  try {
    const res = await fetch('/api/profile', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify(payload)
    });

    if (res.ok) {
      closeSettingsModal();
      showToast('Profile and preferences updated successfully');
      loadOpportunities();
    } else {
      showToast('Failed to save profile preferences');
    }
  } catch (err) {
    showToast('Failed to save profile preferences');
  } finally {
    saveBtn.disabled = false;
    saveBtn.textContent = 'Save Preferences';
  }
}

let scanPollInterval = null;

async function manualRefresh() {
  const refreshBtn = document.getElementById('refreshBtn');
  const refreshText = document.getElementById('refreshBtnText');
  if (refreshBtn) refreshBtn.disabled = true;
  if (refreshText) refreshText.textContent = 'Refreshing...';

  try {
    await Promise.all([loadStats(), loadOpportunities(), loadPipeline()]);
    showToast('Feed refreshed with latest data!');
  } catch (err) {
    showToast('Failed to refresh feed.');
  } finally {
    if (refreshBtn) refreshBtn.disabled = false;
    if (refreshText) refreshText.textContent = 'Refresh';
  }
}

async function triggerScan() {
  const scanBtn = document.getElementById('scanBtn');
  const scanText = document.getElementById('scanBtnText');
  if (scanBtn.disabled) return;

  scanBtn.disabled = true;
  scanText.textContent = 'Starting scan...';

  try {
    const res = await fetch('/api/scan', { method: 'POST', cache: 'no-store' });
    const data = await res.json();
    showToast(data.message || 'Scan started. Searching live opportunities...');

    let seconds = 0;
    if (scanPollInterval) clearInterval(scanPollInterval);

    scanPollInterval = setInterval(async () => {
      seconds += 2;
      scanText.textContent = `Scanning (${seconds}s)...`;

      try {
        const statusRes = await fetch('/api/scan/status', { cache: 'no-store' });
        if (statusRes.ok) {
          const status = await statusRes.json();
          if (!status.running) {
            clearInterval(scanPollInterval);
            scanPollInterval = null;
            scanBtn.disabled = false;
            scanText.textContent = 'Scan Sources';

            const newMsg = status.items_new ? ` (${status.items_new} new!)` : '';
            showToast(`Scan complete! Found ${status.items_found || 0} opportunities${newMsg}.`);
            await loadStats();
            await loadOpportunities();
            await loadPipeline();
          }
        }
      } catch (pollErr) {
        // Poll status retry
      }

      if (seconds >= 120) {
        clearInterval(scanPollInterval);
        scanPollInterval = null;
        scanBtn.disabled = false;
        scanText.textContent = 'Scan Sources';
        loadStats();
        loadOpportunities();
        loadPipeline();
      }
    }, 2000);

  } catch (err) {
    showToast('Failed to start scan.');
    scanBtn.disabled = false;
    scanText.textContent = 'Scan Sources';
  }
}

// Background auto-refresh feed every 45s if tab is visible
setInterval(() => {
  if (!document.hidden && !scanPollInterval) {
    loadStats();
    loadOpportunities();
    loadPipeline();
  }
}, 45000);

// ──────────────────────────────────────────────────────────────────────────
// Toast Notifications
// ──────────────────────────────────────────────────────────────────────────

function showToast(message) {
  const container = document.getElementById('toastContainer');
  if (!container) return;

  const toast = document.createElement('div');
  toast.className = 'toast';
  toast.innerHTML = `
    <span style="color: var(--accent-sage-text); display: flex; align-items: center;">${ICONS.check}</span>
    <span>${escapeHtml(message)}</span>
  `;

  container.appendChild(toast);
  setTimeout(() => toast.classList.add('visible'), 10);

  setTimeout(() => {
    toast.classList.remove('visible');
    setTimeout(() => toast.remove(), 250);
  }, 3500);
}

function escapeHtml(text) {
  if (!text) return '';
  return String(text)
    .replace(/&/g, '&amp;')
    .replace(/</g, '&lt;')
    .replace(/>/g, '&gt;')
    .replace(/"/g, '&quot;')
    .replace(/'/g, '&#039;');
}

// ──────────────────────────────────────────────────────────────────────────
// Global Event Delegation (Strict CSP Compliant, Zero Inline Handlers)
// ──────────────────────────────────────────────────────────────────────────

document.addEventListener('click', (e) => {
  const actionEl = e.target.closest('[data-action]');
  if (!actionEl) return;
  const action = actionEl.getAttribute('data-action');
  const id = actionEl.getAttribute('data-id');

  if (action === 'open-assistant') {
    openAssistant(id);
  } else if (action === 'toggle-save') {
    e.stopPropagation();
    const status = actionEl.getAttribute('data-status') || 'radar';
    toggleSave(id, status);
  } else if (action === 'unsave') {
    e.stopPropagation();
    unsaveOpportunity(id);
  } else if (action === 'drawer-save') {
    const status = actionEl.getAttribute('data-status') || 'radar';
    toggleSaveFromDrawer(id, status);
  } else if (action === 'generate-drafts') {
    generateDrafts(id);
  } else if (action === 'copy-draft') {
    copyDraftText();
  } else if (action === 'open-digest-opp') {
    openOpportunityFromDigest(id);
  }
});

document.addEventListener('DOMContentLoaded', () => {
  const refreshBtn = document.getElementById('refreshBtn');
  if (refreshBtn) {
    refreshBtn.addEventListener('click', manualRefresh);
  }
});
