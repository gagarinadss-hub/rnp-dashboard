'use strict';

// ── State ──────────────────────────────────────────────────────────────────
let dashState     = null;   // current dashboard payload
let activeLaunchId = null;  // null → GSheets live, number → DB launch
let allLaunches   = [];     // cached list for the switcher
let selectedDay   = 'all';  // Channels tab day picker
const REFRESH_MS  = 5 * 60 * 1000;
let charts        = {};

// ── Palette ────────────────────────────────────────────────────────────────
const PALETTE = [
  '#7C3AED','#A855F7','#EC4899','#F97316','#EAB308',
  '#10B981','#3B82F6','#06B6D4','#84CC16','#F43F5E',
];

// ── Helpers ────────────────────────────────────────────────────────────────
function fmt(n) {
  if (n == null) return '—';
  return Number(n).toLocaleString('ru-RU');
}
function fmtDate(s) {
  if (!s) return '—';
  const d = new Date(s + 'T00:00:00');
  return d.toLocaleDateString('ru-RU', { day: 'numeric', month: 'short' });
}
function fmtTime(iso) {
  if (!iso) return '—';
  return new Date(iso).toLocaleTimeString('ru-RU', { hour: '2-digit', minute: '2-digit' });
}
function clamp(v, lo, hi) { return Math.min(Math.max(v, lo), hi); }
function pctClass(p) {
  if (!p || p === 0) return 'pct-zero';
  if (p >= 100) return 'pct-great';
  if (p >= 60)  return 'pct-good';
  if (p >= 30)  return 'pct-warn';
  return 'pct-danger';
}
function isDbSource() {
  return dashState?.overview?._source === 'db';
}

// ── Tab switching ──────────────────────────────────────────────────────────
document.querySelectorAll('.nav-item[data-tab]').forEach(btn => {
  btn.addEventListener('click', () => {
    const tab = btn.dataset.tab;
    document.querySelectorAll('.nav-item[data-tab]').forEach(b => b.classList.remove('active'));
    document.querySelectorAll('.tab-content').forEach(t => t.classList.remove('active'));
    btn.classList.add('active');
    document.getElementById('tab-' + tab).classList.add('active');
    if (tab === 'launches') loadLaunches();
    if (tab === 'channels') renderChannelsTab();
    if (tab === 'utm')      loadUtmTab();
    if (tab === 'compare')  initCompareTab();
  });
});

// ── Launch Selector ────────────────────────────────────────────────────────
async function loadLaunchSelector() {
  try {
    allLaunches = await fetch('/api/launches').then(r => r.json());
    const sel = document.getElementById('launchSelector');
    sel.innerHTML = '';

    // Live option (GSheets)
    const liveOpt = document.createElement('option');
    liveOpt.value = '';
    liveOpt.textContent = '⚡ Текущий (live)';
    sel.appendChild(liveOpt);

    allLaunches.forEach(l => {
      const opt = document.createElement('option');
      opt.value = l.id;
      opt.textContent = `${l.name}${l.is_active ? ' ★' : ''}`;
      sel.appendChild(opt);
    });

    // DB-first: auto-select the active launch on first load
    if (activeLaunchId === null) {
      const active = allLaunches.find(l => l.is_active);
      if (active) {
        activeLaunchId = active.id;
      } else if (allLaunches.length > 0) {
        activeLaunchId = allLaunches[0].id;
      }
    }
    sel.value = activeLaunchId ?? '';
  } catch (err) {
    console.error('Ошибка загрузки списка запусков:', err);
  }
}

document.getElementById('launchSelector').addEventListener('change', async e => {
  const val = e.target.value;
  activeLaunchId = val ? parseInt(val, 10) : null;
  selectedDay    = 'all';
  await loadDashboard();
  // Refresh channels tab if open
  if (document.querySelector('.nav-item[data-tab="channels"]')?.classList.contains('active')) {
    renderChannelsTab();
  }
});

// ── Dashboard ──────────────────────────────────────────────────────────────
async function loadDashboard(force = false) {
  const btn = document.getElementById('refreshBtn');
  btn.textContent = 'Загрузка...';
  btn.disabled = true;

  try {
    let res;
    if (activeLaunchId !== null) {
      res = await fetch(`/api/launches/${activeLaunchId}/dashboard`);
    } else {
      const endpoint = force ? '/api/refresh' : '/api/dashboard';
      res = await fetch(endpoint);
    }
    if (!res.ok) throw new Error(`HTTP ${res.status}`);
    dashState = await res.json();
    renderDashboard();
  } catch (err) {
    console.error('Ошибка загрузки:', err);
    document.getElementById('updatedBadge').textContent = 'Ошибка загрузки';
  } finally {
    btn.textContent = 'Обновить';
    btn.disabled = false;
  }
}

function renderDashboard() {
  if (!dashState) return;
  const o = dashState.overview;
  const d = dashState.daily;
  const f = dashState.forecast;

  // Sidebar updated badge
  document.getElementById('updatedBadge').textContent = `Обновлено: ${fmtTime(o.last_updated)}`;

  // Sync selector to current state
  document.getElementById('launchSelector').value = activeLaunchId ?? '';

  // Channels tab subtitle
  document.getElementById('channels-launch-name').textContent = o.launch_name;

  // ── Hero card ──────────────────────────────────────────────────────────
  document.getElementById('launch-name').textContent  = o.launch_name;
  document.getElementById('launch-dates').textContent = `${fmtDate(o.start_date)} — ${fmtDate(o.end_date)}`;
  document.getElementById('hero-pct').textContent     = `${o.completion_pct}%`;
  document.getElementById('kpi-days-left').textContent = o.days_remaining ?? '—';
  document.getElementById('launch-day-hero').textContent = `День ${o.days_elapsed} / ${o.days_total}`;
  document.getElementById('heroFill').style.width    = `${clamp(o.completion_pct, 0, 100)}%`;
  document.getElementById('hero-actual-label').textContent = `${fmt(o.total_actual)} факт`;
  document.getElementById('hero-forecast').textContent = `Прогноз: ${fmt(f.projected_total)} (${f.projected_pct}%)`;

  // ── KPI cards ──────────────────────────────────────────────────────────
  // Карточка 1: Общий план
  document.getElementById('hero-plan').textContent       = fmt(o.total_plan);
  document.getElementById('launch-dates-kpi').textContent = `${fmtDate(o.start_date)} — ${fmtDate(o.end_date)}`;

  // Карточка 2: Факт сейчас
  document.getElementById('hero-actual').textContent  = fmt(o.total_actual);
  document.getElementById('kpi-fact-pct').textContent = `${o.completion_pct}% от плана`;

  // Карточка 3: Вчера
  const yd = o.yesterday_actual ?? 0;
  const ydDelta = o.yesterday_delta ?? 0;
  document.getElementById('kpi-yesterday').textContent = fmt(yd);
  const deltaEl = document.getElementById('kpi-yesterday-delta');
  if (ydDelta !== 0) {
    const sign = ydDelta > 0 ? '+' : '';
    deltaEl.textContent = `${sign}${fmt(ydDelta)} vs позавчера`;
    deltaEl.className = `kpi-sub ${ydDelta > 0 ? 'delta-up' : 'delta-down'}`;
  } else {
    deltaEl.textContent = 'нет сравнения';
    deltaEl.className = 'kpi-sub';
  }

  // Карточка 4: Прогноз финала
  document.getElementById('kpi-forecast').textContent = fmt(f.projected_total);
  const fPct = f.projected_pct;
  const fSub = document.getElementById('kpi-forecast-pct');
  fSub.textContent = `${fPct}% от плана`;
  fSub.className = `kpi-sub ${fPct >= 90 ? 'delta-up' : fPct >= 70 ? '' : 'delta-down'}`;

  // Карточка 5: Нужно в день
  const pace = o.pace_needed ?? 0;
  document.getElementById('kpi-pace').textContent = pace > 0 ? fmt(pace) : '—';
  document.getElementById('kpi-pace-sub').textContent = pace > 0
    ? `в день × ${o.days_remaining} дн. = ${fmt(pace * o.days_remaining)}`
    : 'план выполнен или нет данных';

  // ── Forecast scenarios ─────────────────────────────────────────────────
  const forecastRow = document.getElementById('forecastRow');
  if (forecastRow && f.pessimistic) {
    const pPct = f.pessimistic_pct ?? 0;
    const oPct = f.optimistic_pct  ?? 0;
    forecastRow.innerHTML = `
      <div class="forecast-scenario forecast-pess">
        <div class="fs-label">Пессимист</div>
        <div class="fs-value">${fmt(f.pessimistic)}</div>
        <div class="fs-pct">${pPct}%</div>
        <div class="fs-hint">если темп упадёт</div>
      </div>
      <div class="forecast-scenario forecast-real">
        <div class="fs-label">Реалист ★</div>
        <div class="fs-value">${fmt(f.realistic)}</div>
        <div class="fs-pct">${f.projected_pct}%</div>
        <div class="fs-hint">средний темп</div>
      </div>
      <div class="forecast-scenario forecast-opt">
        <div class="fs-label">Оптимист</div>
        <div class="fs-value">${fmt(f.optimistic)}</div>
        <div class="fs-pct">${oPct}%</div>
        <div class="fs-hint">лучшие 3 дня</div>
      </div>
      <div class="forecast-scenario forecast-plan">
        <div class="fs-label">План</div>
        <div class="fs-value">${fmt(o.total_plan)}</div>
        <div class="fs-pct">100%</div>
        <div class="fs-hint">цель</div>
      </div>
    `;
  }

  // ── Insight row ────────────────────────────────────────────────────────
  document.getElementById('kpi-today').textContent     = fmt(o.today_actual);
  document.getElementById('kpi-today-sub').textContent = `из ${fmt(o.today_plan)} план`;

  const confEl = document.getElementById('kpi-confidence');
  confEl.textContent = f.confidence;
  const confClass = { 'высокая': 'high', 'средняя': 'medium', 'низкая': 'low' };
  confEl.className = `kpi-value kpi-value--sm badge ${confClass[f.confidence] || ''}`;

  document.getElementById('kpi-days-total').textContent = `из ${o.days_total} дней`;

  // Best / lagging channels
  const best = dashState.best_channels || [];
  const lag  = dashState.lag_channels  || [];
  document.getElementById('best-channels-list').innerHTML = best.length
    ? best.map(c => `
        <div class="insight-item">
          <span class="insight-ch">${c.name}</span>
          <span class="ch-pct pct-great">${c.pct}%</span>
        </div>`).join('')
    : '<div class="insight-empty">Нет данных</div>';
  document.getElementById('lag-channels-list').innerHTML = lag.length
    ? lag.map(c => `
        <div class="insight-item">
          <span class="insight-ch">${c.name}</span>
          <span class="ch-pct pct-danger">${c.pct}%</span>
        </div>`).join('')
    : '<div class="insight-empty">Нет данных</div>';

  // ── Alerts feed ──────────────────────────────────────────────────────────
  renderAlerts(dashState.alerts || []);

  // ── Plan-curve selector ──────────────────────────────────────────────────
  renderPlanCurveSelect(o);

  // ── Charts ─────────────────────────────────────────────────────────────
  renderMainChart(d, f, o);
  renderDonutChart(dashState.channels || []);

  // ── Channels table (dashboard quick view) ──────────────────────────────
  renderChannelTable(document.getElementById('chSearch').value);
}

