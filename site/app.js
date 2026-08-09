const $ = (selector, root = document) => root.querySelector(selector);

const state = {
  payload: null,
  rows: [],
  market: "",
  stage: "",
};

const els = {
  methodGuide: $("#methodGuide"),
  searchBox: $("#searchBox"),
  minPrice: $("#minPrice"),
  maxPrice: $("#maxPrice"),
  minScore: $("#minScore"),
  sortBy: $("#sortBy"),
  marketTabs: $("#marketTabs"),
  stageTabs: $("#stageTabs"),
  clearFilters: $("#clearFilters"),
  candidateRows: $("#candidateRows"),
  candidateCards: $("#candidateCards"),
  candidateCount: $("#candidateCount"),
  triggerCount: $("#triggerCount"),
  priorityCount: $("#priorityCount"),
  instrumentCount: $("#instrumentCount"),
  marketCount: $("#marketCount"),
  failureRate: $("#failureRate"),
  candidateMeta: $("#candidateMeta"),
  resultCount: $("#resultCount"),
  disclaimer: $("#disclaimer"),
  publishState: $("#publishState"),
  publishStateText: $("#publishStateText"),
};

if (window.matchMedia("(max-width: 760px)").matches) {
  els.methodGuide.removeAttribute("open");
}

function escapeHtml(value) {
  return String(value ?? "")
    .replaceAll("&", "&amp;")
    .replaceAll("<", "&lt;")
    .replaceAll(">", "&gt;")
    .replaceAll('"', "&quot;")
    .replaceAll("'", "&#039;");
}

function number(value) {
  if (value === null || value === undefined || value === "") return null;
  const parsed = Number(value);
  return Number.isFinite(parsed) ? parsed : null;
}

function fmt(value) {
  const parsed = number(value);
  if (parsed === null) return "-";
  if (Math.abs(parsed) >= 1000) return parsed.toLocaleString("zh-CN", { maximumFractionDigits: 0 });
  if (Math.abs(parsed) >= 100) return parsed.toLocaleString("zh-CN", { maximumFractionDigits: 2 });
  return parsed.toLocaleString("zh-CN", { maximumFractionDigits: 3 });
}

function pct(value, signed = false) {
  const parsed = number(value);
  if (parsed === null) return "-";
  const sign = signed && parsed > 0 ? "+" : "";
  return `${sign}${(parsed * 100).toFixed(1)}%`;
}

function marketLabel(value) {
  const market = String(value || "").toUpperCase();
  if (market === "CN") return "A股";
  if (market === "US") return "美股";
  if (market === "GOLD") return "黄金";
  return market || "其他";
}

function dateTime(value) {
  if (!value) return "尚未更新";
  const parsed = new Date(value);
  if (Number.isNaN(parsed.getTime())) return String(value).slice(0, 19).replace("T", " ");
  return parsed.toLocaleString("zh-CN", {
    timeZone: "Asia/Shanghai",
    month: "2-digit",
    day: "2-digit",
    hour: "2-digit",
    minute: "2-digit",
    hour12: false,
  });
}

function isStale(value) {
  const parsed = new Date(value);
  if (Number.isNaN(parsed.getTime())) return true;
  return Date.now() - parsed.getTime() > 40 * 60 * 60 * 1000;
}

function filteredRows() {
  const query = els.searchBox.value.trim().toLowerCase();
  const minPrice = number(els.minPrice.value);
  const maxPrice = number(els.maxPrice.value);
  const minScore = number(els.minScore.value);

  const rows = state.rows.filter((item) => {
    const close = number(item.last_close);
    const score = number(item.recommend_score);
    if (state.market && item.market !== state.market) return false;
    if (state.stage && item.signal_stage !== state.stage) return false;
    if (query) {
      const haystack = `${item.symbol || ""} ${item.monitor_symbol || ""} ${item.name || ""}`.toLowerCase();
      if (!haystack.includes(query)) return false;
    }
    if (minPrice !== null && (close === null || close < minPrice)) return false;
    if (maxPrice !== null && (close === null || close > maxPrice)) return false;
    if (minScore !== null && (score === null || score < minScore)) return false;
    return true;
  });

  const sortBy = els.sortBy.value;
  rows.sort((a, b) => {
    if (sortBy === "upside") return (number(b.target_1_upside) ?? -Infinity) - (number(a.target_1_upside) ?? -Infinity);
    if (sortBy === "price-asc") return (number(a.last_close) ?? Infinity) - (number(b.last_close) ?? Infinity);
    if (sortBy === "price-desc") return (number(b.last_close) ?? -Infinity) - (number(a.last_close) ?? -Infinity);
    return (number(b.recommend_score) ?? 0) - (number(a.recommend_score) ?? 0) || (number(b.score) ?? 0) - (number(a.score) ?? 0);
  });
  return rows;
}

