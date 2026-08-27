/* M7 Terminal dashboard.
 *
 * Charts are hand-rolled SVG rather than a charting library: the page must be
 * self-contained, and the two forms needed here (one area line, one donut) are
 * small enough that a dependency would cost more than it saves.
 *
 * Colors come from the project design system (docs/ui/DESIGN.md). The
 * categorical pair used by the donut was validated for colour-vision
 * deficiency separation before use, and every segment is direct-labelled in
 * the legend so identity never rests on colour alone.
 */

const COLORS = {
  primary: "#2563eb",       // primary-container - series line
  positive: "#4edea3",      // secondary-fixed-dim
  negative: "#ffb2b7",      // tertiary-fixed-dim
  warning: "#f59e0b",
  ink: "#dae2fd",
  inkMuted: "#c3c6d7",
};

// Fixed assignment: a bucket keeps its colour regardless of ordering or count.
const ALLOCATION_COLORS = {
  KR: "#00a572", US: "#2563eb", KRW: "#00a572", USD: "#2563eb", OTHER: "#8d90a0",
};

const state = { view: "overview", range: "3M", allocBy: "market", history: null,
                editingName: false, auditCategory: "" };

/* ---------------------------------------------------------------- helpers */

const $ = (id) => document.getElementById(id);

function fmtInt(value) {
  if (value === null || value === undefined) return "—";
  return Math.round(value).toLocaleString("en-US");
}

function fmtSigned(value) {
  if (value === null || value === undefined) return "—";
  const rounded = Math.round(value);
  return (rounded >= 0 ? "+" : "") + rounded.toLocaleString("en-US");
}

function fmtPct(rate) {
  if (rate === null || rate === undefined) return "—";
  return (rate >= 0 ? "+" : "") + (rate * 100).toFixed(2) + "%";
}

function fmtPrice(value, currency) {
  if (value === null || value === undefined) return "—";
  const digits = currency === "KRW" ? 0 : 2;
  return value.toLocaleString("en-US", { minimumFractionDigits: digits, maximumFractionDigits: digits });
}

function toneClass(value) {
  if (value === null || value === undefined || value === 0) return "text-on-surface";
  return value > 0 ? "text-secondary-fixed-dim" : "text-tertiary-fixed-dim";
}

async function getJSON(url) {
  const response = await fetch(url);
  if (!response.ok) throw new Error(`${response.status} ${response.statusText}`);
  return response.json();
}

function showError(detail) {
  $("error-detail").textContent = detail || "";
  $("error-banner").hidden = !detail;
}

/* ------------------------------------------------------------ navigation */

function setView(view) {
  state.view = view;
  document.querySelectorAll("section[data-view]").forEach((section) => {
    section.hidden = section.dataset.view !== view;
  });
  document.querySelectorAll(".nav-item").forEach((item) => {
    const active = item.dataset.view === view;
    item.className =
      "nav-item flex items-center gap-3 px-3 py-2.5 rounded transition-colors " +
      (active
        ? "text-primary font-bold border-r-2 border-primary bg-surface-container-high"
        : "text-on-surface-variant hover:bg-surface-container-high");
  });
  $("page-title").textContent = { overview: "Overview", holdings: "Holdings",
    trading: "Trading", reports: "Reports", audit: "Audit Log",
    settings: "Settings" }[view] || view;

  if (view === "holdings") loadHoldings();
  if (view === "reports") loadReports();
  if (view === "audit") loadAudit().catch((err) => showError(String(err)));
  if (view === "settings") loadSettings();
}

/* -------------------------------------------------------------- overview */