// ── Alerts feed ──────────────────────────────────────────────────────────────
function renderAlerts(alerts) {
  const feed = document.getElementById('alertsFeed');
  if (!feed) return;
  if (!alerts || !alerts.length) {
    feed.innerHTML = '';
    feed.style.display = 'none';
    return;
  }
  feed.style.display = '';
  feed.innerHTML = alerts.map(a => `
    <div class="alert-card alert-${a.severity}">
      <span class="alert-icon">${a.icon || ''}</span>
      <div class="alert-body">
        <div class="alert-title">${a.title || ''}</div>
        <div class="alert-text">${a.text || ''}</div>
      </div>
    </div>`).join('');
}

// ── Plan-curve selector ───────────────────────────────────────────────────────
function renderPlanCurveSelect(o) {
  const sel = document.getElementById('planCurveSelect');
  if (!sel) return;

  // Only meaningful for DB launches (curve is stored per launch)
  if (!isDbSource()) {
    sel.disabled = true;
    sel.innerHTML = '<option value="">равномерно</option>';
    return;
  }
  sel.disabled = false;

  const currentId = o.launch_id;
  const curRef    = o.plan_curve_ref ?? '';

  let opts = '<option value="">равномерно</option>';
  (allLaunches || []).forEach(l => {
    if (l.id === currentId) return;                 // can't base on itself
    if (!(l.total_actual > 0)) return;              // need real fact to shape a curve
    const selAttr = String(l.id) === String(curRef) ? ' selected' : '';
    opts += `<option value="${l.id}"${selAttr}>${l.name}</option>`;
  });
  sel.innerHTML = opts;
}

document.getElementById('planCurveSelect')?.addEventListener('change', async e => {
  const launchId = dashState?.overview?.launch_id;
  if (!launchId) return;
  const refVal = e.target.value ? parseInt(e.target.value, 10) : null;
  e.target.disabled = true;
  try {
    await fetch(`/api/launches/${launchId}/plan-curve`, {
      method: 'PUT',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ ref_launch_id: refVal }),
    });
    await loadDashboard();
    if (document.querySelector('.nav-item[data-tab="channels"]')?.classList.contains('active')) {
      renderChannelsTab();
    }
  } catch (err) {
    console.error('Ошибка смены базы плана:', err);
  } finally {
    e.target.disabled = false;
  }
});

// ── Main Chart ─────────────────────────────────────────────────────────────
function renderMainChart(d, f, o) {
  const labels = d.dates.map(s => {
    const dt = new Date(s + 'T00:00:00');
    return dt.toLocaleDateString('ru-RU', { day: 'numeric', month: 'short' });
  });

  const ctx = document.getElementById('mainChart').getContext('2d');
  if (charts.main) charts.main.destroy();

  charts.main = new Chart(ctx, {
    data: {
      labels,
      datasets: [
        {
          type: 'bar', label: 'Факт (ежедн.)',
          data: d.daily_actual, backgroundColor: '#7C3AED',
          borderRadius: 6, order: 3,
        },
        {
          type: 'bar', label: 'План (ежедн.)',
          data: d.daily_plan, backgroundColor: '#C4B5FD',
          borderRadius: 6, order: 4,
        },
        {
          type: 'line', label: 'Факт (накопл.)',
          data: d.cumulative_actual,
          borderColor: '#EC4899', backgroundColor: 'rgba(236,72,153,0.07)',
          fill: true, tension: 0.4, pointRadius: 4, borderWidth: 2.5,
          order: 1, yAxisID: 'y2',
        },
        {
          type: 'line', label: 'Прогноз (накопл.)',
          data: f.cumulative_forecast,
          borderColor: '#F97316', borderDash: [5, 4],
          fill: false, tension: 0.4, pointRadius: 3, borderWidth: 2,
          order: 2, yAxisID: 'y2',
        },
        {
          type: 'line', label: 'План (накопл.)',
          data: f.cumulative_plan,
          borderColor: '#A78BFA', borderDash: [3, 3],
          fill: false, tension: 0.3, pointRadius: 0, borderWidth: 1.5,
          order: 2, yAxisID: 'y2',
        },
      ],
    },
    options: {
      responsive: true,
      interaction: { mode: 'index', intersect: false },
      plugins: {
        legend: { display: false },
        tooltip: { callbacks: { label: c => ` ${c.dataset.label}: ${fmt(c.raw)}` } },
      },
      scales: {
        x: { grid: { display: false }, ticks: { color: '#9CA3AF' } },
        y: {
          position: 'left', grid: { color: '#F3F4F6' },
          ticks: { color: '#9CA3AF', callback: v => fmt(v) },
          title: { display: true, text: 'Ежедн.', color: '#9CA3AF', font: { size: 11 } },
        },
        y2: {
          position: 'right', grid: { display: false },
          ticks: { color: '#9CA3AF', callback: v => fmt(v) },
          title: { display: true, text: 'Накопл.', color: '#9CA3AF', font: { size: 11 } },
        },
      },
    },
  });
}

// ── Donut Chart ────────────────────────────────────────────────────────────
function renderDonutChart(channels) {
  const top = channels
    .filter(c => c.actual > 0)
    .sort((a, b) => b.actual - a.actual)
    .slice(0, 8);

  const canvasEl = document.getElementById('donutChart');
  const legendEl = document.getElementById('donutLegend');
  if (!canvasEl) return;

  if (!top.length) {
    if (legendEl) legendEl.innerHTML = '<div class="insight-empty">Нет данных</div>';
    return;
  }

  const ctx = canvasEl.getContext('2d');
  if (charts.donut) charts.donut.destroy();

  charts.donut = new Chart(ctx, {
    type: 'doughnut',
    data: {
      labels: top.map(c => c.name),
      datasets: [{
        data: top.map(c => c.actual),
        backgroundColor: PALETTE.slice(0, top.length),
        borderWidth: 2,
        borderColor: '#fff',
        hoverOffset: 6,
      }],
    },
    options: {
      responsive: true,
      cutout: '62%',
      plugins: {
        legend: { display: false },
        tooltip: {
          callbacks: {
            label: c => ` ${c.label}: ${fmt(c.raw)} рег.`,
          },
        },
      },
    },
  });

  if (legendEl) {
    legendEl.innerHTML = top.map((c, i) => `
      <div class="donut-legend-item">
        <span class="donut-legend-dot" style="background:${PALETTE[i]}"></span>
        <span class="donut-legend-name">${c.name}</span>
        <span class="donut-legend-val">${fmt(c.actual)}</span>
      </div>`).join('');
  }
}

// ── Channel Table (Dashboard) ──────────────────────────────────────────────
function renderChannelTable(filter = '') {
  const q        = filter.toLowerCase().trim();
  const channels = (dashState?.channels || []).filter(c => !q || c.name.toLowerCase().includes(q));
  const tbody    = document.getElementById('channelsBody');
  if (!channels.length) {
    tbody.innerHTML = '<tr><td colspan="6" class="loading-cell">Каналы не найдены</td></tr>';
    return;
  }
  tbody.innerHTML = channels.map(c => {
    const fillW = clamp(c.pct, 0, 100);
    const cls   = pctClass(c.pct);
    return `
      <tr>
        <td><span class="ch-name ch-name-link" onclick="openChannelHistory(${JSON.stringify(c.name).replace(/"/g, '&quot;')})">${c.name}</span></td>
        <td><span class="ch-resp">${c.responsible || '—'}</span></td>
        <td class="num">${fmt(c.plan)}</td>
        <td class="num">${fmt(c.actual)}</td>
        <td><span class="ch-pct ${cls}">${c.pct}%</span></td>
        <td>
          <div class="progress-mini">
            <div class="progress-mini-bar">
              <div class="progress-mini-fill" style="width:${fillW}%"></div>
            </div>
          </div>
        </td>
      </tr>`;
  }).join('');
}

document.getElementById('chSearch').addEventListener('input', e => {
  if (dashState) renderChannelTable(e.target.value);
});

