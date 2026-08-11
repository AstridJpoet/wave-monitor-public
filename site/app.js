const $ = (selector, root = document) => root.querySelector(selector);

const state = {
  payload: null,
  history: null,
  rows: [],
  market: "",
  stage: "",
};

const els = {
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
  otherCandidates: $("#otherCandidates"),
  otherRows: $("#otherRows"),
  otherCards: $("#otherCards"),
  otherCount: $("#otherCount"),
  indexCards: $("#indexCards"),
  candidateCount: $("#candidateCount"),
  alertCount: $("#alertCount"),
  priorityCount: $("#priorityCount"),
  instrumentCount: $("#instrumentCount"),
  marketCount: $("#marketCount"),
  failureRate: $("#failureRate"),
  candidateMeta: $("#candidateMeta"),
  resultCount: $("#resultCount"),
  disclaimer: $("#disclaimer"),
  publishState: $("#publishState"),
  publishStateText: $("#publishStateText"),
  historyMeta: $("#historyMeta"),
  historyMetrics: $("#historyMetrics"),
  historySignalCount: $("#historySignalCount"),
  historyRows: $("#historyRows"),
  historyCards: $("#historyCards"),
  snapshotCount: $("#snapshotCount"),
  snapshotSelect: $("#snapshotSelect"),
  snapshotMeta: $("#snapshotMeta"),
  snapshotRuns: $("#snapshotRuns"),
};

function escapeHtml(value) {
  return String(value ?? "")
    .replaceAll("&", "&amp;")
    .replaceAll("<", "&lt;")
    .replaceAll(">", "&gt;")
    .replaceAll('"', "&quot;")
    .replaceAll("'", "&#039;");
}

function bilingualHtml(zh, en, className = "") {
  const classes = ["en-copy", className].filter(Boolean).join(" ");
  return `<span>${escapeHtml(zh)}</span>${en ? `<small class="${classes}">${escapeHtml(en)}</small>` : ""}`;
}