async function loadOverview() {
  let data;
  try {
    data = await getJSON("/api/overview");
  } catch (err) {
    showError(String(err));
    return;
  }
  if (!data.ready) { showError(data.error); return; }
  showError(data.error);

  $("kpi-total").textContent = fmtInt(data.total_krw);
  $("kpi-total-usd").textContent = data.total_usd_equivalent
    ? "≈ $" + fmtInt(data.total_usd_equivalent) : "—";
  // Invested capital, converted at the purchase-time rate where one is known.
  $("kpi-invested").textContent = fmtInt(data.purchase_krw) + " KRW";

  $("kpi-pnl").textContent = fmtSigned(data.profit_krw);
  $("kpi-pnl-wrap").className = "font-data-mono text-2xl font-bold tracking-tight " + toneClass(data.profit_krw);

  // The API reports both nominal and after-fee returns; show the real one
  // when Toss provides it and say which is on screen.
  const hasAfterCost = data.profit_rate_after_cost !== null && data.profit_rate_after_cost !== undefined;
  const shownRate = hasAfterCost ? data.profit_rate_after_cost : data.profit_rate;
  const badge = $("kpi-pnl-rate");
  badge.textContent = fmtPct(shownRate);
  badge.className = "px-1.5 py-0.5 rounded font-data-mono text-[10px] font-bold " +
    (shownRate >= 0 ? "bg-secondary-container/20 text-secondary-fixed-dim" : "bg-tertiary-container/20 text-tertiary-fixed-dim");
  $("kpi-pnl-note").textContent = hasAfterCost
    ? "after fees & tax" : (data.has_unconverted_fx ? "환차손익 미반영" : "nominal");

  // Only foreign positions with a known purchase-time rate contribute; when
  // none do, hide the row instead of showing a misleading "0".
  const hasFx = data.fx_pnl_krw !== null && data.fx_pnl_krw !== undefined;
  $("kpi-fx-row").hidden = !hasFx;
  if (hasFx) {
    $("kpi-fx").textContent = fmtSigned(data.fx_pnl_krw) + " KRW";
    $("kpi-fx").className = toneClass(data.fx_pnl_krw);
  }

  $("kpi-day").textContent = fmtSigned(data.daily_profit_krw);
  $("kpi-day-wrap").className = "font-data-mono text-2xl font-bold tracking-tight " + toneClass(data.daily_profit_krw);
  $("kpi-day-rate").textContent = fmtPct(data.daily_profit_rate);
  $("kpi-day-arrow").textContent = data.daily_profit_krw > 0 ? "arrow_upward"
    : data.daily_profit_krw < 0 ? "arrow_downward" : "remove";
  $("kpi-day-rate-wrap").className =
    "flex items-center gap-1 mt-1 font-data-mono text-[11px] " + toneClass(data.daily_profit_krw);

  const power = data.buying_power || {};
  $("kpi-cash").textContent = fmtInt(power.KRW);
  $("kpi-cash-usd").textContent = power.USD !== null && power.USD !== undefined
    ? "USD " + fmtInt(power.USD) : "—";

  $("fx-chip").textContent = "USD/KRW: " + (data.exchange_rate
    ? data.exchange_rate.toLocaleString("en-US", { minimumFractionDigits: 2, maximumFractionDigits: 2 }) : "—");

  renderMarketChip("krx-chip", "KRX", data.market_status?.KR);
  renderMarketChip("us-chip", "US", data.market_status?.US);
  renderAlerts(data.warnings || []);
}

// Extended-hours sessions are tradable but are not the main session, so they
// get their own muted style rather than the full "Open" green.
const SESSION_LABELS = { regular: "Open", day: "Day", pre: "Pre", after: "After" };

function renderMarketChip(id, label, status) {
  const chip = $(id);
  const dim = "text-on-surface-variant/50 border border-outline-variant/30 px-2 py-1 rounded";
  if (!status || !status.known) {
    chip.textContent = `${label}: —`;
    chip.className = dim;
    return;
  }
  const text = SESSION_LABELS[status.session];
  if (!text) {
    chip.textContent = `${label}: Closed`;
    chip.className = dim;
    return;
  }
  const tone = status.session === "regular"
    ? "text-secondary-fixed-dim bg-secondary-container/10 border-secondary-container/30"
    : "text-tertiary-fixed-dim bg-tertiary-container/10 border-tertiary-container/30";
  chip.innerHTML = `<span class="w-1.5 h-1.5 rounded-full bg-current inline-block"></span> ${label}: ${text}`;
  chip.className = `${tone} border px-2 py-1 rounded flex items-center gap-1`;
}