// ── Channels Tab ───────────────────────────────────────────────────────────
async function renderChannelsTab() {
  const data = dashState;
  if (!data) return;

  const channels  = (data.channels || []).filter(c => c.plan > 0 || c.actual > 0);
  const daysTotal = data.overview?.days_total || 7;
  const launchId  = data.overview?.launch_id || activeLaunchId;
  const canEdit   = isDbSource();

  // Build day picker (highlights a column, doesn't hide others)
  const picker = document.getElementById('dayPicker');
  picker.innerHTML = '';
  const allBtn = document.createElement('button');
  allBtn.className = 'day-btn' + (selectedDay === 'all' ? ' active' : '');
  allBtn.dataset.day = 'all';
  allBtn.textContent = 'Все дни';
  picker.appendChild(allBtn);

  for (let i = 0; i < daysTotal; i++) {
    const btn = document.createElement('button');
    btn.className   = 'day-btn' + (selectedDay === String(i) ? ' active' : '');
    btn.dataset.day = String(i);
    const dayDate   = data.daily?.dates?.[i];
    btn.textContent = dayDate
      ? `День ${i + 1} (${fmtDate(dayDate)})`
      : `День ${i + 1}`;
    picker.appendChild(btn);
  }

  picker.querySelectorAll('.day-btn').forEach(btn => {
    btn.addEventListener('click', () => {
      picker.querySelectorAll('.day-btn').forEach(b => b.classList.remove('active'));
      btn.classList.add('active');
      selectedDay = btn.dataset.day;
      // Highlight the column and scroll it into view
      const container = document.getElementById('channelDetails');
      container.querySelectorAll('.ch-grid-day-head').forEach(th => {
        th.classList.remove('day-highlight');
      });
      if (selectedDay !== 'all') {
        const dayIdx = parseInt(selectedDay, 10);
        const th = container.querySelector(`.ch-grid-day-head[data-day="${dayIdx}"]`);
        if (th) {
          th.classList.add('day-highlight');
          th.scrollIntoView({ behavior: 'smooth', block: 'nearest', inline: 'center' });
        }
      }
    });
  });

  // Load comments async, then render
  let comments = [];
  if (launchId) {
    try {
      comments = await fetch(`/api/launches/${launchId}/comments`).then(r => r.ok ? r.json() : []);
    } catch { comments = []; }
  }

  renderChannelGrid(channels, comments, launchId, canEdit);

  // Load unmatched labels section
  loadUnmatchedLabels();
}

function renderChannelGrid(channels, comments, launchId, canEdit) {
  const container = document.getElementById('channelDetails');
  container.innerHTML = '';

  if (!channels.length) {
    container.innerHTML = '<p style="color:var(--text-sub)">Нет данных по каналам</p>';
    return;
  }

  const data      = dashState;
  const daysTotal = data?.overview?.days_total || 0;
  const dates     = data?.daily?.dates || [];

  // Build comments lookup: commentsMap[channelName][dayNum] = text
  const commentsMap = {};
  for (const c of (comments || [])) {
    if (!commentsMap[c.channel_name]) commentsMap[c.channel_name] = {};
    commentsMap[c.channel_name][c.day_num] = c.comment || '';
  }

  // Helper: classify achievement %
  function dciClass(pct, fact) {
    if (fact === 0) return 'dci-zero';
    if (pct >= 100) return 'dci-great';
    if (pct >= 80)  return 'dci-good';
    if (pct >= 50)  return 'dci-warn';
    return 'dci-danger';
  }

  // Number of sticky columns
  const STICKY_COLS = 8;  // name, resp, plan, fact, yesterday, delta, pace, pct
  const totalCols   = STICKY_COLS + daysTotal;

  // Sort channels: behind plan first (ascending pct), then by actual desc
  const sortedChannels = [...channels].sort((a, b) => {
    const aPct = a.plan > 0 ? a.actual / a.plan : (a.actual > 0 ? 99 : -1);
    const bPct = b.plan > 0 ? b.actual / b.plan : (b.actual > 0 ? 99 : -1);
    return aPct - bPct;
  });

  // Build thead
  let dayHeads = '';
  for (let i = 0; i < daysTotal; i++) {
    const dateStr  = dates[i] ? fmtDate(dates[i]) : '';
    const hlClass  = (selectedDay !== 'all' && parseInt(selectedDay, 10) === i) ? ' day-highlight' : '';
    dayHeads += `<th class="ch-grid-day-head${hlClass}" data-day="${i}">День ${i + 1}${dateStr ? `<br><span>${dateStr}</span>` : ''}</th>`;
  }

  // Traffic light for pace
  function paceLight(ratio) {
    if (ratio === 0) return { icon: '⚪', cls: 'pace-none', label: 'нет данных' };
    if (ratio >= 0.9) return { icon: '🟢', cls: 'pace-ok',   label: `${Math.round(ratio * 100)}% темп` };
    if (ratio >= 0.6) return { icon: '🟡', cls: 'pace-warn', label: `${Math.round(ratio * 100)}% темп` };
    return               { icon: '🔴', cls: 'pace-bad',  label: `${Math.round(ratio * 100)}% темп` };
  }

  // Build tbody rows
  let rows = '';
  for (const ch of sortedChannels) {
    const totalPct = ch.plan > 0 ? Math.round((ch.actual / ch.plan) * 100) : (ch.actual > 0 ? 100 : 0);
    const totalCls = pctClass(totalPct);

    // Yesterday + delta
    const yd     = ch.yesterday ?? 0;
    const delta  = ch.yesterday_delta ?? 0;
    const deltaStr = delta === 0 ? '—' : (delta > 0 ? `+${fmt(delta)}` : fmt(delta));
    const deltaCls = delta > 0 ? 'delta-up' : delta < 0 ? 'delta-down' : '';

    // Pace light
    const pl = paceLight(ch.pace_ratio ?? 0);

    // Day cells
    let dayCells = '';
    for (let i = 0; i < daysTotal; i++) {
      const fact = ch.daily_actual?.[i] ?? 0;
      const plan = ch.daily_plan?.[i]   ?? 0;
      const pct  = plan > 0 ? Math.round((fact / plan) * 100) : (fact > 0 ? 100 : 0);
      const cls  = dciClass(pct, fact);
      const editableClass = canEdit ? ' editable-fact' : '';
      dayCells += `
        <td class="ch-grid-day-cell" data-day="${i}" data-channel="${ch.name}">
          <div class="day-cell-inner ${cls}">
            <div class="dci-plan">пл ${fmt(plan)}</div>
            <div class="dci-fact${editableClass}"
                 data-value="${fact}"
                 data-launch="${launchId}"
                 data-channel="${ch.name}"
                 data-day="${i + 1}"
                 data-plan-el="pct-${ch.name}-${i}">${fmt(fact)}</div>
            <div class="dci-pct" id="pct-${ch.name}-${i}">${plan > 0 ? pct + '%' : '—'}</div>
          </div>
        </td>`;
    }

    // Check if channel has any comment
    const chComments = commentsMap[ch.name] || {};
    const hasAnyComment = Object.values(chComments).some(t => t && t.trim());

    rows += `
      <tr class="ch-grid-row" data-channel="${ch.name}">
        <td class="ch-grid-sticky ch-grid-name">
          <span class="ch-name">${ch.name}</span>
          <button class="comment-toggle${hasAnyComment ? ' has-comment' : ''}" data-channel="${ch.name}" title="Комментарии">💬</button>
        </td>
        <td class="ch-grid-sticky ch-grid-resp">${ch.responsible || '—'}</td>
        <td class="ch-grid-sticky ch-grid-num">${fmt(ch.plan)}</td>
        <td class="ch-grid-sticky ch-grid-num ch-fact-cell" data-channel="${ch.name}">${fmt(ch.actual)}</td>
        <td class="ch-grid-sticky ch-grid-num ch-yesterday">${yd > 0 ? fmt(yd) : '—'}</td>
        <td class="ch-grid-sticky ch-grid-num ${deltaCls}">${deltaStr}</td>
        <td class="ch-grid-sticky ch-grid-num">
          <span class="pace-badge ${pl.cls}" title="${pl.label}">${pl.icon}</span>
        </td>
        <td class="ch-grid-sticky ch-grid-num"><span class="ch-pct ${totalCls}">${totalPct}%</span></td>
        ${dayCells}
      </tr>`;

    // Comment row (hidden by default)
    let commentItems = '';
    for (let i = 0; i < daysTotal; i++) {
      const dayNum    = i + 1;
      const dateLabel = dates[i] ? fmtDate(dates[i]) : `День ${dayNum}`;
      const text      = chComments[dayNum] || '';
      commentItems += `
        <div class="comment-day-item">
          <div class="comment-day-label">${dateLabel}</div>
          <textarea class="comment-ta" data-channel="${ch.name}" data-day="${dayNum}" placeholder="Комментарий...">${text}</textarea>
        </div>`;
    }

    rows += `
      <tr class="ch-comment-row hidden" data-channel="${ch.name}">
        <td colspan="${totalCols}" class="ch-comment-cell">
          <div class="ch-comment-inner">
            <div class="comment-day-grid">${commentItems}</div>
          </div>
        </td>
      </tr>`;
  }

  // Assemble full table
  const wrap = document.createElement('div');
  wrap.className = 'ch-grid-wrap';
  wrap.innerHTML = `
    <table class="ch-grid">
      <thead>
        <tr>
          <th class="ch-grid-sticky ch-grid-name">Канал</th>
          <th class="ch-grid-sticky ch-grid-resp">Ответственный</th>
          <th class="ch-grid-sticky ch-grid-num">План</th>
          <th class="ch-grid-sticky ch-grid-num">Факт</th>
          <th class="ch-grid-sticky ch-grid-num">Вчера</th>
          <th class="ch-grid-sticky ch-grid-num">Δ</th>
          <th class="ch-grid-sticky ch-grid-num">Темп</th>
          <th class="ch-grid-sticky ch-grid-num">%</th>
          ${dayHeads}
        </tr>
      </thead>
      <tbody>${rows}</tbody>
    </table>`;
  container.appendChild(wrap);

  // Attach fact-edit handlers
  if (canEdit) {
    wrap.querySelectorAll('.editable-fact').forEach(el => {
      el.addEventListener('click', function(e) {
        handleFactEdit(e.currentTarget);
      });
    });
  }

  // Attach comment toggle handlers
  wrap.querySelectorAll('.comment-toggle').forEach(btn => {
    btn.addEventListener('click', () => {
      const chName  = btn.dataset.channel;
      const commRow = wrap.querySelector(`.ch-comment-row[data-channel="${CSS.escape(chName)}"]`);
      if (commRow) commRow.classList.toggle('hidden');
    });
  });

  // Attach comment textarea blur handlers
  wrap.querySelectorAll('.comment-ta').forEach(ta => {
    const originalVal = ta.value;
    ta.dataset.original = originalVal;
    ta.addEventListener('blur', () => {
      if (ta.value !== ta.dataset.original) {
        saveComment(launchId, ta.dataset.channel, parseInt(ta.dataset.day, 10), ta.value);
        ta.dataset.original = ta.value;
        // Update the toggle button indicator
        const chName  = ta.dataset.channel;
        const toggle  = wrap.querySelector(`.comment-toggle[data-channel="${CSS.escape(chName)}"]`);
        if (toggle) {
          const commRow = wrap.querySelector(`.ch-comment-row[data-channel="${CSS.escape(chName)}"]`);
          const hasText = commRow
            ? [...commRow.querySelectorAll('.comment-ta')].some(t => t.value.trim())
            : false;
          toggle.classList.toggle('has-comment', hasText);
        }
      }
    });
  });
}