function setBilingual(element, zh, en) {
  element.innerHTML = bilingualHtml(zh, en);
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

function returnClass(value) {
  const parsed = number(value);
  if (parsed === null || parsed === 0) return "neutral";
  return parsed > 0 ? "positive" : "negative";
}

function marketLabel(value) {
  const market = String(value || "").toUpperCase();
  if (market === "CN") return "A股";
  if (market === "US") return "美股";
  if (market === "GOLD") return "黄金";
  return market || "其他";
}

function marketEnglish(value) {
  const market = String(value || "").toUpperCase();
  if (market === "CN") return "A-share";
  if (market === "US") return "US";
  if (market === "GOLD") return "Gold";
  return market || "Other";
}

const patternEnglish = {
  "2浪回撤候选": "Wave 2 retracement",
  "4浪回踩候选": "Wave 4 pullback",
  "ABC/C浪末端候选": "Late ABC / Wave C",
  "疑似3浪突破": "Potential Wave 3 breakout",
};

const positionEnglish = {
  "支撑附近": "Near support",
  "支撑上方": "Above support",
  "支撑下方": "Below support",
  "低于失效位": "Below invalidation",
};

const confirmationEnglish = {
  "支撑收复": "Support reclaimed",
  "更高低点": "Higher low",
  "突破3日高点": "Breaks 3-day high",
  "成交放量": "Volume expansion",
  "近5日突破": "Breakout within 5 days",
  "突破放量": "Breakout on volume",
  "突破后守位": "Holds breakout level",
  "等待右侧确认": "Awaiting confirmation",
};

function stageCopy(item) {
  if (item.signal_stage === "trigger") return { zh: "右侧触发", en: "Confirmed Entry" };
  if (item.signal_stage === "probe") return { zh: "左侧试错", en: "Early Probe" };
  return { zh: "观察候选", en: "Watchlist" };
}

function waveLevelEnglish(value) {
  if (value === "大级别") return "Major level";
  if (value === "中级别") return "Intermediate level";
  return value || "";
}

function recommendationEnglish(value) {
  if (value === "优先") return "Priority";
  if (value === "较强") return "Strong";
  if (value === "观察") return "Watch";
  return value || "";
}

function marketContextEnglish(value) {
  if (value === "大盘强势") return "Strong market";
  if (value === "大盘偏强") return "Constructive market";
  if (value === "大盘震荡") return "Mixed market";
  if (value === "大盘偏弱") return "Weak market";
  if (value === "未取得大盘数据") return "Market data unavailable";
  return value || "";
}

function indexStatusEnglish(value) {
  if (value === "强势") return "Strong";
  if (value === "偏强") return "Constructive";
  if (value === "震荡") return "Mixed";
  if (value === "偏弱") return "Weak";
  return value || "";
}

function indexNameEnglish(value) {
  if (value === "上证指数") return "Shanghai Composite";
  if (value === "沪深300") return "CSI 300";
  if (value === "标普500") return "S&P 500";
  if (value === "纳斯达克综合") return "Nasdaq Composite";
  return value || "";
}

function confirmationTranslation(value) {
  const source = value || "等待右侧确认";
  return source
    .split("、")
    .map((part) => confirmationEnglish[part] || part)
    .join(", ");
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
    if (state.stage === "alert" && !["trigger", "probe"].includes(item.signal_stage)) return false;
    if (state.stage && state.stage !== "alert" && item.signal_stage !== state.stage) return false;
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
  const stage = ["trigger", "probe"].includes(item.signal_stage) ? item.signal_stage : "watch";
  const stageText = stageCopy(item);
  const patternEn = patternEnglish[item.pattern] || item.pattern || "";
  const levelEn = waveLevelEnglish(item.wave_level);
  const positionEn = positionEnglish[item.position_label] || item.position_label || "";
  const recommendationEn = recommendationEnglish(item.recommend_label);
  const confirmation = item.confirmation_detail || "等待右侧确认";
  return `
    <tr>
      <td><span class="badge ${escapeHtml(String(item.market || "").toLowerCase())}">${bilingualHtml(marketLabel(item.market), marketEnglish(item.market))}</span></td>
      <td>
        <div class="symbol">
          <strong>${escapeHtml(item.monitor_symbol || item.symbol)}</strong>
          <small>${escapeHtml(item.name || "")}</small>
        </div>
      </td>
      <td><span class="stage-badge ${stage}">${bilingualHtml(stageText.zh, stageText.en)}</span></td>
      <td class="pattern-cell">
        <div>${escapeHtml(item.wave_level || "")}${item.wave_level ? " · " : ""}${escapeHtml(item.pattern || "")}</div>
        <div class="en-copy">${escapeHtml(levelEn)}${levelEn ? " · " : ""}${escapeHtml(patternEn)}</div>
        <div class="position-label${invalidClass}">${bilingualHtml(item.position_label || "", positionEn)}</div>
        <div class="muted">${bilingualHtml(confirmation, confirmationTranslation(confirmation))}</div>
      </td>
      <td>
        <div class="score">${fmt(item.recommend_score)}<small>${escapeHtml(item.recommend_label || "")}<span class="en-inline">${escapeHtml(recommendationEn)}</span></small></div>
        <div class="score-breakdown">结构 ${fmt(item.structure_score)} · 位置 ${fmt(item.position_score)} · 确认 ${fmt(item.confirmation_score)}<small>Structure ${fmt(item.structure_score)} · Position ${fmt(item.position_score)} · Confirmation ${fmt(item.confirmation_score)}</small></div>
        <div class="market-context ${number(item.market_adjustment) < 0 ? "weak" : ""}">${escapeHtml(item.market_context_label || "")}${number(item.market_adjustment) < 0 ? ` ${fmt(item.market_adjustment)}` : ""}<small>${escapeHtml(marketContextEnglish(item.market_context_label))}</small></div>
      </td>
      <td>${fmt(item.last_close)}<div class="muted">${escapeHtml(item.last_date || "")}</div></td>
      <td>${fmt(item.support)}<div class="muted">距离 ${pct(item.distance_to_support, true)}<small class="en-copy">From support</small></div></td>
      <td>${fmt(item.invalid_below)}</td>
      <td>${targets.map(fmt).join(" / ") || "-"}</td>
      <td class="${number(item.target_1_upside) >= 0 ? "positive" : "negative"}">${pct(item.target_1_upside, true)}<div class="muted">盈亏比 ${fmt(item.risk_reward)}<small class="en-copy">Reward / risk</small></div></td>
    </tr>`;
}

function candidateCard(item) {
  const invalidClass = item.position_label === "低于失效位" ? " invalid" : "";
  const stage = ["trigger", "probe"].includes(item.signal_stage) ? item.signal_stage : "watch";
  const stageText = stageCopy(item);
  const recommendationEn = recommendationEnglish(item.recommend_label);
  const alignmentZh = item.multi_level_alignment ? " · 双级别共振" : "";
  const alignmentEn = item.multi_level_alignment ? " · Multi-timeframe alignment" : "";
  const confirmation = item.confirmation_detail || "等待右侧确认";
  return `
    <article class="candidate-card">
      <div class="card-head">
        <span class="badge ${escapeHtml(String(item.market || "").toLowerCase())}">${bilingualHtml(marketLabel(item.market), marketEnglish(item.market))}</span>
        <div class="card-symbol">
          <strong>${escapeHtml(item.monitor_symbol || item.symbol)}</strong>
          <small>${escapeHtml(item.name || "")}</small>
        </div>
        <div class="card-score">${fmt(item.recommend_score)}<small>${escapeHtml(item.recommend_label || "")} · ${escapeHtml(recommendationEn)}</small></div>
      </div>
      <div class="card-stage-row">
        <span class="stage-badge ${stage}">${bilingualHtml(stageText.zh, stageText.en)}</span>
        <span>${escapeHtml(item.wave_level || "")}${alignmentZh}<small class="en-copy">${escapeHtml(waveLevelEnglish(item.wave_level))}${alignmentEn}</small></span>
      </div>
      <div class="card-pattern">
        <div>${escapeHtml(item.pattern || "")} · <span class="position-label${invalidClass}">${escapeHtml(item.position_label || "")}</span></div>
        <div class="en-copy">${escapeHtml(patternEnglish[item.pattern] || item.pattern || "")} · ${escapeHtml(positionEnglish[item.position_label] || item.position_label || "")}</div>
        <div class="muted">${bilingualHtml(confirmation, confirmationTranslation(confirmation))}</div>
      </div>
      <div class="price-grid">
        <div><span>最新价<small>Last</small></span><strong>${fmt(item.last_close)}</strong></div>
        <div><span>支撑<small>Support</small></span><strong>${fmt(item.support)}</strong></div>
        <div><span>失效<small>Invalid</small></span><strong>${fmt(item.invalid_below)}</strong></div>
        <div><span>目标<small>Target</small></span><strong>${fmt(item.target_1)}</strong></div>
      </div>
      <div class="card-foot">
        <span>结构 ${fmt(item.structure_score)} · 位置 ${fmt(item.position_score)} · 确认 ${fmt(item.confirmation_score)}<small>Structure · Position · Confirmation</small></span>
        <span>盈亏比 ${fmt(item.risk_reward)} · ${escapeHtml(item.market_context_label || "")}<small>Reward / risk · ${escapeHtml(marketContextEnglish(item.market_context_label))}</small></span>
      </div>
    </article>`;
}

function indexCard(item) {
  const statusClass = number(item.score) >= 80 ? "strong" : number(item.score) >= 60 ? "constructive" : number(item.score) >= 40 ? "mixed" : "weak";
  const aboveMa20 = number(item.last_close) !== null && number(item.ma20) !== null && number(item.last_close) >= number(item.ma20);
  return `
    <article class="index-card ${statusClass}">
      <div class="index-card-head">
        <div>
          <strong>${escapeHtml(item.name || item.symbol)}</strong>
          <small>${escapeHtml(indexNameEnglish(item.name))} · ${escapeHtml(item.symbol || "")}</small>
        </div>
        <span class="index-status">${escapeHtml(item.status || "-")}<small>${escapeHtml(indexStatusEnglish(item.status))}</small></span>
      </div>
      <div class="index-price">
        <strong>${fmt(item.last_close)}</strong>
        <span class="${number(item.change_1d) >= 0 ? "positive" : "negative"}">${pct(item.change_1d, true)}<small>1 day</small></span>
      </div>
      <div class="index-foot">
        <span>5日 ${pct(item.change_5d, true)}<small>5 days</small></span>
        <span>${aboveMa20 ? "高于" : "低于"} MA20<small>${aboveMa20 ? "Above" : "Below"} MA20</small></span>
        <span>环境分 ${fmt(item.score)}<small>Context score</small></span>
      </div>
    </article>`;
}

function renderIndices(items) {
  if (!items.length) {
    els.indexCards.innerHTML = `<div class="index-empty">${bilingualHtml("暂未取得指数数据", "Index data is currently unavailable")}</div>`;
    return;
  }
  els.indexCards.innerHTML = items.map(indexCard).join("");
}

function historyMetric(item) {
  const sampleCount = Number(item.sample_count || 0);
  const hasSamples = sampleCount > 0;
  return `
    <article class="history-metric${hasSamples ? "" : " waiting"}">
      <div class="history-period">
        <strong>${escapeHtml(item.label || "-")}</strong>
        <small>${escapeHtml(item.label_en || "")} · ${Number(item.sessions || 0)} sessions</small>
      </div>
      <div class="history-result">
        <div><span>胜率<small>Win rate</small></span><strong>${hasSamples ? pct(item.win_rate) : "-"}</strong></div>
        <div><span>平均收益<small>Avg return</small></span><strong class="${returnClass(item.average_return)}">${hasSamples ? pct(item.average_return, true) : "-"}</strong></div>
      </div>
      <p>${hasSamples ? `${sampleCount} 笔成熟样本 · 中位 ${pct(item.median_return, true)}` : "样本积累中"}<small>${hasSamples ? `${sampleCount} mature samples · Median ${pct(item.median_return, true)}` : "Collecting forward samples"}</small></p>
    </article>`;
}

function outcomeReturn(signal, key) {
  const outcome = signal.outcomes && signal.outcomes[key];
  return outcome ? number(outcome.return) : null;
}

function historyOutcomeCell(signal, key) {
  const value = outcomeReturn(signal, key);
  if (value === null) return `<span class="pending-return">积累中<small>Pending</small></span>`;
  const outcome = signal.outcomes[key];
  return `<span class="history-return ${returnClass(value)}">${pct(value, true)}<small>${escapeHtml(outcome.date || "")}</small></span>`;
}

function historyTableRow(signal) {
  const stageText = stageCopy({ signal_stage: signal.entry_stage });
  return `
    <tr>
      <td>${escapeHtml(signal.entry_date || "-")}<div class="muted">${bilingualHtml(stageText.zh, stageText.en)}</div></td>
      <td><span class="badge ${escapeHtml(String(signal.market || "").toLowerCase())}">${bilingualHtml(marketLabel(signal.market), marketEnglish(signal.market))}</span></td>
      <td><div class="symbol"><strong>${escapeHtml(signal.monitor_symbol || signal.symbol || "-")}</strong><small>${escapeHtml(signal.name || "")}</small></div></td>
      <td>${fmt(signal.entry_price)} / ${fmt(signal.latest_price)}<div class="muted">${escapeHtml(signal.latest_date || "")}</div></td>
      <td><div class="score compact">${fmt(signal.entry_score)}<small>${escapeHtml(signal.entry_pattern || "")}</small></div></td>
      <td><span class="history-return ${returnClass(signal.current_return)}">${pct(signal.current_return, true)}</span></td>
      <td>${historyOutcomeCell(signal, "d5")}</td>
      <td>${historyOutcomeCell(signal, "d21")}</td>
      <td>${historyOutcomeCell(signal, "d63")}</td>
      <td>${historyOutcomeCell(signal, "d126")}</td>
    </tr>`;
}

function compactOutcome(signal, key, zh, en) {
  const value = outcomeReturn(signal, key);
  return `<div><span>${escapeHtml(zh)}<small>${escapeHtml(en)}</small></span><strong class="${returnClass(value)}">${value === null ? "-" : pct(value, true)}</strong></div>`;
}

function historyCard(signal) {
  const stageText = stageCopy({ signal_stage: signal.entry_stage });
  return `
    <article class="history-signal-card">
      <div class="history-signal-head">
        <span class="badge ${escapeHtml(String(signal.market || "").toLowerCase())}">${bilingualHtml(marketLabel(signal.market), marketEnglish(signal.market))}</span>
        <div><strong>${escapeHtml(signal.monitor_symbol || signal.symbol || "-")}</strong><small>${escapeHtml(signal.name || "")}</small></div>
        <div class="card-score">${fmt(signal.entry_score)}<small>${escapeHtml(stageText.zh)} · ${escapeHtml(stageText.en)}</small></div>
      </div>
      <div class="history-price-line">
        <span>入场 ${escapeHtml(signal.entry_date || "-")} · ${fmt(signal.entry_price)}<small>Entry</small></span>
        <span>最新 ${fmt(signal.latest_price)} · <strong class="${returnClass(signal.current_return)}">${pct(signal.current_return, true)}</strong><small>Latest · Current return</small></span>
      </div>
      <div class="history-return-grid">
        ${compactOutcome(signal, "d5", "一周", "1 week")}
        ${compactOutcome(signal, "d21", "一月", "1 month")}
        ${compactOutcome(signal, "d63", "三月", "3 months")}
        ${compactOutcome(signal, "d126", "六月", "6 months")}
      </div>
    </article>`;
}

function renderHistory(summary) {
  state.history = summary;
  const horizons = Array.isArray(summary.horizons) ? summary.horizons : [];
  const signals = Array.isArray(summary.signals) ? summary.signals : [];
  const snapshots = Array.isArray(summary.snapshots) ? summary.snapshots : [];
  els.historyMetrics.innerHTML = horizons.length
    ? horizons.map(historyMetric).join("")
    : `<div class="history-empty">${bilingualHtml("历史样本开始积累后显示", "Metrics appear as forward samples mature")}</div>`;

  const since = summary.tracking_since ? ` · 自 ${summary.tracking_since}` : "";
  setBilingual(
    els.historyMeta,
    `${Number(summary.snapshot_day_count || 0)} 天快照 · ${Number(summary.signal_count || 0)} 笔独立信号${since}`,
    `${Number(summary.snapshot_day_count || 0)} snapshot days · ${Number(summary.signal_count || 0)} independent signals`
  );
  setBilingual(els.historySignalCount, `${signals.length} 笔`, `${signals.length} signals`);
  setBilingual(els.snapshotCount, `${snapshots.length} 天`, `${snapshots.length} days`);

  if (signals.length) {
    els.historyRows.innerHTML = signals.map(historyTableRow).join("");
    els.historyCards.innerHTML = signals.map(historyCard).join("");
  } else {
    const empty = bilingualHtml("尚无历史信号", "No tracked signals yet");
    els.historyRows.innerHTML = `<tr><td class="empty" colspan="10">${empty}</td></tr>`;
    els.historyCards.innerHTML = `<div class="history-empty">${empty}</div>`;
  }

  if (!snapshots.length) {
    els.snapshotSelect.disabled = true;
    els.snapshotSelect.innerHTML = `<option value="">暂无快照 / No snapshots</option>`;
    return;
  }
  els.snapshotSelect.disabled = false;
  els.snapshotSelect.innerHTML = snapshots
    .map((item) => `<option value="${escapeHtml(item.path || "")}">${escapeHtml(item.date || "-")} · ${Number(item.recommendation_count || 0)} 条</option>`)
    .join("");
  loadSnapshot(els.snapshotSelect.value);
}

function snapshotRecommendation(item) {
  const stageText = stageCopy(item);
  return `
    <div class="snapshot-item">
      <span class="badge ${escapeHtml(String(item.market || "").toLowerCase())}">${escapeHtml(marketLabel(item.market))}</span>
      <div><strong>${escapeHtml(item.monitor_symbol || item.symbol || "-")}</strong><small>${escapeHtml(item.name || "")}</small></div>
      <span>${fmt(item.last_close)}<small>Entry</small></span>
      <span class="snapshot-score">${fmt(item.recommend_score)}<small>${escapeHtml(stageText.zh)}</small></span>
    </div>`;
}

function renderSnapshot(snapshot) {
  const runs = Array.isArray(snapshot.runs) ? snapshot.runs : [];
  setBilingual(
    els.snapshotMeta,
    `${escapeHtml(snapshot.date || "-")} · ${runs.length} 次扫描记录`,
    `${runs.length} archived scan${runs.length === 1 ? "" : "s"}`
  );
  if (!runs.length) {
    els.snapshotRuns.innerHTML = `<div class="history-empty">${bilingualHtml("当天没有快照记录", "No archived runs for this date")}</div>`;
    return;
  }
  els.snapshotRuns.innerHTML = runs
    .slice()
    .reverse()
    .map((run) => {
      const recommendations = Array.isArray(run.recommendations) ? run.recommendations : [];
      return `
        <section class="snapshot-run">
          <div class="snapshot-run-head">
            <strong>${dateTime(run.published_at)}</strong>
            <span>${recommendations.length} 条<small>${recommendations.length} entries</small></span>
          </div>
          <div class="snapshot-list">
            ${recommendations.length ? recommendations.map(snapshotRecommendation).join("") : `<div class="snapshot-empty">${bilingualHtml("本次没有85分以上买入提醒", "No 85+ entry alerts in this run")}</div>`}
          </div>
        </section>`;
    })
    .join("");
}

async function loadSnapshot(path) {
  if (!path) return;
  try {
    const response = await fetch(`${path}?v=${Date.now()}`, { cache: "no-store" });
    if (!response.ok) throw new Error(`HTTP ${response.status}`);
    renderSnapshot(await response.json());
  } catch (error) {
    els.snapshotRuns.innerHTML = `<div class="history-empty">${bilingualHtml("快照读取失败", "Unable to load this snapshot")}</div>`;
    console.error(error);
  }
}

async function loadHistory() {
  try {
    const response = await fetch(`./data/history/summary.json?v=${Date.now()}`, { cache: "no-store" });
    if (!response.ok) throw new Error(`HTTP ${response.status}`);
    renderHistory(await response.json());
  } catch (error) {
    setBilingual(els.historyMeta, "历史数据暂不可用", "History unavailable");
    els.historyMetrics.innerHTML = `<div class="history-empty">${bilingualHtml("暂时无法读取历史统计", "Unable to load track record")}</div>`;
    console.error(error);
  }
}

function render() {
  const rows = filteredRows();
  const alerts = rows.filter((item) => ["trigger", "probe"].includes(item.signal_stage)).length;
  const priority = rows.filter((item) => number(item.recommend_score) >= 85).length;
  const primaryRows = rows.filter((item) => number(item.recommend_score) >= 85);
  const otherRows = rows.filter((item) => number(item.recommend_score) < 85);
  els.candidateCount.textContent = rows.length.toLocaleString("zh-CN");
  els.alertCount.textContent = alerts.toLocaleString("zh-CN");
  els.priorityCount.textContent = priority.toLocaleString("zh-CN");
  setBilingual(els.resultCount, `${rows.length} 条`, `${rows.length} results`);

  if (!rows.length) {
    const empty = bilingualHtml("没有符合当前筛选条件的候选。", "No candidates match the current filters.");
    els.candidateRows.innerHTML = `<tr><td class="empty" colspan="10">${empty}</td></tr>`;
    els.candidateCards.innerHTML = `<div class="empty">${empty}</div>`;
    els.otherCandidates.hidden = true;
    return;
  }

  if (primaryRows.length) {
    els.candidateRows.innerHTML = primaryRows.map(tableRow).join("");
    els.candidateCards.innerHTML = primaryRows.map(candidateCard).join("");
  } else {
    const noPriority = bilingualHtml("暂无85分以上候选。", "No candidates currently score 85 or above.");
    els.candidateRows.innerHTML = `<tr><td class="empty" colspan="10">${noPriority}</td></tr>`;
    els.candidateCards.innerHTML = `<div class="empty">${noPriority}</div>`;
  }

  els.otherCandidates.hidden = !otherRows.length;
  if (otherRows.length) {
    setBilingual(els.otherCount, `${otherRows.length} 条`, `${otherRows.length} results`);
    els.otherRows.innerHTML = otherRows.map(tableRow).join("");
    els.otherCards.innerHTML = otherRows.map(candidateCard).join("");
  }
}

function renderMetadata(payload) {
  const meta = payload.metadata || {};
  const markets = Object.values(meta.market_counts || {}).filter((count) => Number(count) > 0).length;
  renderIndices(Array.isArray(meta.index_snapshots) ? meta.index_snapshots : []);
  els.instrumentCount.textContent = Number(meta.instrument_count || 0).toLocaleString("zh-CN");
  els.marketCount.textContent = markets || "-";
  els.failureRate.textContent = pct(meta.failure_rate);
  setBilingual(
    els.disclaimer,
    "仅供研究参考，不构成投资建议；波浪识别具有主观性。",
    "For research only. Not investment advice; Elliott Wave interpretation is subjective."
  );

  if (!payload.published_at) {
    setBilingual(els.candidateMeta, "等待首次全市场扫描", "Waiting for the first full-market scan");
    els.publishState.classList.remove("stale", "error");
    setBilingual(els.publishStateText, "等待首次扫描", "Awaiting first scan");
    return;
  }

  const detailsZh = [
    `更新于 ${dateTime(payload.published_at)}`,
    `右侧 ${meta.trigger_count || 0} 条`,
    `左侧 ${meta.probe_count || 0} 条`,
    `观察 ${meta.watch_count || 0} 条`,
  ];
  const detailsEn = [
    `Updated ${dateTime(payload.published_at)}`,
    `${meta.trigger_count || 0} confirmed`,
    `${meta.probe_count || 0} early probes`,
    `${meta.watch_count || 0} watchlist`,
  ];
  if (meta.duration_seconds !== null && meta.duration_seconds !== undefined) {
    detailsZh.push(`耗时 ${Math.round(meta.duration_seconds)} 秒`);
    detailsEn.push(`${Math.round(meta.duration_seconds)} sec`);
  }
  setBilingual(els.candidateMeta, detailsZh.join(" · "), detailsEn.join(" · "));

  els.publishState.classList.toggle("stale", isStale(payload.published_at));
  if (isStale(payload.published_at)) {
    setBilingual(els.publishStateText, "等待下次更新", "Awaiting update");
  } else {
    setBilingual(els.publishStateText, `已更新 ${dateTime(payload.published_at)}`, `Updated ${dateTime(payload.published_at)}`);
  }
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
    setBilingual(els.publishStateText, "数据读取失败", "Data unavailable");
    setBilingual(els.candidateMeta, "暂时无法读取最新扫描结果", "The latest scan cannot be loaded right now");
    const errorMessage = bilingualHtml("数据读取失败，请稍后刷新。", "Unable to load data. Please refresh later.");
    els.candidateRows.innerHTML = `<tr><td class="empty" colspan="10">${errorMessage}</td></tr>`;
    els.candidateCards.innerHTML = `<div class="empty">${errorMessage}</div>`;
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
els.snapshotSelect.addEventListener("change", () => loadSnapshot(els.snapshotSelect.value));

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
loadHistory();