function renderAlerts(warnings) {
  const host = $("alerts");
  host.innerHTML = "";
  warnings.forEach((warning) => {
    const div = document.createElement("div");
    div.className = "bg-surface-container-high border-l-[3px] rounded shadow-sm p-4 flex items-start gap-3 mb-gutter";
    div.style.borderLeftColor = COLORS.warning;
    div.innerHTML = `
      <span class="material-symbols-outlined mt-0.5" style="color:${COLORS.warning};font-variation-settings:'FILL' 1;">warning</span>
      <div><p class="text-[15px] font-semibold text-on-surface leading-tight"></p>
      <p class="text-body-md text-on-surface-variant mt-1 text-sm">토스 API가 보고한 매수 유의사항입니다. 해당 종목의 거래가 제한되거나 변동성이 확대된 상태일 수 있습니다.</p></div>`;
    div.querySelector("p").textContent = warning;
    host.appendChild(div);
  });
}

/* ----------------------------------------------------------------- chart */

async function loadHistory() {
  const data = await getJSON(`/api/history?range=${state.range}`);
  state.history = data;
  renderChart(data);
}

function renderChart(data) {
  const svg = $("chart");
  const points = data.points || [];
  const empty = $("chart-empty");

  // Two points do not make a trend line. Say what is happening instead of
  // drawing something that implies more history than exists.
  if (points.length < 3) {
    svg.innerHTML = "";
    empty.hidden = false;
    $("chart-empty-detail").textContent =
      `스냅샷 ${data.total_snapshots}개 수집됨 · main.py 를 실행할 때마다 한 점씩 쌓입니다.`;
    return;
  }
  empty.hidden = true;

  const host = $("chart-host");
  const W = host.clientWidth || 800;
  const H = host.clientHeight || 320;
  const pad = { top: 16, right: 16, bottom: 28, left: 68 };
  const innerW = Math.max(1, W - pad.left - pad.right);
  const innerH = Math.max(1, H - pad.top - pad.bottom);

  const values = points.map((p) => p.total_krw);
  let min = Math.min(...values);
  let max = Math.max(...values);
  if (min === max) { min -= 1; max += 1; }
  const span = max - min;
  min -= span * 0.1;
  max += span * 0.1;

  const x = (i) => pad.left + (points.length === 1 ? innerW / 2 : (i / (points.length - 1)) * innerW);
  const y = (v) => pad.top + innerH - ((v - min) / (max - min)) * innerH;

  const line = points.map((p, i) => `${i === 0 ? "M" : "L"}${x(i).toFixed(1)},${y(p.total_krw).toFixed(1)}`).join(" ");
  const area = `${line} L${x(points.length - 1).toFixed(1)},${pad.top + innerH} L${x(0).toFixed(1)},${pad.top + innerH} Z`;

  // Four recessive gridlines with value labels; axis text uses ink tokens,
  // never the series colour.
  let grid = "";
  for (let i = 0; i <= 4; i++) {
    const value = min + ((max - min) * i) / 4;
    const gy = y(value);
    grid += `<line x1="${pad.left}" y1="${gy}" x2="${W - pad.right}" y2="${gy}" stroke="${COLORS.ink}" stroke-opacity="0.08"/>`;
    grid += `<text x="${pad.left - 8}" y="${gy + 4}" text-anchor="end" font-size="10" font-family="JetBrains Mono, monospace" fill="${COLORS.inkMuted}" fill-opacity="0.6">${shortKRW(value)}</text>`;
  }

  const firstLabel = points[0].ts.slice(0, 10);
  const lastLabel = points[points.length - 1].ts.slice(0, 10);

  svg.setAttribute("viewBox", `0 0 ${W} ${H}`);
  svg.innerHTML = `
    <defs><linearGradient id="areaFill" x1="0" x2="0" y1="0" y2="1">
      <stop offset="0%" stop-color="${COLORS.primary}" stop-opacity="0.4"/>
      <stop offset="100%" stop-color="${COLORS.primary}" stop-opacity="0"/>
    </linearGradient></defs>
    ${grid}
    <path d="${area}" fill="url(#areaFill)"/>
    <path d="${line}" fill="none" stroke="${COLORS.primary}" stroke-width="2" stroke-linejoin="round" stroke-linecap="round"/>
    <text x="${pad.left}" y="${H - 8}" font-size="10" font-family="JetBrains Mono, monospace" fill="${COLORS.inkMuted}" fill-opacity="0.6">${firstLabel}</text>
    <text x="${W - pad.right}" y="${H - 8}" text-anchor="end" font-size="10" font-family="JetBrains Mono, monospace" fill="${COLORS.inkMuted}" fill-opacity="0.6">${lastLabel}</text>
    <line id="crosshair" y1="${pad.top}" y2="${pad.top + innerH}" stroke="${COLORS.ink}" stroke-opacity="0.3" stroke-dasharray="3 3" style="display:none"/>
    <circle id="cursor-dot" r="4.5" fill="${COLORS.primary}" stroke="#171f33" stroke-width="2" style="display:none"/>
    <rect id="chart-hit" x="${pad.left}" y="${pad.top}" width="${innerW}" height="${innerH}" fill="transparent"/>`;

  attachHover(svg, points, x, y, pad, innerH);
}