async function handleFactEdit(el) {
  if (el.querySelector('input')) return; // already editing

  const launchId = el.dataset.launch;
  const channel  = el.dataset.channel;
  const dayNum   = parseInt(el.dataset.day, 10);
  const oldVal   = parseInt(el.dataset.value, 10) || 0;
  const pctElId  = el.dataset.planEl;

  const input = document.createElement('input');
  input.type  = 'number';
  input.min   = '0';
  input.value = oldVal;
  input.className = 'fact-input';
  el.textContent = '';
  el.appendChild(input);
  input.focus();
  input.select();

  async function save() {
    const newVal = parseInt(input.value, 10);
    if (isNaN(newVal) || newVal === oldVal) {
      el.textContent = fmt(oldVal);
      el.dataset.value = oldVal;
      return;
    }
    el.textContent = '…';
    try {
      const res = await fetch(`/api/launches/${launchId}/facts`, {
        method: 'PUT',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ channel_name: channel, day_num: dayNum, fact: newVal }),
      });
      if (!res.ok) throw new Error();
      el.textContent = fmt(newVal);
      el.dataset.value = newVal;
      // Update pct display in the same cell
      const cell    = el.closest('.day-cell-inner');
      const planEl  = cell?.querySelector('.dci-plan');
      if (planEl && pctElId) {
        const planText = planEl.textContent.replace('пл ', '').replace(/\s/g, '').replace(/ /g, '');
        const planVal  = parseInt(planText.replace(/[^\d]/g, ''), 10) || 0;
        const newPct   = planVal > 0 ? Math.round((newVal / planVal) * 100) : (newVal > 0 ? 100 : 0);
        const pctEl    = document.getElementById(pctElId);
        if (pctEl) pctEl.textContent = planVal > 0 ? newPct + '%' : '—';
      }
      // Refresh totals
      await loadDashboard();
      // Re-render channels tab if still open
      if (document.querySelector('.nav-item[data-tab="channels"]')?.classList.contains('active')) {
        renderChannelsTab();
      }
    } catch {
      el.textContent = fmt(oldVal);
      el.dataset.value = oldVal;
    }
  }

  input.addEventListener('blur', save);
  input.addEventListener('keydown', ev => {
    if (ev.key === 'Enter') input.blur();
    if (ev.key === 'Escape') { input.value = oldVal; input.blur(); }
  });
}

async function saveComment(launchId, channelName, dayNum, commentText) {
  if (!launchId) return;
  try {
    await fetch(`/api/launches/${launchId}/comments`, {
      method: 'PUT',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ channel_name: channelName, day_num: dayNum, comment: commentText }),
    });
  } catch (err) {
    console.error('Ошибка сохранения комментария:', err);
  }
}

// ── Launches Tab ───────────────────────────────────────────────────────────
async function loadLaunches() {
  const grid = document.getElementById('launchesGrid');
  grid.innerHTML = '<p style="color:var(--text-sub);padding:20px">Загрузка...</p>';
  try {
    const launches = await fetch('/api/launches').then(r => r.json());
    allLaunches = launches;
    grid.innerHTML = '';
    if (!launches.length) {
      grid.innerHTML = '<p style="color:var(--text-sub)">Запусков пока нет</p>';
      return;
    }
    launches.forEach(l => {
      const pct = l.completion_pct || 0;
      const cls = pctClass(pct);
      const card = document.createElement('div');
      card.className = `launch-card${l.is_active ? ' is-active' : ''}`;
      card.innerHTML = `
        <div class="launch-card-name">${l.name}${l.is_active ? ' <span class="badge active">Активный</span>' : ''}</div>
        <div class="launch-card-dates">${fmtDate(l.reg_start)} — ${fmtDate(l.reg_end)}</div>
        <div class="launch-card-stats">
          <div>
            <div class="launch-card-total">${fmt(l.total_actual)}</div>
            <div class="launch-card-total-sub">из ${l.total_plan > 0 ? fmt(l.total_plan) : '—'} план</div>
          </div>
          <div class="ch-pct ${cls}">${pct}%</div>
        </div>
        <div class="launch-card-bar">
          <div class="launch-card-bar-fill" style="width:${clamp(pct, 0, 100)}%"></div>
        </div>
      `;
      card.addEventListener('click', () => showLaunchDetail(l.id));
      grid.appendChild(card);
    });
  } catch (err) {
    grid.innerHTML = `<p style="color:var(--danger)">Ошибка: ${err.message}</p>`;
  }
}

let detailChart = null;

async function showLaunchDetail(id) {
  document.getElementById('launchesListView').classList.add('hidden');
  const detailView = document.getElementById('launchDetailView');
  detailView.classList.remove('hidden');
  document.getElementById('detail-launch-name').textContent = 'Загрузка...';

  try {
    const data = await fetch('/api/launches/' + id).then(r => r.json());
    const o    = data.overview;
    document.getElementById('detail-launch-name').textContent = o.name;

    const kpiGrid = document.getElementById('detail-kpi-grid');
    const pct     = o.completion_pct;
    kpiGrid.innerHTML = `
      <div class="kpi-card">
        <div class="kpi-label">Факт</div>
        <div class="kpi-value">${fmt(o.total_actual)}</div>
        <div class="kpi-sub">из ${o.total_plan > 0 ? fmt(o.total_plan) : '—'} план</div>
      </div>
      <div class="kpi-card">
        <div class="kpi-label">Выполнение</div>
        <div class="kpi-value">${o.total_plan > 0 ? pct + '%' : '—'}</div>
      </div>
      <div class="kpi-card">
        <div class="kpi-label">Период регистраций</div>
        <div class="kpi-value kpi-value--sm">${fmtDate(o.reg_start)} — ${fmtDate(o.reg_end)}</div>
      </div>
      <div class="kpi-card">
        <div class="kpi-label">Дата мероприятия</div>
        <div class="kpi-value kpi-value--sm">${fmtDate(o.event_date) || '—'}</div>
      </div>
    `;

    // Chart
    const labels = data.daily_total.map((_, i) => `День ${i + 1}`);
    const ctx    = document.getElementById('detailChart').getContext('2d');
    if (detailChart) detailChart.destroy();
    detailChart = new Chart(ctx, {
      type: 'bar',
      data: {
        labels,
        datasets: [{
          label: 'Регистрации',
          data: data.daily_total,
          backgroundColor: '#7C3AED',
          borderRadius: 6,
        }],
      },
      options: {
        responsive: true,
        plugins: {
          legend: { display: false },
          tooltip: { callbacks: { label: c => ` ${fmt(c.raw)} рег.` } },
        },
        scales: {
          x: { grid: { display: false }, ticks: { color: '#9CA3AF' } },
          y: { grid: { color: '#F3F4F6' }, ticks: { color: '#9CA3AF', callback: v => fmt(v) } },
        },
      },
    });

    // Channels table
    const tbody    = document.getElementById('detailChannelsBody');
    const channels = data.channels || [];
    if (!channels.length) {
      tbody.innerHTML = '<tr><td colspan="6" class="loading-cell">Нет данных по каналам</td></tr>';
    } else {
      tbody.innerHTML = channels
        .filter(c => c.total_actual > 0 || c.plan > 0)
        .sort((a, b) => b.total_actual - a.total_actual)
        .map(ch => {
          const chPct  = ch.plan > 0 ? Math.round(ch.total_actual / ch.plan * 100) : 0;
          const chCls  = pctClass(chPct);
          const pills  = ch.daily
            .map((cnt, i) => cnt > 0
              ? `<span class="day-pill">Д${i + 1}: ${fmt(cnt)}</span>`
              : '')
            .join('');
          return `
            <tr>
              <td><span class="ch-name">${ch.name}</span></td>
              <td><span class="ch-resp">${ch.responsible || '—'}</span></td>
              <td class="num">${ch.plan > 0 ? fmt(ch.plan) : '—'}</td>
              <td class="num">${fmt(ch.total_actual)}</td>
              <td><span class="ch-pct ${chCls}">${ch.plan > 0 ? chPct + '%' : '—'}</span></td>
              <td><div class="day-pills">${pills || '—'}</div></td>
            </tr>`;
        }).join('');
    }
  } catch (err) {
    document.getElementById('detail-launch-name').textContent = 'Ошибка загрузки';
    console.error(err);
  }
}