function tableRow(item) {
  const targets = [item.target_1, item.target_2].filter((value) => number(value) !== null);
  const invalidClass = item.position_label === "低于失效位" ? " invalid" : "";
  const stage = item.signal_stage === "trigger" ? "trigger" : "watch";
  return `
    <tr>
      <td><span class="badge ${escapeHtml(String(item.market || "").toLowerCase())}">${escapeHtml(marketLabel(item.market))}</span></td>
      <td>
        <div class="symbol">
          <strong>${escapeHtml(item.monitor_symbol || item.symbol)}</strong>
          <small>${escapeHtml(item.name || "")}</small>
        </div>
      </td>
      <td><span class="stage-badge ${stage}">${escapeHtml(item.stage_label || "观察候选")}</span></td>
      <td class="pattern-cell">
        ${escapeHtml(item.wave_level || "")}${item.wave_level ? " · " : ""}${escapeHtml(item.pattern || "")}
        <div class="position-label${invalidClass}">${escapeHtml(item.position_label || "")}</div>
        <div class="muted">${escapeHtml(item.confirmation_detail || "等待右侧确认")}</div>
      </td>
      <td>
        <div class="score">${fmt(item.recommend_score)}<small>${escapeHtml(item.recommend_label || "")}</small></div>
        <div class="score-breakdown">结${fmt(item.structure_score)} 位${fmt(item.position_score)} 确${fmt(item.confirmation_score)}</div>
      </td>
      <td>${fmt(item.last_close)}<div class="muted">${escapeHtml(item.last_date || "")}</div></td>
      <td>${fmt(item.support)}<div class="muted">距离 ${pct(item.distance_to_support, true)}</div></td>
      <td>${fmt(item.invalid_below)}</td>
      <td>${targets.map(fmt).join(" / ") || "-"}</td>
      <td class="${number(item.target_1_upside) >= 0 ? "positive" : "negative"}">${pct(item.target_1_upside, true)}<div class="muted">盈亏比 ${fmt(item.risk_reward)}</div></td>
    </tr>`;
}

function candidateCard(item) {
  const invalidClass = item.position_label === "低于失效位" ? " invalid" : "";
  const stage = item.signal_stage === "trigger" ? "trigger" : "watch";
  return `
    <article class="candidate-card">
      <div class="card-head">
        <span class="badge ${escapeHtml(String(item.market || "").toLowerCase())}">${escapeHtml(marketLabel(item.market))}</span>
        <div class="card-symbol">
          <strong>${escapeHtml(item.monitor_symbol || item.symbol)}</strong>
          <small>${escapeHtml(item.name || "")}</small>
        </div>
        <div class="card-score">${fmt(item.recommend_score)}<small>${escapeHtml(item.recommend_label || "")}</small></div>
      </div>
      <div class="card-stage-row">
        <span class="stage-badge ${stage}">${escapeHtml(item.stage_label || "观察候选")}</span>
        <span>${escapeHtml(item.wave_level || "")}${item.multi_level_alignment ? " · 双级别共振" : ""}</span>
      </div>
      <div class="card-pattern">
        ${escapeHtml(item.pattern || "")} · <span class="position-label${invalidClass}">${escapeHtml(item.position_label || "")}</span>
        <div class="muted">${escapeHtml(item.confirmation_detail || "等待右侧确认")}</div>
      </div>
      <div class="price-grid">
        <div><span>最新价</span><strong>${fmt(item.last_close)}</strong></div>
        <div><span>支撑</span><strong>${fmt(item.support)}</strong></div>
        <div><span>失效</span><strong>${fmt(item.invalid_below)}</strong></div>
        <div><span>目标</span><strong>${fmt(item.target_1)}</strong></div>
      </div>
      <div class="card-foot">
        <span>结${fmt(item.structure_score)} · 位${fmt(item.position_score)} · 确${fmt(item.confirmation_score)}</span>
        <span>盈亏比 ${fmt(item.risk_reward)}</span>
      </div>
    </article>`;
}