function attachHover(svg, points, x, y, pad, innerH) {
  const hit = svg.querySelector("#chart-hit");
  const crosshair = svg.querySelector("#crosshair");
  const dot = svg.querySelector("#cursor-dot");
  const tooltip = $("tooltip");
  const host = $("chart-host");

  hit.addEventListener("mousemove", (event) => {
    const box = svg.getBoundingClientRect();
    const scale = svg.viewBox.baseVal.width / box.width;
    const px = (event.clientX - box.left) * scale;

    let nearest = 0;
    let best = Infinity;
    points.forEach((_, i) => {
      const distance = Math.abs(x(i) - px);
      if (distance < best) { best = distance; nearest = i; }
    });

    const point = points[nearest];
    const cx = x(nearest);
    const cy = y(point.total_krw);

    crosshair.setAttribute("x1", cx); crosshair.setAttribute("x2", cx);
    crosshair.style.display = "";
    dot.setAttribute("cx", cx); dot.setAttribute("cy", cy);
    dot.style.display = "";

    tooltip.innerHTML =
      `<div class="text-on-surface-variant/70 mb-1">${point.ts.replace("T", " ").slice(0, 16)}</div>` +
      `<div class="text-on-surface font-bold">${fmtInt(point.total_krw)} KRW</div>` +
      `<div class="${point.profit_rate >= 0 ? "text-secondary-fixed-dim" : "text-tertiary-fixed-dim"}">${fmtPct(point.profit_rate)}</div>`;
    tooltip.hidden = false;

    const left = (cx / scale) + 14;
    const maxLeft = host.clientWidth - tooltip.offsetWidth - 8;
    tooltip.style.left = Math.min(left, maxLeft) + "px";
    tooltip.style.top = Math.max(8, (cy / scale) - 20) + "px";
  });

  hit.addEventListener("mouseleave", () => {
    crosshair.style.display = "none";
    dot.style.display = "none";
    tooltip.hidden = true;
  });
}

function shortKRW(value) {
  const abs = Math.abs(value);
  if (abs >= 1e8) return (value / 1e8).toFixed(1) + "억";
  if (abs >= 1e4) return (value / 1e4).toFixed(0) + "만";
  return Math.round(value).toLocaleString("en-US");
}

/* ------------------------------------------------------------ allocation */

async function loadAllocation() {
  const data = await getJSON(`/api/allocation?by=${state.allocBy}`);
  renderDonut(data.segments || []);
}