document.getElementById('backToLaunches').addEventListener('click', () => {
  document.getElementById('launchDetailView').classList.add('hidden');
  document.getElementById('launchesListView').classList.remove('hidden');
});

// ── Modal: Channel List ────────────────────────────────────────────────────
const KNOWN_CHANNELS = [
  "Email", "ТГ Боты Димы", "Тг-бот с выдачей ЛМ (НейроБаза) (рассылка)",
  "Рефка", "ТГ канал прошлые мероприятия", "ТГ Канал НБ", "ОП",
  "Инстаграм Димы", "ТГ Каналы Лайка + Платформа", "ТГ Канал Димы",
  "Геткурс", "ТГ Боты Лайк", "ВК (посты+рассылки)", "Боты ИИ",
  "Студенты", "Кураторы", "Бот Саши О", "Екатерина Суханова ТГ",
  "ТГ-посевы (Дмитрий)", "Ватсап (Бондарь и все остальное)",
  "МАХ Дима", "ВК Дима", "Суханова ВК", "МАХ Суханова на ВК",
  "ВК сообщество Суханова", "ВК канал Суханова", "Продуктовые каналы УБ",
  "Выступления", "Ютуб", "без метки", "Прочее",
];

let modalChannels = [...KNOWN_CHANNELS]; // mutable for custom additions

function makeChannelRow(ch) {
  return `
    <div class="channel-row" data-channel="${ch}">
      <label class="channel-check">
        <input type="checkbox" value="${ch}"> <span>${ch}</span>
      </label>
      <input type="number" class="channel-plan" placeholder="план" min="0" style="display:none">
      <input type="text"   class="channel-resp" placeholder="ответственный" style="display:none">
    </div>`;
}

function populateChannelsList() {
  const list = document.getElementById('channelsList');
  list.innerHTML = modalChannels.map(makeChannelRow).join('');
  attachChannelRowListeners(list);
}

function attachChannelRowListeners(container) {
  container.querySelectorAll('input[type=checkbox]').forEach(cb => {
    cb.addEventListener('change', e => {
      const row = e.target.closest('.channel-row');
      row.querySelector('.channel-plan').style.display = e.target.checked ? 'inline-block' : 'none';
      row.querySelector('.channel-resp').style.display = e.target.checked ? 'inline-block' : 'none';
    });
  });
}

// Add custom channel
document.getElementById('addChannelBtn').addEventListener('click', () => {
  const input = document.getElementById('newChannelName');
  const name  = input.value.trim();
  if (!name) return;
  if (modalChannels.includes(name)) {
    // Just check it if it exists
    const cb = document.querySelector(`#channelsList input[value="${name}"]`);
    if (cb) { cb.checked = true; cb.dispatchEvent(new Event('change')); }
    input.value = '';
    return;
  }
  modalChannels.push(name);
  const list = document.getElementById('channelsList');
  const tmp  = document.createElement('div');
  tmp.innerHTML = makeChannelRow(name);
  const row = tmp.firstElementChild;
  list.appendChild(row);
  attachChannelRowListeners(row.parentElement);
  // Auto-check the new channel
  const cb = row.querySelector('input[type=checkbox]');
  cb.checked = true;
  cb.dispatchEvent(new Event('change'));
  input.value = '';
});

document.getElementById('newChannelName').addEventListener('keydown', e => {
  if (e.key === 'Enter') {
    e.preventDefault();
    document.getElementById('addChannelBtn').click();
  }
});

// ── Modal open/close ───────────────────────────────────────────────────────
function openModal() {
  document.getElementById('modalOverlay').classList.remove('hidden');
}
function closeModal() {
  document.getElementById('modalOverlay').classList.add('hidden');
  document.getElementById('newLaunchForm').reset();
  modalChannels = [...KNOWN_CHANNELS];
  populateChannelsList();
}

document.getElementById('newLaunchBtn').addEventListener('click', openModal);
document.getElementById('closeModal').addEventListener('click', closeModal);
document.getElementById('cancelModal').addEventListener('click', closeModal);
document.getElementById('modalOverlay').addEventListener('click', e => {
  if (e.target === document.getElementById('modalOverlay')) closeModal();
});

// ── Form submit ────────────────────────────────────────────────────────────
document.getElementById('newLaunchForm').addEventListener('submit', async e => {
  e.preventDefault();
  const fd       = new FormData(e.target);
  const channels = [];
  document.querySelectorAll('#channelsList input[type=checkbox]:checked').forEach(cb => {
    const row     = cb.closest('.channel-row');
    const planEl  = row.querySelector('.channel-plan');
    const respEl  = row.querySelector('.channel-resp');
    channels.push({
      name:        cb.value,
      plan:        parseInt(planEl.value) || 0,
      responsible: respEl.value.trim(),
    });
  });
  const body = {
    name:           fd.get('name'),
    reg_start:      fd.get('reg_start'),
    reg_end:        fd.get('reg_end'),
    event_date:     fd.get('event_date') || null,
    event_end_date: fd.get('event_end_date') || null,
    total_plan:     parseInt(fd.get('total_plan')) || 0,
    channels,
  };
  const submitBtn = e.target.querySelector('[type=submit]');
  submitBtn.disabled = true;
  submitBtn.textContent = 'Создаём...';
  try {
    const resp = await fetch('/api/launches', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify(body),
    });
    if (!resp.ok) throw new Error(`HTTP ${resp.status}`);
    const created = await resp.json();
    closeModal();
    // Switch to the new launch
    activeLaunchId = created.id;
    selectedDay    = 'all';
    await loadLaunchSelector();
    document.querySelector('.nav-item[data-tab="dashboard"]').click();
    await loadDashboard();
  } catch (err) {
    alert('Ошибка создания запуска: ' + err.message);
  } finally {
    submitBtn.disabled = false;
    submitBtn.textContent = 'Создать запуск';
  }
});

// ── Refresh button ─────────────────────────────────────────────────────────
document.getElementById('refreshBtn').addEventListener('click', () => loadDashboard(true));

// ── Reimport button ────────────────────────────────────────────────────────
document.getElementById('reimportBtn').addEventListener('click', async () => {
  const btn = document.getElementById('reimportBtn');
  const badge = document.getElementById('importBadge');
  btn.textContent = '⬇ Импорт...';
  btn.disabled = true;
  badge.textContent = '';
  try {
    const launchId = activeLaunchId;
    const url = launchId
      ? `/api/launches/${launchId}/reimport`
      : '/api/reimport';
    const res = await fetch(url, { method: 'POST' });
    if (!res.ok) throw new Error(`HTTP ${res.status}`);
    const data = await res.json();
    const total = data.total_registrations ?? '?';
    badge.textContent = `↓ ${fmt(total)} рег.`;
    // Reload dashboard with fresh DB data
    if (launchId === null) {
      // for live mode just refresh
      await loadDashboard(true);
    } else {
      await loadDashboard();
    }
  } catch (err) {
    badge.textContent = 'Ошибка импорта';
    badge.style.color = 'var(--danger)';
    console.error('Reimport error:', err);
  } finally {
    btn.textContent = '⬇ Импорт';
    btn.disabled = false;
  }
});

// ── Unmatched Labels ───────────────────────────────────────────────────────
async function loadUnmatchedLabels() {
  const launchId = activeLaunchId || dashState?.overview?.launch_id;
  const card     = document.getElementById('unmatchedCard');
  const tbody    = document.getElementById('unmatchedBody');
  const status   = document.getElementById('saveMappingsStatus');
  if (!launchId || !card || !tbody) return;

  try {
    const [labels, mappings] = await Promise.all([
      fetch(`/api/launches/${launchId}/unmatched`).then(r => r.json()),
      fetch('/api/label-mappings').then(r => r.json()),
    ]);

    if (!labels.length) {
      card.style.display = 'none';
      return;
    }

    // Index of already-saved mappings
    const existingMap = {};
    for (const m of mappings) {
      existingMap[`${m.utm_source}|${m.utm_medium}`] = m.channel_name;
    }

    // Channel options from current dashState
    const chNames = (dashState?.channels || []).map(c => c.name).filter(Boolean);

    card.style.display = '';
    if (status) status.textContent = '';

    tbody.innerHTML = labels.map(l => {
      const key      = `${l.utm_source}|${l.utm_medium}`;
      const existing = existingMap[key] || '';
      const opts     = ['', ...chNames].map(ch =>
        `<option value="${ch}"${ch === existing ? ' selected' : ''}>${ch || '— не назначено —'}</option>`
      ).join('');
      const savedBadge = existing
        ? `<span class="badge active" title="${existing}">сохранено</span>`
        : '';
      return `
        <tr class="${existing ? 'label-row-saved' : ''}">
          <td><code class="label-code">${l.utm_source || '<em>пусто</em>'}</code></td>
          <td><code class="label-code">${l.utm_medium || '<em>пусто</em>'}</code></td>
          <td class="num">${fmt(l.count)}</td>
          <td><select class="label-ch-select" data-src="${l.utm_source}" data-med="${l.utm_medium}">${opts}</select></td>
          <td style="text-align:center">${savedBadge}</td>
        </tr>`;
    }).join('');

  } catch (err) {
    console.error('Ошибка загрузки нераспределённых меток:', err);
    if (card) card.style.display = 'none';
  }
}