function render() {
  const rows = filteredRows();
  const triggers = rows.filter((item) => item.signal_stage === "trigger").length;
  const priority = rows.filter((item) => number(item.recommend_score) >= 85).length;
  els.candidateCount.textContent = rows.length.toLocaleString("zh-CN");
  els.triggerCount.textContent = triggers.toLocaleString("zh-CN");
  els.priorityCount.textContent = priority.toLocaleString("zh-CN");
  els.resultCount.textContent = `${rows.length} 条`;

  if (!rows.length) {
    const empty = "没有符合当前筛选条件的候选。";
    els.candidateRows.innerHTML = `<tr><td class="empty" colspan="10">${empty}</td></tr>`;
    els.candidateCards.innerHTML = `<div class="empty">${empty}</div>`;
    return;
  }
  els.candidateRows.innerHTML = rows.map(tableRow).join("");
  els.candidateCards.innerHTML = rows.map(candidateCard).join("");
}

function renderMetadata(payload) {
  const meta = payload.metadata || {};
  const markets = Object.values(meta.market_counts || {}).filter((count) => Number(count) > 0).length;
  els.instrumentCount.textContent = Number(meta.instrument_count || 0).toLocaleString("zh-CN");
  els.marketCount.textContent = markets || "-";
  els.failureRate.textContent = pct(meta.failure_rate);
  els.disclaimer.textContent = payload.disclaimer || "仅供研究参考，不构成投资建议。";

  if (!payload.published_at) {
    els.candidateMeta.textContent = "等待首次全市场扫描";
    els.publishState.classList.remove("stale", "error");
    els.publishStateText.textContent = "等待首次扫描";
    return;
  }

  const details = [
    `更新于 ${dateTime(payload.published_at)}`,
    `触发 ${meta.trigger_count || 0} 条`,
    `观察 ${meta.watch_count || 0} 条`,
  ];
  if (meta.duration_seconds !== null && meta.duration_seconds !== undefined) details.push(`耗时 ${Math.round(meta.duration_seconds)} 秒`);
  els.candidateMeta.textContent = details.join(" · ");

  els.publishState.classList.toggle("stale", isStale(payload.published_at));
  els.publishStateText.textContent = isStale(payload.published_at) ? "等待下次更新" : `已更新 ${dateTime(payload.published_at)}`;
}

async function loadData() {
  try {
    const response = await fetch(`./data/candidates.json?v=${Date.now()}`, { cache: "no-store" });
    if (!response.ok) throw new Error(`HTTP ${response.status}`);
    const payload = await response.json();
    state.payload = payload;
    state.rows = Array.isArray(payload.candidates) ? payload.candidates : [];
    renderMetadata(payload);
    render();
    document.documentElement.dataset.ready = "true";
  } catch (error) {
    els.publishState.classList.add("error");
    els.publishStateText.textContent = "数据读取失败";
    els.candidateMeta.textContent = "暂时无法读取最新扫描结果";
    els.candidateRows.innerHTML = `<tr><td class="empty" colspan="10">数据读取失败，请稍后刷新。</td></tr>`;
    els.candidateCards.innerHTML = `<div class="empty">数据读取失败，请稍后刷新。</div>`;
    console.error(error);
  }
}

let inputTimer = null;
function queueRender() {
  window.clearTimeout(inputTimer);
  inputTimer = window.setTimeout(render, 120);
}

els.marketTabs.addEventListener("click", (event) => {
  const button = event.target.closest("button[data-market]");
  if (!button) return;
  state.market = button.dataset.market || "";
  els.marketTabs.querySelectorAll("button").forEach((item) => item.classList.toggle("active", item === button));
  render();
});

els.stageTabs.addEventListener("click", (event) => {
  const button = event.target.closest("button[data-stage]");
  if (!button) return;
  state.stage = button.dataset.stage || "";
  els.stageTabs.querySelectorAll("button").forEach((item) => item.classList.toggle("active", item === button));
  render();
});

[els.searchBox, els.minPrice, els.maxPrice, els.minScore].forEach((input) => input.addEventListener("input", queueRender));
els.sortBy.addEventListener("change", render);

els.clearFilters.addEventListener("click", () => {
  state.market = "";
  state.stage = "";
  els.searchBox.value = "";
  els.minPrice.value = "";
  els.maxPrice.value = "";
  els.minScore.value = "60";
  els.sortBy.value = "recommend";
  els.marketTabs.querySelectorAll("button").forEach((button) => button.classList.toggle("active", !button.dataset.market));
  els.stageTabs.querySelectorAll("button").forEach((button) => button.classList.toggle("active", !button.dataset.stage));
  render();
});

loadData();