function renderDonut(segments) {
  const svg = $("donut");
  const legend = $("alloc-legend");
  legend.innerHTML = "";

  if (!segments.length) {
    svg.innerHTML = `<circle cx="50" cy="50" r="40" fill="none" stroke="#2d3449" stroke-width="12"/>`;
    $("donut-label").textContent = "—";
    $("donut-value").textContent = "—";
    return;
  }

  const R = 40;
  const CIRC = 2 * Math.PI * R;
  let offset = 0;
  let markup = `<circle cx="50" cy="50" r="${R}" fill="none" stroke="#2d3449" stroke-width="12"/>`;

  segments.forEach((segment) => {
    const color = ALLOCATION_COLORS[segment.key] || ALLOCATION_COLORS.OTHER;
    const length = segment.share * CIRC;
    // A 2px surface gap keeps adjacent segments from reading as one arc.
    const gap = segments.length > 1 ? 2 : 0;
    markup += `<circle cx="50" cy="50" r="${R}" fill="none" stroke="${color}" stroke-width="12"
      stroke-dasharray="${Math.max(0, length - gap).toFixed(2)} ${(CIRC - length + gap).toFixed(2)}"
      stroke-dashoffset="${(-offset).toFixed(2)}"><title>${segment.label}: ${(segment.share * 100).toFixed(1)}%</title></circle>`;
    offset += length;

    const row = document.createElement("div");
    row.className = "flex justify-between items-center text-sm";
    row.innerHTML =
      `<div class="flex items-center gap-2"><div class="swatch w-3 h-3 rounded-sm shrink-0"></div><span class="label text-on-surface"></span></div>` +
      `<span class="value font-data-mono font-medium text-on-surface-variant"></span>`;
    row.querySelector(".swatch").style.backgroundColor = color;
    row.querySelector(".label").textContent = segment.label;
    row.querySelector(".value").textContent = fmtInt(segment.value_krw);
    legend.appendChild(row);
  });

  svg.innerHTML = markup;
  $("donut-label").textContent = segments[0].key;
  $("donut-value").textContent = (segments[0].share * 100).toFixed(0) + "%";
}

/* -------------------------------------------------------------- holdings */

async function loadHoldings() {
  if (state.editingName) return;   // never rebuild the table mid-edit
  const data = await getJSON("/api/holdings");
  const body = $("holdings-body");
  body.innerHTML = "";
  const positions = data.positions || [];
  $("holdings-count").textContent = `${positions.length} positions`;

  if (!positions.length) {
    body.innerHTML = `<tr><td colspan="12" class="px-4 py-8 text-center text-on-surface-variant font-body-md">
      보유 종목이 없습니다. 토스 계좌 보유분은 자동으로, 타 증권사 보유분은 config.yaml 의 portfolio.manual 로 표시됩니다.</td></tr>`;
    return;
  }

  positions.forEach((position) => {
    const row = document.createElement("tr");
    row.className = "border-b border-outline-variant/20 hover:bg-surface-container-high transition-colors h-8";
    const isToss = position.source.includes("toss");
    const badgeClass = isToss
      ? "bg-primary-container/20 text-primary border-primary/30"
      : "bg-surface-container-highest text-on-surface-variant border-outline-variant/50";
    row.innerHTML = `
      <td class="px-4 py-1.5 text-on-surface">${position.symbol || "—"}</td>
      <td class="name-cell px-4 py-1.5 font-body-md"></td>
      <td class="px-4 py-1.5"><span class="text-[10px] px-1.5 py-0.5 rounded border ${badgeClass}">${isToss ? "토스" : "수기"}</span></td>
      <td class="px-4 py-1.5 text-right">${position.quantity}</td>
      <td class="px-4 py-1.5 text-right">${fmtPrice(position.last_price, position.currency)} <span class="text-on-surface-variant/50 text-[10px]">${position.currency}</span></td>
      <td class="px-4 py-1.5 text-right text-on-surface-variant">${fmtPrice(position.avg_price, position.currency)}</td>
      <td class="px-4 py-1.5 text-right text-on-surface-variant">${fmtInt(position.cost_krw)}</td>
      <td class="px-4 py-1.5 text-right">${fmtInt(position.value_krw)}</td>
      <td class="px-4 py-1.5 text-right ${toneClass(position.profit_krw)}">${fmtSigned(position.profit_krw)}</td>
      <td class="px-4 py-1.5 text-right ${toneClass(position.profit_rate)}">${fmtPct(position.profit_rate)}</td>
      <td class="px-4 py-1.5 text-right ${toneClass(position.fx_pnl_krw)}">${fmtSigned(position.fx_pnl_krw)}</td>
      <td class="px-4 py-1.5 text-right text-on-surface-variant">${(position.weight * 100).toFixed(1)}%</td>`;
    makeNameEditable(row.querySelector(".name-cell"), position);
    body.appendChild(row);
  });
}