document.getElementById('saveAllMappingsBtn')?.addEventListener('click', async () => {
  const btn     = document.getElementById('saveAllMappingsBtn');
  const status  = document.getElementById('saveMappingsStatus');
  const selects = document.querySelectorAll('#unmatchedBody .label-ch-select');

  btn.disabled    = true;
  status.textContent = 'Сохраняю маппинги...';

  let saved = 0, errors = 0;
  for (const sel of selects) {
    const ch = sel.value;
    if (!ch) continue;
    try {
      const res = await fetch('/api/label-mappings', {
        method: 'PUT',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ utm_source: sel.dataset.src, utm_medium: sel.dataset.med, channel_name: ch }),
      });
      res.ok ? saved++ : errors++;
    } catch { errors++; }
  }

  if (!saved && !errors) {
    status.textContent = 'Нет новых маппингов для сохранения';
    btn.disabled = false;
    return;
  }

  if (errors > 0) {
    status.textContent = `⚠ Сохранено: ${saved}, ошибок: ${errors}`;
    btn.disabled = false;
    return;
  }

  // Success — run reimport
  status.textContent = `✅ Сохранено ${saved} маппингов. Переимпортирую...`;
  const launchId = activeLaunchId || dashState?.overview?.launch_id;
  if (launchId) {
    try {
      const r = await fetch(`/api/launches/${launchId}/reimport`, { method: 'POST' });
      const d = await r.json();
      await loadDashboard();
      await loadUnmatchedLabels();
      const left = document.querySelectorAll('#unmatchedBody tr').length;
      status.textContent = `✅ Готово! ${fmt(d.total_registrations)} рег. импортировано${left ? `, ещё ${left} меток не распределено` : ', все метки распределены 🎉'}`;
    } catch {
      status.textContent = `✅ Маппинги сохранены. Нажмите ⬇ Импорт для обновления данных.`;
    }
  }
  btn.disabled = false;
});

// ── UTM Labels Tab ─────────────────────────────────────────────────────────
let utmData = [];        // raw list from API
let utmChanges = {};     // {`src|med`: channelName} pending saves

async function loadUtmTab() {
  const launchId = activeLaunchId || dashState?.overview?.launch_id;
  const tbody    = document.getElementById('utmBody');
  const statsRow = document.getElementById('utmStatsRow');
  if (!launchId || !tbody) return;

  tbody.innerHTML = '<tr><td colspan="7" class="loading-cell">Загрузка...</td></tr>';
  if (statsRow) statsRow.innerHTML = '';

  try {
    const [labels, mappings, channels] = await Promise.all([
      fetch(`/api/launches/${launchId}/utm-labels`).then(r => r.json()),
      fetch('/api/label-mappings').then(r => r.json()),
      fetch(`/api/launches/${launchId}/dashboard`).then(r => r.json()),
    ]);

    utmData = labels;
    utmChanges = {};

    // Build user mapping index: src|med|platform -> channel_name
    const userMap = {};
    for (const m of mappings) userMap[`${m.utm_source}|${m.utm_medium}|${m.platform}`] = m.channel_name;

    // Channel list for dropdowns
    const chNames = (channels.channels || []).map(c => c.name).filter(Boolean);

    // Summary stats
    const total    = labels.reduce((s, l) => s + l.count, 0);
    const matched  = labels.filter(l => l.resolved_channel && l.resolved_channel !== 'без метки').reduce((s, l) => s + l.count, 0);
    const unmatched = total - matched;
    if (statsRow) {
      statsRow.innerHTML = `
        <div class="utm-stat-chip utm-stat-total">Всего меток: <b>${labels.length}</b></div>
        <div class="utm-stat-chip utm-stat-total">Всего регистраций: <b>${fmt(total)}</b></div>
        <div class="utm-stat-chip utm-stat-ok">Распределено: <b>${fmt(matched)}</b> (${total ? Math.round(matched/total*100) : 0}%)</div>
        <div class="utm-stat-chip utm-stat-warn">Нераспределено: <b>${fmt(unmatched)}</b></div>
      `;
    }

    renderUtmTable(labels, userMap, chNames);

    // Wire up search + filter
    const searchEl    = document.getElementById('utmSearch');
    const filterEl    = document.getElementById('utmShowUnmatched');
    if (searchEl)  searchEl.oninput  = () => renderUtmTable(utmData, userMap, chNames);
    if (filterEl)  filterEl.onchange = () => renderUtmTable(utmData, userMap, chNames);

  } catch (err) {
    tbody.innerHTML = `<tr><td colspan="7" class="loading-cell" style="color:var(--danger)">Ошибка загрузки: ${err.message}</td></tr>`;
  }
}

function renderUtmTable(labels, userMap, chNames) {
  const tbody      = document.getElementById('utmBody');
  const q          = (document.getElementById('utmSearch')?.value || '').toLowerCase().trim();
  const onlyUnmatch = document.getElementById('utmShowUnmatched')?.checked;
  const total      = labels.reduce((s, l) => s + l.count, 0);

  let rows = labels.filter(l => {
    if (onlyUnmatch && l.resolved_channel && l.resolved_channel !== 'без метки') return false;
    if (!q) return true;
    return (l.utm_source + l.utm_medium + l.platform + l.resolved_channel).toLowerCase().includes(q);
  });

  if (!rows.length) {
    tbody.innerHTML = '<tr><td colspan="8" class="loading-cell">Ничего не найдено</td></tr>';
    return;
  }

  const pct = n => total > 0 ? (n / total * 100).toFixed(1) : '0';

  tbody.innerHTML = rows.map(l => {
    const key         = `${l.utm_source}|${l.utm_medium}|${l.platform}`;
    const override    = utmChanges[key] ?? userMap[key] ?? '';
    const currentCh   = l.resolved_channel || 'без метки';
    const isUnmatched = !l.resolved_channel || l.resolved_channel === 'без метки';
    const hasOverride = !!userMap[key];

    const opts = ['', ...chNames].map(ch =>
      `<option value="${ch}"${ch === override ? ' selected' : ''}>${ch || '— не назначено —'}</option>`
    ).join('');

    const chBadge = isUnmatched
      ? `<span class="utm-ch-badge utm-ch-none">без метки</span>`
      : `<span class="utm-ch-badge utm-ch-ok">${currentCh}</span>`;

    const savedBadge = hasOverride
      ? `<span class="badge active" title="${userMap[key]}">✓ сохранено</span>`
      : '';

    const platCls = l.platform === 'MAX' ? 'utm-plat-max' : l.platform === 'ТГ' ? 'utm-plat-tg' : 'utm-plat-unknown';
    const platBadge = l.platform
      ? `<span class="utm-platform-badge ${platCls}">${l.platform}</span>`
      : `<span class="utm-platform-badge utm-plat-unknown">—</span>`;

    return `
      <tr class="${isUnmatched ? 'utm-row-unmatched' : ''}" data-key="${key}">
        <td><code class="label-code">${l.utm_source || '<i>(пусто)</i>'}</code></td>
        <td><code class="label-code">${l.utm_medium || '<i>(пусто)</i>'}</code></td>
        <td>${platBadge}</td>
        <td class="num">${fmt(l.count)}</td>
        <td class="num" style="color:var(--text-sub)">${pct(l.count)}%</td>
        <td>${chBadge}</td>
        <td>
          <select class="label-ch-select utm-ch-select"
                  data-src="${l.utm_source}" data-med="${l.utm_medium}" data-platform="${l.platform}">
            ${opts}
          </select>
        </td>
        <td style="text-align:center">${savedBadge}</td>
      </tr>`;
  }).join('');

  // Track changes in dropdowns
  tbody.querySelectorAll('.utm-ch-select').forEach(sel => {
    sel.addEventListener('change', () => {
      const key = `${sel.dataset.src}|${sel.dataset.med}|${sel.dataset.platform}`;
      if (sel.value) utmChanges[key] = sel.value;
      else delete utmChanges[key];
      sel.closest('tr')?.classList.toggle('utm-row-changed', !!sel.value);
    });
  });
}

document.getElementById('utmSaveBtn')?.addEventListener('click', async () => {
  const btn    = document.getElementById('utmSaveBtn');
  const status = document.getElementById('utmSaveStatus');
  const pending = Object.entries(utmChanges);

  if (!pending.length) {
    status.textContent = 'Нет изменений для сохранения';
    return;
  }

  btn.disabled = true;
  status.textContent = `Сохраняю ${pending.length} маппингов...`;

  let saved = 0, errors = 0;
  for (const [key, ch] of pending) {
    const [src, med, platform] = key.split('|');
    try {
      const res = await fetch('/api/label-mappings', {
        method: 'PUT',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ utm_source: src, utm_medium: med, platform: platform || '', channel_name: ch }),
      });
      res.ok ? saved++ : errors++;
    } catch { errors++; }
  }

  if (errors) {
    status.textContent = `⚠ Сохранено: ${saved}, ошибок: ${errors}`;
    btn.disabled = false;
    return;
  }

  // Reimport
  status.textContent = `✅ Сохранено ${saved}. Переимпортирую...`;
  const launchId = activeLaunchId || dashState?.overview?.launch_id;
  try {
    const r = await fetch(`/api/launches/${launchId}/reimport`, { method: 'POST' });
    const d = await r.json();
    await loadDashboard();
    await loadUtmTab();
    const stillUnmatched = utmData.filter(l => !l.resolved_channel || l.resolved_channel === 'без метки').length;
    status.textContent = `✅ Готово! ${fmt(d.total_registrations)} рег. • осталось нераспределённых меток: ${stillUnmatched}`;
  } catch {
    status.textContent = `✅ Маппинги сохранены. Нажмите ⬇ Импорт для обновления.`;
  }
  btn.disabled = false;
});