/* Toss reports name == symbol for some tickers (IONX, TSLL), so the name is
 * click-to-edit. The override is stored server-side and also applies to the
 * Notion report, so a ticker reads the same in both places. */
function makeNameEditable(cell, position) {
  if (!position.symbol) {           // static assets are named in config.yaml
    cell.textContent = position.name;
    cell.className += " text-on-surface-variant";
    return;
  }

  const render = (name) => {
    cell.textContent = name;
    cell.title = "클릭해서 이름 수정";
    cell.className =
      "name-cell px-4 py-1.5 font-body-md text-on-surface-variant cursor-text " +
      "hover:text-on-surface hover:underline decoration-dotted underline-offset-4";
  };

  const edit = () => {
    state.editingName = true;
    const current = cell.textContent;
    const input = document.createElement("input");
    input.value = current;
    input.maxLength = 80;
    input.className =
      "w-full bg-surface-container-lowest border border-primary/60 rounded px-2 py-0.5 " +
      "text-on-surface font-body-md outline-none";
    cell.textContent = "";
    cell.className = "name-cell px-2 py-1";
    cell.appendChild(input);
    input.focus();
    input.select();

    let done = false;
    const finish = async (save) => {
      if (done) return;
      done = true;
      state.editingName = false;
      const value = input.value.trim();
      if (!save || value === current) { render(current); return; }
      render(value || position.symbol);
      try {
        const res = await fetch(`/api/holdings/${encodeURIComponent(position.symbol)}/name`, {
          method: "PUT",
          headers: { "Content-Type": "application/json" },
          body: JSON.stringify({ name: value }),
        });
        if (!res.ok) throw new Error(`${res.status}`);
        const data = await res.json();
        render(data.name || position.symbol);   // blank clears the override
      } catch (err) {
        render(current);                        // put the old name back
        showError(`이름 저장 실패: ${err}`);
      }
    };

    input.addEventListener("keydown", (event) => {
      if (event.key === "Enter") finish(true);
      if (event.key === "Escape") finish(false);
    });
    input.addEventListener("blur", () => finish(true));
  };

  render(position.name);
  cell.addEventListener("click", () => { if (!cell.querySelector("input")) edit(); });
}

/* --------------------------------------------------------------- reports */

async function loadReports() {
  const data = await getJSON("/api/reports");
  const host = $("reports-list");
  host.innerHTML = "";
  const reports = data.reports || [];

  if (!reports.length) {
    host.innerHTML = `<div class="p-8 text-center text-on-surface-variant">
      아직 생성된 리포트가 없습니다. <code class="font-data-mono text-primary">python main.py</code> 를 실행하세요.</div>`;
    return;
  }

  reports.forEach((report) => {
    const item = document.createElement("div");
    item.className = "p-4 hover:bg-surface-container-high transition-colors";
    item.innerHTML = `
      <div class="flex items-center justify-between gap-4">
        <div class="min-w-0">
          <p class="font-semibold text-on-surface truncate"></p>
          <p class="font-data-mono text-xs text-on-surface-variant/60 mt-0.5">${report.ts}</p>
        </div>
        ${report.url ? `<a href="${report.url}" target="_blank" rel="noopener"
          class="shrink-0 border border-outline-variant hover:bg-surface-container px-3 py-1.5 rounded text-label-caps font-bold tracking-wide">OPEN</a>` : ""}
      </div>
      <p class="text-sm text-on-surface-variant mt-2 line-clamp-2"></p>`;
    item.querySelector("p").textContent = report.title || "Report";
    const summary = item.querySelectorAll("p")[2];
    if (summary) summary.textContent = (report.ai_comment || "").slice(0, 220);
    host.appendChild(item);
  });

  // Surface the newest AI comment on the Overview card too.
  const latest = reports[0];
  if (latest && latest.ai_comment) {
    $("ai-text").textContent = latest.ai_comment.replace(/\s+/g, " ").slice(0, 260) + "…";
    $("ai-ts").textContent = latest.ts;
    if (latest.url) $("ai-link").href = latest.url; else $("ai-link").hidden = true;
    $("ai-card").hidden = false;
  }
}

/* -------------------------------------------------------------- settings */

async function loadSettings() {
  const data = await getJSON("/api/settings");
  $("settings-body").textContent = JSON.stringify(data, null, 2);
}

/* ----------------------------------------------------------------- health */

async function loadHealth() {
  try {
    const data = await getJSON("/api/health");
    $("api-status").textContent = "Brokerage API: " + (data.connected ? "Connected" : "Error");
    $("api-dot").className = "w-2 h-2 rounded-full " +
      (data.connected ? "bg-secondary-container animate-pulse" : "bg-error");
    $("last-sync").textContent = "Last Sync: " +
      (data.last_sync ? new Date(data.last_sync).toLocaleTimeString("ko-KR", { hour: "2-digit", minute: "2-digit" }) : "—");
  } catch (err) {
    $("api-status").textContent = "Brokerage API: Offline";
    $("api-dot").className = "w-2 h-2 rounded-full bg-error";
  }
}

/* ------------------------------------------------------------------ init */

function styleRangeButtons() {
  document.querySelectorAll(".range-btn").forEach((button) => {
    const active = button.dataset.range === state.range;
    button.className = "range-btn px-3 py-1 text-[11px] font-data-mono font-bold rounded transition-colors " +
      (active ? "bg-surface border border-outline-variant/50 text-on-surface shadow-sm"
              : "text-on-surface-variant hover:text-on-surface");
  });
}


/* ------------------------------------------------------------- audit log */

const AUDIT_LABELS = {
  baseline: "기준선", universe: "유니버스", strategies: "전략 목록", strategy_params: "전략 파라미터",
  limits: "리스크 한도", veto: "AI 보류", candidate: "AI 제안",
};

// Two actors, two very different weights of claim: "human" is inferred from
// the OS user of whichever process noticed the edit, "ai" is exact. The badge
// colours say so at a glance rather than in a footnote nobody reads.
const AUDIT_TONE = {
  human: "bg-primary-container/30 text-primary border-primary/40",
  ai: "bg-secondary-container/25 text-secondary border-secondary/40",
};

function auditChangeLine(change) {
  const before = change.before === null || change.before === undefined ? "—" : String(change.before);
  const after = change.after === null || change.after === undefined ? "—" : String(change.after);
  const row = document.createElement("div");
  row.className = "flex items-start gap-2 font-data-mono text-xs py-0.5";
  const target = document.createElement("span");
  target.className = "text-on-surface shrink-0";
  target.textContent = change.target;
  const arrow = document.createElement("span");
  arrow.className = "text-on-surface-variant/70 break-all";
  arrow.textContent = `${before} → ${after}`;
  row.append(target, arrow);
  if (change.evidence) {
    const evidence = document.createElement("span");
    evidence.className = "text-on-surface-variant/50 italic break-all";
    evidence.textContent = `(근거: ${change.evidence})`;
    row.appendChild(evidence);
  }
  return row;
}

async function loadAudit() {
  const query = state.auditCategory ? `?category=${state.auditCategory}` : "";
  const data = await getJSON(`/api/audit${query}`);
  const host = $("audit-list");
  host.innerHTML = "";
  const entries = data.entries || [];

  if (!entries.length) {
    host.innerHTML = `<div class="p-8 text-center text-on-surface-variant">
      기록된 변경이 없습니다. 설정을 바꾼 뒤 <code class="font-data-mono text-primary">python main.py</code>
      또는 <code class="font-data-mono text-primary">python trade.py</code> 를 실행하면 감지됩니다.</div>`;
    return;
  }

  entries.forEach((entry) => {
    const item = document.createElement("div");
    item.className = "p-4 hover:bg-surface-container-high transition-colors";

    const head = document.createElement("div");
    head.className = "flex items-center gap-2 flex-wrap";

    const badge = document.createElement("span");
    badge.className =
      "px-2 py-0.5 rounded border text-label-caps font-bold tracking-wide " +
      (AUDIT_TONE[entry.actor_kind] || AUDIT_TONE.human);
    badge.textContent = entry.actor_kind === "ai" ? "AI" : "사람";

    const category = document.createElement("span");
    category.className = "font-semibold text-on-surface";
    category.textContent = AUDIT_LABELS[entry.category] || entry.category;

    const when = document.createElement("span");
    when.className = "font-data-mono text-xs text-on-surface-variant/60 ml-auto";
    when.textContent = (entry.detected_at || "").replace("T", " ").slice(0, 19);

    head.append(badge, category, when);

    const who = document.createElement("p");
    who.className = "text-xs text-on-surface-variant/60 mt-1";
    who.textContent = `${entry.actor || "—"} · ${entry.source || "—"}`;

    const summary = document.createElement("p");
    summary.className = "text-sm text-on-surface-variant mt-1";
    summary.textContent = entry.summary || "";

    const changes = document.createElement("div");
    changes.className = "mt-2 border-l-2 border-outline-variant/40 pl-3";
    (entry.changes || []).forEach((change) => changes.appendChild(auditChangeLine(change)));

    item.append(head, who, summary, changes);
    host.appendChild(item);
  });
}