document.getElementById('utmRefreshBtn')?.addEventListener('click', () => loadUtmTab());

// ── Compare Tab ────────────────────────────────────────────────────────────
let compareChart = null;

async function initCompareTab() {
  // Populate reference selector
  const sel = document.getElementById('compareRefSelect');
  if (!sel) return;
  const currentId = activeLaunchId || dashState?.overview?.launch_id;

  if (sel.options.length <= 1) {
    const launches = await fetch('/api/launches').then(r => r.json()).catch(() => []);
    sel.innerHTML = '<option value="">Выбери запуск...</option>';
    launches.forEach(l => {
      if (l.id === currentId) return;  // skip current
      const opt = document.createElement('option');
      opt.value = l.id;
      opt.textContent = `${l.name} (${fmtDate(l.reg_start)} — ${fmtDate(l.reg_end)}, ${fmt(l.total_actual)} рег.)`;
      sel.appendChild(opt);
    });
  }

  document.getElementById('compareEmpty').style.display = '';
  document.getElementById('compareResult').style.display = 'none';

  renderPaceBenchmark(currentId);
}

let paceChart = null;
async function renderPaceBenchmark(launchId) {
  const card = document.getElementById('paceCard');
  if (!card || !launchId) { if (card) card.style.display = 'none'; return; }

  let data;
  try {
    const res = await fetch(`/api/launches/${launchId}/pace`);
    if (!res.ok) throw new Error('no data');
    data = await res.json();
  } catch (e) {
    card.style.display = 'none';
    return;
  }
  card.style.display = '';

  // вердикт
  const vEl = document.getElementById('paceVerdict');
  const v = data.verdict;
  if (v) {
    const map = {
      ahead:   { txt: `Впереди темпа на +${v.delta}пп`, cls: 'pace-ahead' },
      behind:  { txt: `Отстаём от темпа на ${v.delta}пп`, cls: 'pace-behind' },
      ontrack: { txt: `В рамках темпа (${v.delta >= 0 ? '+' : ''}${v.delta}пп)`, cls: 'pace-ontrack' },
    };
    const m = map[v.status] || map.ontrack;
    vEl.textContent = m.txt;
    vEl.className = `pace-verdict ${m.cls}`;
    document.getElementById('paceSub').textContent =
      `День ${v.day}: набрано ${v.target_pct}% плана · среднеисторически к этому дню — ${v.bench_pct}% (по ${data.ref_count} запускам)`;
  } else {
    vEl.textContent = '';
    document.getElementById('paceSub').textContent = `Среднее по ${data.ref_count} запускам`;
  }

  // график
  if (paceChart) { paceChart.destroy(); paceChart = null; }
  const ctx = document.getElementById('paceChart');
  if (!ctx) return;
  const labels = data.days.map(d => `Д${d}`);
  paceChart = new Chart(ctx, {
    type: 'line',
    data: {
      labels,
      datasets: [
        { label: 'Лучший (история)', data: data.best_curve, borderColor: 'rgba(16,185,129,0.35)',
          backgroundColor: 'rgba(16,185,129,0.08)', fill: '+1', pointRadius: 0, borderWidth: 1, borderDash: [4,4] },
        { label: 'Худший (история)', data: data.worst_curve, borderColor: 'rgba(244,63,94,0.35)',
          backgroundColor: 'transparent', fill: false, pointRadius: 0, borderWidth: 1, borderDash: [4,4] },
        { label: 'Средний темп', data: data.avg_curve, borderColor: '#8a8aa3',
          backgroundColor: 'transparent', fill: false, pointRadius: 0, borderWidth: 2, borderDash: [6,3] },
        { label: data.launch.name, data: data.target_curve, borderColor: '#7C3AED',
          backgroundColor: 'rgba(124,58,237,0.10)', fill: false, pointRadius: 3, borderWidth: 3, tension: 0.25 },
      ]
    },
    options: {
      responsive: true, maintainAspectRatio: false,
      interaction: { mode: 'index', intersect: false },
      plugins: {
        legend: { position: 'bottom', labels: { boxWidth: 12, font: { size: 11 } } },
        tooltip: { callbacks: { label: c => `${c.dataset.label}: ${c.parsed.y}% плана` } }
      },
      scales: {
        y: { beginAtZero: true, ticks: { callback: v => v + '%' }, title: { display: true, text: '% плана накопл.' } }
      }
    }
  });
}

document.getElementById('compareLoadBtn')?.addEventListener('click', async () => {
  const refId = document.getElementById('compareRefSelect')?.value;
  const currentId = activeLaunchId || dashState?.overview?.launch_id;
  if (!refId || !currentId) return;

  const btn = document.getElementById('compareLoadBtn');
  btn.disabled = true;
  btn.textContent = 'Загрузка...';

  try {
    const data = await fetch(`/api/launches/${currentId}/compare/${refId}`).then(r => r.json());

    document.getElementById('compareEmpty').style.display = 'none';
    document.getElementById('compareResult').style.display = '';

    // KPI cards
    const mainTotal = data.main_cumulative.at(-1) || 0;
    const refTotal  = data.ref_cumulative.at(-1)  || 0;
    const diff = mainTotal - refTotal;
    const diffSign = diff >= 0 ? '+' : '';
    document.getElementById('compareKpiGrid').innerHTML = `
      <div class="kpi-card kpi-card--today">
        <div class="kpi-body">
          <div class="kpi-label">${data.launch.name}</div>
          <div class="kpi-value">${fmt(mainTotal)}</div>
          <div class="kpi-sub">рег. итого</div>
        </div>
      </div>
      <div class="kpi-card">
        <div class="kpi-body">
          <div class="kpi-label">${data.reference.name}</div>
          <div class="kpi-value">${fmt(refTotal)}</div>
          <div class="kpi-sub">рег. итого</div>
        </div>
      </div>
      <div class="kpi-card ${diff >= 0 ? 'kpi-card--forecast' : 'kpi-card--pace'}">
        <div class="kpi-body">
          <div class="kpi-label">Разница</div>
          <div class="kpi-value">${diffSign}${fmt(diff)}</div>
          <div class="kpi-sub">${diff >= 0 ? 'опережаем' : 'отстаём'}</div>
        </div>
      </div>
    `;

    // Legend
    document.getElementById('compareLegend').innerHTML = `
      <span><span class="dot" style="background:#7C3AED"></span>${data.launch.name}</span>
      <span><span class="dot" style="background:#EC4899"></span>${data.reference.name}</span>
    `;

    // Chart
    const ctx = document.getElementById('compareChart').getContext('2d');
    if (compareChart) compareChart.destroy();
    compareChart = new Chart(ctx, {
      type: 'line',
      data: {
        labels: data.days.map(d => `День ${d}`),
        datasets: [
          {
            label: data.launch.name,
            data: data.main_cumulative,
            borderColor: '#7C3AED', backgroundColor: 'rgba(124,58,237,0.07)',
            fill: true, tension: 0.4, pointRadius: 4, borderWidth: 2.5,
          },
          {
            label: data.reference.name,
            data: data.ref_cumulative,
            borderColor: '#EC4899', borderDash: [5, 4],
            fill: false, tension: 0.4, pointRadius: 4, borderWidth: 2,
          },
        ],
      },
      options: {
        responsive: true,
        interaction: { mode: 'index', intersect: false },
        plugins: {
          legend: { display: false },
          tooltip: { callbacks: { label: c => ` ${c.dataset.label}: ${fmt(c.raw)} рег.` } },
        },
        scales: {
          x: { grid: { display: false }, ticks: { color: '#9CA3AF' } },
          y: { grid: { color: '#F3F4F6' }, ticks: { color: '#9CA3AF', callback: v => fmt(v) } },
        },
      },
    });

    // Table
    document.getElementById('compareTableHead').innerHTML = `
      <tr>
        <th>День</th>
        <th class="num">${data.launch.name}</th>
        <th class="num">${data.reference.name}</th>
        <th class="num">Разница</th>
        <th class="num">Накопл. сейчас</th>
        <th class="num">Накопл. прошлый</th>
      </tr>`;
    document.getElementById('compareTableBody').innerHTML = data.days.map((d, i) => {
      const m  = data.main_daily[i]      || 0;
      const r  = data.ref_daily[i]       || 0;
      const mc = data.main_cumulative[i] || 0;
      const rc = data.ref_cumulative[i]  || 0;
      const diff = m - r;
      const diffCls = diff > 0 ? 'delta-up' : diff < 0 ? 'delta-down' : '';
      return `
        <tr>
          <td>День ${d}</td>
          <td class="num">${m > 0 ? fmt(m) : '—'}</td>
          <td class="num">${r > 0 ? fmt(r) : '—'}</td>
          <td class="num ${diffCls}">${m > 0 || r > 0 ? (diff >= 0 ? '+' : '') + fmt(diff) : '—'}</td>
          <td class="num">${mc > 0 ? fmt(mc) : '—'}</td>
          <td class="num">${rc > 0 ? fmt(rc) : '—'}</td>
        </tr>`;
    }).join('');

  } catch (err) {
    alert('Ошибка загрузки сравнения: ' + err.message);
  } finally {
    btn.disabled = false;
    btn.textContent = 'Сравнить';
  }
});

// ── Channel drill-down history ─────────────────────────────────────────────
let chHistChannel  = null;
let chHistLaunchId = null;