function styleAuditTabs() {
  document.querySelectorAll(".audit-tab").forEach((tab) => {
    const active = (tab.dataset.category || "") === state.auditCategory;
    tab.className = "audit-tab px-3 py-1.5 rounded text-xs font-semibold transition-colors border " +
      (active
        ? "border-primary text-primary bg-surface-container-high"
        : "border-outline-variant/40 text-on-surface-variant hover:text-on-surface");
  });
}

function styleAllocTabs() {
  document.querySelectorAll(".alloc-tab").forEach((tab) => {
    const active = tab.dataset.by === state.allocBy;
    tab.className = "alloc-tab px-4 py-2 font-body-md text-sm transition-colors " +
      (active ? "text-primary border-b-2 border-primary -mb-px" : "text-on-surface-variant hover:text-on-surface");
  });
}

function init() {
  document.querySelectorAll(".nav-item").forEach((item) => {
    item.addEventListener("click", (event) => { event.preventDefault(); setView(item.dataset.view); });
  });
  document.querySelectorAll(".range-btn").forEach((button) => {
    button.addEventListener("click", () => {
      state.range = button.dataset.range;
      styleRangeButtons();
      loadHistory().catch((err) => showError(String(err)));
    });
  });
  document.querySelectorAll(".alloc-tab").forEach((tab) => {
    tab.addEventListener("click", () => {
      state.allocBy = tab.dataset.by;
      styleAllocTabs();
      loadAllocation().catch((err) => showError(String(err)));
    });
  });
  $("alert-bell").addEventListener("click", () => setView("overview"));
  document.querySelectorAll(".audit-tab").forEach((tab) => {
    tab.addEventListener("click", () => {
      state.auditCategory = tab.dataset.category || "";
      styleAuditTabs();
      loadAudit().catch((err) => showError(String(err)));
    });
  });

  const glossary = $("glossary-overlay");
  const closeGlossary = () => { glossary.hidden = true; };
  $("glossary-btn").addEventListener("click", () => { glossary.hidden = false; });
  $("glossary-close").addEventListener("click", closeGlossary);
  glossary.addEventListener("click", (event) => { if (event.target === glossary) closeGlossary(); });
  window.addEventListener("keydown", (event) => {
    if (event.key === "Escape" && !glossary.hidden) closeGlossary();
  });
  // Changing the hash is a same-document navigation, so deep links only work
  // if we listen for it.
  window.addEventListener("hashchange", () => setView(location.hash.slice(1) || "overview"));

  styleRangeButtons();
  styleAllocTabs();
  styleAuditTabs();
  setView(location.hash.slice(1) || "overview");

  const refresh = () => {
    loadOverview().catch((err) => showError(String(err)));
    loadHealth();
    if (state.view === "holdings") loadHoldings().catch(() => {});
  };

  refresh();
  loadHistory().catch((err) => showError(String(err)));
  loadAllocation().catch((err) => showError(String(err)));
  loadReports().catch(() => {});

  // The server caches upstream calls, so polling here costs nothing at Toss.
  setInterval(refresh, 15000);
  window.addEventListener("resize", () => { if (state.history) renderChart(state.history); });
}

document.addEventListener("DOMContentLoaded", init);