async function openChannelHistory(name) {
  const overlay = document.getElementById('chHistoryOverlay');
  const body    = document.getElementById('chHistoryBody');
  chHistChannel  = name;
  chHistLaunchId = activeLaunchId || dashState?.overview?.launch_id || null;
  document.getElementById('chHistoryTitle').textContent = name;
  body.innerHTML = '<div class="loading-cell">Загрузка…</div>';
  overlay.classList.remove('hidden');

  let data;
  try {
    const res = await fetch(`/api/channels/${encodeURIComponent(name)}/history`);
    if (!res.ok) throw new Error('not found');
    data = await res.json();
  } catch (e) {
    body.innerHTML = '<div class="loading-cell">Нет данных по этому каналу</div>';
    return;
  }
  renderChannelHistory(data);
  loadChannelTasks();
}

function renderChannelHistory(data) {
  const body = document.getElementById('chHistoryBody');
  const hist = data.history || [];

  // summary cards
  const trendTxt = data.trend
    ? (data.trend.direction === 'up'   ? `↑ растёт (+${data.trend.diff}пп)`
     : data.trend.direction === 'down' ? `↓ падает (${data.trend.diff}пп)`
     : '→ стабильно')
    : '—';
  const cards = `
    <div class="ch-hist-cards">
      <div class="ch-hist-card"><span class="chc-label">Запусков</span><span class="chc-val">${data.total_launches}</span></div>
      <div class="ch-hist-card"><span class="chc-label">Средн. %</span><span class="chc-val ${pctClass(data.avg_pct)}">${data.avg_pct != null ? data.avg_pct + '%' : '—'}</span></div>
      <div class="ch-hist-card"><span class="chc-label">Лучший</span><span class="chc-val">${data.best ? data.best.pct + '%' : '—'}</span><span class="chc-sub">${data.best ? data.best.launch_name : ''}</span></div>
      <div class="ch-hist-card"><span class="chc-label">Худший</span><span class="chc-val">${data.worst ? data.worst.pct + '%' : '—'}</span><span class="chc-sub">${data.worst ? data.worst.launch_name : ''}</span></div>
      <div class="ch-hist-card"><span class="chc-label">Макс. факт</span><span class="chc-val">${data.max_actual ? fmt(data.max_actual.actual) : '—'}</span><span class="chc-sub">${data.max_actual ? data.max_actual.launch_name : ''}</span></div>
      <div class="ch-hist-card"><span class="chc-label">Тренд</span><span class="chc-val">${trendTxt}</span></div>
    </div>`;

  const rows = hist.map(h => {
    const cls = pctClass(h.pct);
    const fillW = clamp(h.pct || 0, 0, 100);
    return `
      <tr class="${h.is_active ? 'ch-hist-active' : ''}">
        <td>${h.launch_name}${h.is_active ? ' <span class="badge-live">live</span>' : ''}</td>
        <td class="num">${fmt(h.plan)}</td>
        <td class="num">${fmt(h.actual)}</td>
        <td><span class="ch-pct ${cls}">${h.pct != null ? h.pct + '%' : '—'}</span></td>
        <td><div class="progress-mini"><div class="progress-mini-bar"><div class="progress-mini-fill" style="width:${fillW}%"></div></div></div></td>
      </tr>`;
  }).join('');

  body.innerHTML = `
    ${cards}
    <div class="ch-hist-chart-wrap"><canvas id="chHistChart"></canvas></div>
    <div class="ch-tasks">
      <div class="ch-tasks-head">
        <h4>📋 Задачи по каналу</h4>
        <span class="ch-tasks-sub" id="chTasksSub"></span>
      </div>
      <div class="ch-tasks-add">
        <input type="text" id="chTaskInput" class="add-channel-input" placeholder="Новая задача (напр. «запостить в 12:00»)…" maxlength="300">
        <button type="button" id="chTaskAddBtn" class="btn-secondary btn-sm">+ Добавить</button>
      </div>
      <div id="chTasksList" class="ch-tasks-list"><div class="loading-cell">Загрузка…</div></div>
    </div>
    <table class="ch-hist-table">
      <thead><tr><th>Запуск</th><th class="num">План</th><th class="num">Факт</th><th>%</th><th>Прогресс</th></tr></thead>
      <tbody>${rows || '<tr><td colspan="5" class="loading-cell">Нет данных</td></tr>'}</tbody>
    </table>`;

  // chart: % выполнения по запускам (хронологически)
  if (charts.chHist) { charts.chHist.destroy(); charts.chHist = null; }
  const ctx = document.getElementById('chHistChart');
  if (ctx && hist.length) {
    charts.chHist = new Chart(ctx, {
      type: 'bar',
      data: {
        labels: hist.map(h => h.launch_name.length > 22 ? h.launch_name.slice(0, 22) + '…' : h.launch_name),
        datasets: [{
          label: '% выполнения плана',
          data: hist.map(h => h.pct),
          backgroundColor: hist.map(h => h.pct >= 100 ? '#10B981' : h.pct >= 60 ? '#3B82F6' : h.pct >= 30 ? '#EAB308' : '#F43F5E'),
          borderRadius: 4,
        }]
      },
      options: {
        responsive: true, maintainAspectRatio: false,
        plugins: {
          legend: { display: false },
          tooltip: { callbacks: { label: c => {
            const h = hist[c.dataIndex];
            return `${h.pct}% · факт ${fmt(h.actual)} / план ${fmt(h.plan)}`;
          } } }
        },
        scales: {
          y: { beginAtZero: true, ticks: { callback: v => v + '%' } },
          x: { ticks: { maxRotation: 60, minRotation: 30, font: { size: 10 } } }
        }
      }
    });
  }
}

// ── Channel tasks (внутри drill-down) ──────────────────────────────────────
function escapeHtml(s) {
  return String(s).replace(/[&<>"']/g, c => ({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[c]));
}

async function loadChannelTasks() {
  const list = document.getElementById('chTasksList');
  const sub  = document.getElementById('chTasksSub');
  const addBtn = document.getElementById('chTaskAddBtn');
  const input  = document.getElementById('chTaskInput');
  if (!list) return;

  if (!chHistLaunchId) {
    list.innerHTML = '<div class="ch-tasks-empty">Задачи доступны при выборе запуска</div>';
    if (input) input.disabled = true;
    if (addBtn) addBtn.disabled = true;
    return;
  }

  // wire add controls (once per render)
  if (addBtn && !addBtn.dataset.wired) {
    addBtn.dataset.wired = '1';
    addBtn.addEventListener('click', addChannelTask);
    input.addEventListener('keydown', e => { if (e.key === 'Enter') addChannelTask(); });
  }

  let tasks = [];
  try {
    tasks = await fetch(`/api/launches/${chHistLaunchId}/channels/${encodeURIComponent(chHistChannel)}/tasks`)
      .then(r => r.ok ? r.json() : []);
  } catch { tasks = []; }
  renderTasksList(tasks);
  const open = tasks.filter(t => !t.done).length;
  if (sub) sub.textContent = tasks.length ? `${open} открыто · ${tasks.length} всего` : '';
}

function renderTasksList(tasks) {
  const list = document.getElementById('chTasksList');
  if (!list) return;
  if (!tasks.length) {
    list.innerHTML = '<div class="ch-tasks-empty">Пока нет задач</div>';
    return;
  }
  list.innerHTML = tasks.map(t => `
    <div class="ch-task ${t.done ? 'ch-task-done' : ''}" data-id="${t.id}">
      <label class="ch-task-check">
        <input type="checkbox" ${t.done ? 'checked' : ''} onchange="toggleChannelTask(${t.id}, this.checked)">
        <span class="ch-task-text">${escapeHtml(t.text)}</span>
      </label>
      <button class="ch-task-del" title="Удалить" onclick="deleteChannelTask(${t.id})">×</button>
    </div>`).join('');
}

async function addChannelTask() {
  const input = document.getElementById('chTaskInput');
  if (!input || !chHistLaunchId) return;
  const text = input.value.trim();
  if (!text) return;
  input.value = '';
  try {
    await fetch(`/api/launches/${chHistLaunchId}/channels/${encodeURIComponent(chHistChannel)}/tasks`, {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ text }),
    });
  } catch {}
  loadChannelTasks();
}

async function toggleChannelTask(id, done) {
  try {
    await fetch(`/api/tasks/${id}`, {
      method: 'PATCH',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ done }),
    });
  } catch {}
  loadChannelTasks();
}

async function deleteChannelTask(id) {
  try {
    await fetch(`/api/tasks/${id}`, { method: 'DELETE' });
  } catch {}
  loadChannelTasks();
}
window.toggleChannelTask = toggleChannelTask;
window.deleteChannelTask = deleteChannelTask;

function closeChHistory() {
  document.getElementById('chHistoryOverlay').classList.add('hidden');
  if (charts.chHist) { charts.chHist.destroy(); charts.chHist = null; }
}
document.getElementById('closeChHistory').addEventListener('click', closeChHistory);
document.getElementById('chHistoryOverlay').addEventListener('click', e => {
  if (e.target === document.getElementById('chHistoryOverlay')) closeChHistory();
});
window.openChannelHistory = openChannelHistory;

// ── Auto-refresh ───────────────────────────────────────────────────────────
setInterval(() => loadDashboard(true), REFRESH_MS);

// ── Expose for console debugging ───────────────────────────────────────────
window.loadDashboard = loadDashboard;
window.loadLaunches  = loadLaunches;

// ── Boot ───────────────────────────────────────────────────────────────────
populateChannelsList();
// Wait for selector (sets activeLaunchId to active DB launch) before loading dashboard
loadLaunchSelector().then(() => loadDashboard());
