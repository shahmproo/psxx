/**
 * PSX Real-Time Market Dashboard & Alert Engine
 * Frontend Controller: WebSockets, Web Audio Synth, Interactive Stock Table & Alert Management
 */

// Application State
const state = {
  stocks: {},            // Symbol -> StockQuote
  indices: {},           // Code -> IndexQuote
  summary: null,
  topMovers: { gainers: [], losers: [], volume_leaders: [] },
  alerts: [],
  alertHistory: [],
  settings: {
    telegram_bot_token: "",
    telegram_chat_id: "",
    telegram_enabled: false,
    poll_interval_seconds: 8,
    min_volume_gainers: 50000,
    sound_alerts_enabled: true
  },
  tableFilter: {
    search: "",
    sector: "ALL",
    filterType: "all",
    sortBy: "volume",
    direction: "desc"
  },
  ws: null,
  wsConnected: false,
  audioContext: null,
  soundMuted: false
};

// --- Web Audio API Chime Synthesizer ---
function playAlertChime() {
  if (state.soundMuted || !state.settings.sound_alerts_enabled) return;

  try {
    const AudioCtx = window.AudioContext || window.webkitAudioContext;
    if (!AudioCtx) return;

    if (!state.audioContext) {
      state.audioContext = new AudioCtx();
    }
    if (state.audioContext.state === "suspended") {
      state.audioContext.resume();
    }

    const ctx = state.audioContext;
    const now = ctx.currentTime;

    // Dual-tone harmonic chime (880Hz -> 1318.5Hz - A5 to E6)
    const osc1 = ctx.createOscillator();
    const osc2 = ctx.createOscillator();
    const gain = ctx.createGain();

    osc1.type = "sine";
    osc2.type = "triangle";

    osc1.frequency.setValueAtTime(880, now);
    osc1.frequency.exponentialRampToValueAtTime(1318.5, now + 0.12);

    osc2.frequency.setValueAtTime(440, now);
    osc2.frequency.exponentialRampToValueAtTime(659.25, now + 0.15);

    gain.gain.setValueAtTime(0.01, now);
    gain.gain.linearRampToValueAtTime(0.3, now + 0.05);
    gain.gain.exponentialRampToValueAtTime(0.001, now + 0.65);

    osc1.connect(gain);
    osc2.connect(gain);
    gain.connect(ctx.destination);

    osc1.start(now);
    osc2.start(now);
    osc1.stop(now + 0.7);
    osc2.stop(now + 0.7);
  } catch (e) {
    console.warn("Audio chime could not be played:", e);
  }
}

// --- WebSocket Connection ---
function initWebSocket() {
  const protocol = window.location.protocol === "https:" ? "wss:" : "ws:";
  const wsUrl = `${protocol}//${window.location.host}/ws`;

  console.log("Connecting to WebSocket:", wsUrl);
  updateConnectionBadge(false);

  state.ws = new WebSocket(wsUrl);

  state.ws.onopen = () => {
    console.log("WebSocket connected.");
    state.wsConnected = true;
    updateConnectionBadge(true);
  };

  state.ws.onmessage = (event) => {
    try {
      const msg = JSON.parse(event.data);
      handleWebSocketMessage(msg);
    } catch (e) {
      console.error("Failed to parse WebSocket message:", e);
    }
  };

  state.ws.onclose = () => {
    console.warn("WebSocket disconnected. Reconnecting in 3s...");
    state.wsConnected = false;
    updateConnectionBadge(false);
    setTimeout(initWebSocket, 3000);
  };

  state.ws.onerror = (err) => {
    console.error("WebSocket error:", err);
    state.ws.close();
  };
}

function handleWebSocketMessage(msg) {
  // Pulse live sync indicator
  triggerSyncPulse();

  if (msg.type === "INITIAL_STATE") {
    state.summary = msg.summary;
    state.topMovers = msg.top_movers;
    if (msg.stocks) {
      msg.stocks.forEach(s => {
        state.stocks[s.symbol] = s;
      });
    }
    renderHeader();
    renderTopMovers();
    renderStockTable();
    populateSectorFilter();
  } else if (msg.type === "MARKET_TICK") {
    state.summary = msg.summary;
    state.topMovers = msg.top_movers;
    renderHeader();
    renderTopMovers();
    // Fetch refreshed stock list to preserve smooth table state
    loadStocks();
  } else if (msg.type === "ALERT_TRIGGERED") {
    console.warn("🚨 ALERT TRIGGERED:", msg.data);
    onAlertTriggered(msg.data);
  }
}

function updateConnectionBadge(connected) {
  const dot = document.getElementById("wsPulseDot");
  const text = document.getElementById("wsStatusText");
  if (!dot || !text) return;

  if (connected) {
    dot.className = "w-2.5 h-2.5 rounded-full bg-emerald-400 pulse-dot";
    text.textContent = "LIVE STREAM";
    text.className = "text-xs font-semibold text-emerald-400";
  } else {
    dot.className = "w-2.5 h-2.5 rounded-full bg-rose-500";
    text.textContent = "RECONNECTING";
    text.className = "text-xs font-semibold text-rose-400";
  }
}

function triggerSyncPulse() {
  const badge = document.getElementById("syncBadge");
  if (badge) {
    badge.classList.remove("text-cyan-400");
    badge.classList.add("text-emerald-300");
    setTimeout(() => {
      badge.classList.remove("text-emerald-300");
      badge.classList.add("text-cyan-400");
    }, 600);
  }
}

// --- Alert Handling & Notifications ---
function onAlertTriggered(data) {
  // 1. Play Web Audio synth chime
  playAlertChime();

  // 2. Render floating toast notification
  showToast(data);

  // 3. Refresh alert list and history log
  loadAlerts();
  loadAlertHistory();
}

function showToast(alertData) {
  const container = document.getElementById("toastContainer");
  if (!container) return;

  const toastId = "toast_" + Date.now();
  const toast = document.createElement("div");
  toast.id = toastId;
  toast.className = "toast-enter p-4 rounded-xl border border-amber-500/40 bg-slate-900/95 shadow-2xl backdrop-blur-md flex flex-col gap-2 pointer-events-auto min-w-[320px] max-w-sm";

  const isUp = alertData.change >= 0;
  const changeColor = isUp ? "text-emerald-400" : "text-rose-400";

  toast.innerHTML = `
    <div class="flex items-start justify-between">
      <div class="flex items-center gap-2">
        <span class="inline-flex items-center justify-center p-1.5 rounded-lg bg-amber-500/20 text-amber-400 text-sm">
          🚨
        </span>
        <div>
          <span class="font-bold text-white text-base">${alertData.symbol}</span>
          <span class="text-xs text-slate-400 block">${alertData.name || ""}</span>
        </div>
      </div>
      <button onclick="dismissToast('${toastId}')" class="text-slate-400 hover:text-white transition-colors text-lg leading-none">&times;</button>
    </div>
    <div class="text-sm text-slate-200 font-medium bg-slate-800/80 p-2.5 rounded-lg border border-slate-700/50">
      ${alertData.message}
    </div>
    <div class="flex items-center justify-between text-xs num-mono text-slate-300 pt-1">
      <div>
        LTP: <span class="font-bold text-white">PKR ${formatNum(alertData.current_price)}</span>
        <span class="${changeColor} font-semibold ml-1">(${isUp ? '+' : ''}${formatNum(alertData.change_pct)}%)</span>
      </div>
      <div class="text-slate-400">${alertData.triggered_at}</div>
    </div>
    ${alertData.telegram_sent ? '<div class="text-[11px] text-cyan-400 flex items-center gap-1">✈️ Sent to Telegram</div>' : ''}
  `;

  container.appendChild(toast);

  // Animate in
  requestAnimationFrame(() => {
    toast.classList.remove("toast-enter");
    toast.classList.add("toast-show");
  });

  // Auto-dismiss after 8 seconds
  setTimeout(() => {
    dismissToast(toastId);
  }, 8000);
}

function dismissToast(toastId) {
  const toast = document.getElementById(toastId);
  if (!toast) return;
  toast.classList.remove("toast-show");
  toast.classList.add("toast-leave");
  setTimeout(() => {
    if (toast.parentNode) toast.parentNode.removeChild(toast);
  }, 350);
}

// --- Header & Market Stats Rendering ---
function renderHeader() {
  if (!state.summary) return;
  const s = state.summary;

  // Status Badge
  const statusBadge = document.getElementById("marketStatusBadge");
  const statusReason = document.getElementById("marketStatusReason");
  if (statusBadge) {
    if (s.status === "OPEN") {
      statusBadge.className = "px-3 py-1 text-xs font-extrabold rounded-full bg-emerald-500/20 text-emerald-400 border border-emerald-500/40 flex items-center gap-1.5";
      statusBadge.innerHTML = '<span class="w-2 h-2 rounded-full bg-emerald-400 pulse-dot"></span> MARKET OPEN';
    } else if (s.status === "RECESS") {
      statusBadge.className = "px-3 py-1 text-xs font-extrabold rounded-full bg-amber-500/20 text-amber-400 border border-amber-500/40 flex items-center gap-1.5";
      statusBadge.innerHTML = '<span class="w-2 h-2 rounded-full bg-amber-400"></span> FRIDAY RECESS';
    } else {
      statusBadge.className = "px-3 py-1 text-xs font-extrabold rounded-full bg-rose-500/20 text-rose-400 border border-rose-500/40 flex items-center gap-1.5";
      statusBadge.innerHTML = '<span class="w-2 h-2 rounded-full bg-rose-400"></span> MARKET CLOSED';
    }
  }

  if (statusReason) {
    statusReason.textContent = `${s.status_reason} • ${s.next_session_info}`;
  }

  // PKT Time
  const timeEl = document.getElementById("pktTimeDisplay");
  if (timeEl) {
    timeEl.textContent = s.pkt_time;
  }

  // Last Sync
  const syncEl = document.getElementById("lastSyncDisplay");
  if (syncEl) {
    syncEl.textContent = s.last_sync || "Just now";
  }

  // KSE-100 Chip
  if (s.kse100) {
    const kseEl = document.getElementById("kse100Display");
    if (kseEl) {
      const isUp = s.kse100.change >= 0;
      const color = isUp ? "text-emerald-400" : "text-rose-400";
      const icon = isUp ? "▲" : "▼";
      kseEl.innerHTML = `
        <div class="text-xs text-slate-400 font-semibold uppercase">KSE-100 INDEX</div>
        <div class="flex items-baseline gap-2">
          <span class="text-xl font-bold num-mono text-white">${formatNum(s.kse100.current)}</span>
          <span class="text-xs font-bold num-mono ${color}">
            ${icon} ${isUp ? '+' : ''}${formatNum(s.kse100.change)} (${isUp ? '+' : ''}${formatNum(s.kse100.change_pct)}%)
          </span>
        </div>
      `;
    }
  }

  // ALLSHR Chip
  if (s.allshr) {
    const allEl = document.getElementById("allshrDisplay");
    if (allEl) {
      const isUp = s.allshr.change >= 0;
      const color = isUp ? "text-emerald-400" : "text-rose-400";
      allEl.innerHTML = `
        <div class="text-xs text-slate-400 font-semibold uppercase">ALL SHARE INDEX</div>
        <div class="flex items-baseline gap-2">
          <span class="text-xl font-bold num-mono text-white">${formatNum(s.allshr.current)}</span>
          <span class="text-xs font-bold num-mono ${color}">
            ${isUp ? '+' : ''}${formatNum(s.allshr.change_pct)}%
          </span>
        </div>
      `;
    }
  }

  // Total Market Volume
  const volEl = document.getElementById("totalVolDisplay");
  if (volEl) {
    volEl.textContent = formatVolumeCompact(s.total_volume);
  }

  // Market Breadth
  const total = (s.advancers + s.decliners + s.unchanged) || 1;
  const advPct = ((s.advancers / total) * 100).toFixed(1);
  const decPct = ((s.decliners / total) * 100).toFixed(1);
  const unchPct = ((s.unchanged / total) * 100).toFixed(1);

  const advEl = document.getElementById("advancersCount");
  const decEl = document.getElementById("declinersCount");
  const unchEl = document.getElementById("unchangedCount");
  const barAdv = document.getElementById("barAdvancers");
  const barDec = document.getElementById("barDecliners");
  const barUnch = document.getElementById("barUnchanged");

  if (advEl) advEl.textContent = `${s.advancers} (${advPct}%)`;
  if (decEl) decEl.textContent = `${s.decliners} (${decPct}%)`;
  if (unchEl) unchEl.textContent = `${s.unchanged} (${unchPct}%)`;

  if (barAdv) barAdv.style.width = `${advPct}%`;
  if (barDec) barDec.style.width = `${decPct}%`;
  if (barUnch) barUnch.style.width = `${unchPct}%`;
}

// --- Top Movers Rendering ---
function renderTopMovers() {
  const { gainers, losers, volume_leaders } = state.topMovers;

  renderMoverCards("topGainersList", gainers, "gainer");
  renderMoverCards("topLosersList", losers, "loser");
  renderMoverCards("volumeLeadersList", volume_leaders, "volume");
}

function renderMoverCards(containerId, list, type) {
  const container = document.getElementById(containerId);
  if (!container) return;

  if (!list || list.length === 0) {
    container.innerHTML = '<div class="text-center py-6 text-slate-500 text-xs">No active symbols currently</div>';
    return;
  }

  let html = "";
  list.slice(0, 3).forEach((stock, idx) => {
    const isUp = stock.change >= 0;
    const color = isUp ? "text-emerald-400" : "text-rose-400";
    const badgeColor = isUp ? "badge-up" : "badge-down";

    html += `
      <div onclick="openAlertModalFor('${stock.symbol}')"
           class="p-3 rounded-lg bg-slate-900/60 border border-slate-800/80 hover:border-cyan-500/50 hover:bg-slate-800/60 cursor-pointer transition-all flex items-center justify-between group">
        <div class="flex items-center gap-2.5">
          <span class="w-6 h-6 rounded-md bg-slate-800 text-slate-300 text-xs font-bold flex items-center justify-center border border-slate-700">
            ${idx + 1}
          </span>
          <div>
            <div class="font-bold text-white text-sm group-hover:text-cyan-400 transition-colors flex items-center gap-1.5">
              ${stock.symbol}
              <span class="text-[10px] text-slate-500 font-normal truncate max-w-[110px]">${stock.name}</span>
            </div>
            <div class="text-xs text-slate-400 num-mono flex items-center gap-2">
              <span>PKR ${formatNum(stock.current)}</span>
              <span class="text-slate-500">•</span>
              <span>Vol: ${formatVolumeCompact(stock.volume)}</span>
            </div>
          </div>
        </div>
        <div class="text-right">
          <span class="px-2 py-0.5 rounded text-xs font-bold num-mono ${badgeColor}">
            ${isUp ? '+' : ''}${formatNum(stock.change_pct)}%
          </span>
          <div class="text-[11px] ${color} num-mono mt-0.5 font-medium">
            ${isUp ? '+' : ''}${formatNum(stock.change)}
          </div>
        </div>
      </div>
    `;
  });

  container.innerHTML = html;
}

function setFilterType(type) {
  state.tableFilter.filterType = type;

  // Update tab buttons style
  const tabs = ["all", "gainers", "losers", "active"];
  tabs.forEach(t => {
    const btn = document.getElementById("filterTab_" + t);
    if (!btn) return;
    if (t === type) {
      btn.className = "px-2.5 py-1 rounded bg-slate-800 text-cyan-400 border border-slate-700 font-semibold transition-colors";
    } else {
      btn.className = "px-2.5 py-1 rounded text-slate-400 hover:text-white hover:bg-slate-800/60 font-semibold transition-colors";
    }
  });

  loadStocks();
}

async function loadSummary() {
  try {
    const res = await fetch("/api/market/summary");
    if (res.ok) {
      state.summary = await res.json();
      renderHeader();
    }
  } catch (e) {
    console.error("Error loading market summary:", e);
  }
}

async function loadTopMovers() {
  try {
    const res = await fetch("/api/market/top-movers");
    if (res.ok) {
      state.topMovers = await res.json();
      renderTopMovers();
    }
  } catch (e) {
    console.error("Error loading top movers:", e);
  }
}

// --- All Stocks Table Rendering & Search/Filter ---
async function loadStocks() {
  const { search, sector, filterType, sortBy, direction } = state.tableFilter;
  const url = `/api/market/stocks?search=${encodeURIComponent(search)}&sector=${encodeURIComponent(sector)}&filter_type=${filterType}&sort_by=${sortBy}&direction=${direction}&limit=250`;

  try {
    const res = await fetch(url);
    if (res.ok) {
      const data = await res.json();
      data.stocks.forEach(s => {
        state.stocks[s.symbol] = s;
      });
      renderStockTable(data.stocks, data.total);
    }
  } catch (e) {
    console.error("Error loading stocks:", e);
  }
}

function renderStockTable(stocksList, totalCount) {
  const tbody = document.getElementById("stocksTableBody");
  const countEl = document.getElementById("stocksTotalCount");
  if (!tbody) return;

  const list = stocksList || Object.values(state.stocks);
  if (countEl) countEl.textContent = `${totalCount || list.length} Listed Equities`;

  if (list.length === 0) {
    tbody.innerHTML = `
      <tr>
        <td colspan="9" class="text-center py-12 text-slate-500 text-sm">
          No matching stocks found for current filter.
        </td>
      </tr>
    `;
    return;
  }

  let html = "";
  list.forEach(stock => {
    const isUp = stock.change > 0;
    const isDown = stock.change < 0;
    const color = isUp ? "text-emerald-400" : isDown ? "text-rose-400" : "text-slate-400";
    const badge = isUp ? "badge-up" : isDown ? "badge-down" : "badge-neutral";

    html += `
      <tr class="border-b border-slate-800/60 hover:bg-slate-800/40 transition-colors group">
        <!-- Symbol & Name -->
        <td class="py-3 px-4">
          <div class="font-bold text-white text-sm group-hover:text-cyan-400 transition-colors">${stock.symbol}</div>
          <div class="text-xs text-slate-400 truncate max-w-[190px]" title="${stock.name}">${stock.name}</div>
        </td>
        <!-- Sector -->
        <td class="py-3 px-4 text-xs text-slate-400 font-medium truncate max-w-[140px]" title="${stock.sector}">
          ${stock.sector || "EQUITY"}
        </td>
        <!-- Current (LTP) -->
        <td class="py-3 px-4 text-right font-bold text-sm num-mono text-white" id="price_${stock.symbol}">
          PKR ${formatNum(stock.current)}
        </td>
        <!-- Net Change -->
        <td class="py-3 px-4 text-right text-xs num-mono font-semibold ${color}">
          ${isUp ? '+' : ''}${formatNum(stock.change)}
        </td>
        <!-- % Change -->
        <td class="py-3 px-4 text-right">
          <span class="px-2 py-0.5 rounded text-xs font-bold num-mono ${badge}">
            ${isUp ? '+' : ''}${formatNum(stock.change_pct)}%
          </span>
        </td>
        <!-- Day High / Low -->
        <td class="py-3 px-4 text-right text-xs num-mono text-slate-300">
          <span class="text-emerald-400 font-medium">${formatNum(stock.high)}</span>
          <span class="text-slate-600 mx-1">/</span>
          <span class="text-rose-400 font-medium">${formatNum(stock.low)}</span>
        </td>
        <!-- LDCP (Prev Close) -->
        <td class="py-3 px-4 text-right text-xs num-mono text-slate-400">
          ${formatNum(stock.ldcp)}
        </td>
        <!-- Traded Volume -->
        <td class="py-3 px-4 text-right text-xs num-mono font-semibold text-slate-200">
          ${formatVolumeFull(stock.volume)}
        </td>
        <!-- Action Button -->
        <td class="py-3 px-4 text-center">
          <button onclick="openAlertModalFor('${stock.symbol}')"
                  class="px-2.5 py-1 text-xs font-semibold rounded-md bg-cyan-500/10 text-cyan-400 border border-cyan-500/30 hover:bg-cyan-500 hover:text-white transition-all">
            🔔 Set Alert
          </button>
        </td>
      </tr>
    `;
  });

  tbody.innerHTML = html;
}

function populateSectorFilter() {
  const select = document.getElementById("sectorFilter");
  if (!select) return;

  const currentVal = select.value;
  const sectors = new Set();
  Object.values(state.stocks).forEach(s => {
    if (s.sector) sectors.add(s.sector);
  });

  const sorted = Array.from(sectors).sort();
  select.innerHTML = '<option value="ALL">All Sectors</option>';
  sorted.forEach(sec => {
    const opt = document.createElement("option");
    opt.value = sec;
    opt.textContent = sec;
    if (sec === currentVal) opt.selected = true;
    select.appendChild(opt);
  });
}

function setupTableControls() {
  const searchInput = document.getElementById("stockSearchInput");
  const sectorSelect = document.getElementById("sectorFilter");

  if (searchInput) {
    let timeout = null;
    searchInput.addEventListener("input", (e) => {
      clearTimeout(timeout);
      timeout = setTimeout(() => {
        state.tableFilter.search = e.target.value;
        loadStocks();
      }, 250);
    });
  }

  if (sectorSelect) {
    sectorSelect.addEventListener("change", (e) => {
      state.tableFilter.sector = e.target.value;
      loadStocks();
    });
  }

  // Header column sort click handlers
  document.querySelectorAll("[data-sort]").forEach(th => {
    th.addEventListener("click", () => {
      const col = th.getAttribute("data-sort");
      if (state.tableFilter.sortBy === col) {
        state.tableFilter.direction = state.tableFilter.direction === "asc" ? "desc" : "asc";
      } else {
        state.tableFilter.sortBy = col;
        state.tableFilter.direction = "desc";
      }
      updateSortIndicators();
      loadStocks();
    });
  });
}

function updateSortIndicators() {
  document.querySelectorAll("[data-sort]").forEach(th => {
    const col = th.getAttribute("data-sort");
    const indicator = th.querySelector(".sort-indicator");
    if (indicator) {
      if (state.tableFilter.sortBy === col) {
        indicator.textContent = state.tableFilter.direction === "asc" ? " ▲" : " ▼";
        indicator.className = "sort-indicator text-cyan-400 font-bold";
      } else {
        indicator.textContent = " ↕";
        indicator.className = "sort-indicator text-slate-600";
      }
    }
  });
}

// --- Alert Management Modal & CRUD ---
function openAlertModalFor(symbol) {
  const modal = document.getElementById("alertCreateModal");
  const symInput = document.getElementById("alertSymbolInput");
  const targetInput = document.getElementById("alertTargetInput");
  const priceDisplay = document.getElementById("alertCurrentPriceDisplay");

  if (symbol) {
    symInput.value = symbol.toUpperCase();
    const stock = state.stocks[symbol.toUpperCase()];
    if (stock) {
      targetInput.value = stock.current || "";
      if (priceDisplay) {
        priceDisplay.textContent = `Current LTP: PKR ${formatNum(stock.current)} | ${stock.name}`;
      }
    }
  } else {
    symInput.value = "";
    targetInput.value = "";
    if (priceDisplay) priceDisplay.textContent = "";
  }

  if (modal) modal.classList.remove("hidden");
}

function closeAlertModal() {
  const modal = document.getElementById("alertCreateModal");
  if (modal) modal.classList.add("hidden");
}

async function handleCreateAlert(e) {
  e.preventDefault();
  const symbol = document.getElementById("alertSymbolInput").value.trim().toUpperCase();
  const condType = document.getElementById("alertConditionType").value;
  const targetVal = parseFloat(document.getElementById("alertTargetInput").value);
  const triggerMode = document.getElementById("alertTriggerMode").value;
  const cooldown = parseInt(document.getElementById("alertCooldownInput").value) || 15;
  const notes = document.getElementById("alertNotesInput").value.trim();

  if (!symbol) {
    alert("Please enter a valid stock symbol (e.g. CNERGY, OGDC, LUCK)");
    return;
  }
  if (isNaN(targetVal) || targetVal <= 0) {
    alert("Please enter a valid target value greater than 0");
    return;
  }

  const payload = {
    symbol: symbol,
    condition_type: condType,
    target_value: targetVal,
    trigger_mode: triggerMode,
    cooldown_minutes: cooldown,
    notes: notes || null
  };

  try {
    const res = await fetch("/api/alerts", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify(payload)
    });
    if (res.ok) {
      closeAlertModal();
      loadAlerts();
      showToast({
        symbol: symbol,
        name: "Alert Created",
        message: `Alert set for ${symbol} (${condType} ${targetVal})`,
        current_price: targetVal,
        change: 0,
        change_pct: 0,
        triggered_at: "Just now",
        telegram_sent: false
      });
    } else {
      const err = await res.json();
      alert("Failed to create alert: " + (err.detail || "Server error"));
    }
  } catch (err) {
    console.error("Create alert error:", err);
    alert("Network error creating alert.");
  }
}

async function loadAlerts() {
  try {
    const res = await fetch("/api/alerts");
    if (res.ok) {
      state.alerts = await res.json();
      renderAlertsList();
    }
  } catch (e) {
    console.error("Failed to load alerts:", e);
  }
}

function renderAlertsList() {
  const container = document.getElementById("activeAlertsList");
  const countBadge = document.getElementById("alertsCountBadge");
  if (!container) return;

  if (countBadge) countBadge.textContent = state.alerts.length;

  if (state.alerts.length === 0) {
    container.innerHTML = `
      <div class="text-center py-10 text-slate-500">
        <div class="text-2xl mb-2">🔔</div>
        <p class="text-sm font-medium">No alerts currently set.</p>
        <p class="text-xs text-slate-600 mt-1">Click "Set Alert" on any stock to configure rules.</p>
      </div>
    `;
    return;
  }

  let html = "";
  state.alerts.forEach(a => {
    const isActive = a.is_active;
    const condLabel = formatConditionType(a.condition_type);

    html += `
      <div class="p-3.5 rounded-xl border ${isActive ? 'border-slate-700 bg-slate-900/80' : 'border-slate-800 bg-slate-950/40 opacity-70'} transition-all flex items-center justify-between gap-3">
        <div class="flex items-center gap-3">
          <button onclick="toggleAlert(${a.id})"
                  class="w-10 h-6 flex items-center rounded-full p-1 transition-colors duration-200 ease-in-out ${isActive ? 'bg-cyan-500' : 'bg-slate-700'}"
                  title="${isActive ? 'Click to Disable' : 'Click to Enable'}">
            <div class="bg-white w-4 h-4 rounded-full shadow-md transform transition-transform duration-200 ease-in-out ${isActive ? 'translate-x-4' : 'translate-x-0'}"></div>
          </button>
          <div>
            <div class="flex items-center gap-2">
              <span class="font-bold text-white text-sm">${a.symbol}</span>
              <span class="text-xs font-semibold px-2 py-0.5 rounded bg-cyan-500/10 text-cyan-400 border border-cyan-500/20">
                ${condLabel} ${formatNum(a.target_value)}
              </span>
              <span class="text-[10px] text-slate-500 uppercase px-1.5 py-0.5 rounded bg-slate-800 border border-slate-700">
                ${a.trigger_mode}
              </span>
            </div>
            <div class="text-xs text-slate-400 mt-1 flex items-center gap-3">
              <span>Cooldown: ${a.cooldown_minutes}m</span>
              <span>Fired: <strong class="text-slate-300">${a.trigger_count || 0} times</strong></span>
              ${a.last_triggered_at ? `<span class="text-amber-400/80">Last: ${formatDateShort(a.last_triggered_at)}</span>` : ''}
            </div>
            ${a.notes ? `<div class="text-[11px] text-slate-500 italic mt-0.5">"${a.notes}"</div>` : ''}
          </div>
        </div>
        <div class="flex items-center gap-2">
          <button onclick="deleteAlert(${a.id})"
                  class="p-2 rounded-lg text-slate-500 hover:text-rose-400 hover:bg-rose-500/10 transition-colors"
                  title="Delete Alert">
            🗑
          </button>
        </div>
      </div>
    `;
  });

  container.innerHTML = html;
}

async function toggleAlert(alertId) {
  try {
    const res = await fetch(`/api/alerts/${alertId}/toggle`, { method: "POST" });
    if (res.ok) {
      loadAlerts();
    }
  } catch (e) {
    console.error("Toggle alert error:", e);
  }
}

async function deleteAlert(alertId) {
  if (!confirm("Are you sure you want to delete this alert?")) return;
  try {
    const res = await fetch(`/api/alerts/${alertId}`, { method: "DELETE" });
    if (res.ok) {
      loadAlerts();
    }
  } catch (e) {
    console.error("Delete alert error:", e);
  }
}

async function loadAlertHistory() {
  const container = document.getElementById("alertHistoryList");
  if (!container) return;

  try {
    const res = await fetch("/api/alerts/history?limit=30");
    if (res.ok) {
      const history = await res.json();
      if (history.length === 0) {
        container.innerHTML = '<div class="text-center py-8 text-slate-500 text-xs">No alerts have fired yet.</div>';
        return;
      }

      let html = "";
      history.forEach(h => {
        html += `
          <div class="p-3 rounded-lg border border-slate-800 bg-slate-900/40 text-xs flex items-center justify-between">
            <div>
              <div class="flex items-center gap-2 font-semibold">
                <span class="text-white">${h.symbol}</span>
                <span class="text-slate-400 font-normal">${h.message}</span>
              </div>
              <div class="text-[11px] text-slate-500 mt-0.5">${formatDateShort(h.triggered_at)}</div>
            </div>
            <div class="text-right">
              ${h.telegram_sent
                ? '<span class="text-[10px] text-cyan-400 px-1.5 py-0.5 rounded bg-cyan-950/60 border border-cyan-800/40">✈️ Telegram Sent</span>'
                : '<span class="text-[10px] text-slate-500">Web Only</span>'}
            </div>
          </div>
        `;
      });
      container.innerHTML = html;
    }
  } catch (e) {
    console.error("Failed to load history:", e);
  }
}

// --- Settings & Telegram Management ---
async function loadSettings() {
  try {
    const res = await fetch("/api/settings");
    if (res.ok) {
      state.settings = await res.json();
      populateSettingsModal();
      updateTelegramPill();
    }
  } catch (e) {
    console.error("Error loading settings:", e);
  }
}

function populateSettingsModal() {
  const token = document.getElementById("settingTgToken");
  const chat = document.getElementById("settingTgChat");
  const enabled = document.getElementById("settingTgEnabled");
  const poll = document.getElementById("settingPollInterval");
  const minVol = document.getElementById("settingMinVolume");
  const sound = document.getElementById("settingSoundEnabled");

  if (token) token.value = state.settings.telegram_bot_token || "";
  if (chat) chat.value = state.settings.telegram_chat_id || "";
  if (enabled) enabled.checked = !!state.settings.telegram_enabled;
  if (poll) poll.value = state.settings.poll_interval_seconds || 8;
  if (minVol) minVol.value = state.settings.min_volume_gainers || 50000;
  if (sound) sound.checked = !!state.settings.sound_alerts_enabled;
}

function updateTelegramPill() {
  const pill = document.getElementById("telegramStatusPill");
  if (!pill) return;

  if (state.settings.telegram_enabled && state.settings.telegram_bot_token && state.settings.telegram_chat_id) {
    pill.className = "px-2.5 py-1 text-xs font-semibold rounded-full bg-cyan-500/10 text-cyan-400 border border-cyan-500/30 flex items-center gap-1.5 cursor-pointer hover:bg-cyan-500/20";
    pill.innerHTML = "✈️ Telegram: Connected";
  } else {
    pill.className = "px-2.5 py-1 text-xs font-semibold rounded-full bg-slate-800 text-slate-400 border border-slate-700 flex items-center gap-1.5 cursor-pointer hover:bg-slate-700";
    pill.innerHTML = "✈️ Telegram: Not Configured";
  }
}

async function handleSaveSettings(e) {
  e.preventDefault();
  const token = document.getElementById("settingTgToken").value.trim();
  const chat = document.getElementById("settingTgChat").value.trim();
  const enabled = document.getElementById("settingTgEnabled").checked;
  const poll = parseInt(document.getElementById("settingPollInterval").value) || 8;
  const minVol = parseInt(document.getElementById("settingMinVolume").value) || 50000;
  const sound = document.getElementById("settingSoundEnabled").checked;

  const payload = {
    telegram_bot_token: token,
    telegram_chat_id: chat,
    telegram_enabled: enabled,
    poll_interval_seconds: poll,
    min_volume_gainers: minVol,
    sound_alerts_enabled: sound
  };

  try {
    const res = await fetch("/api/settings", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify(payload)
    });
    if (res.ok) {
      state.settings = await res.json();
      updateTelegramPill();
      closeSettingsModal();
      alert("Settings saved successfully!");
    }
  } catch (err) {
    console.error("Save settings error:", err);
    alert("Failed to save settings.");
  }
}

async function testTelegramConnection() {
  const token = document.getElementById("settingTgToken").value.trim();
  const chat = document.getElementById("settingTgChat").value.trim();
  const btn = document.getElementById("testTgBtn");

  if (!token || !chat) {
    alert("Please enter both Bot Token and Chat ID to run the test.");
    return;
  }

  btn.disabled = true;
  btn.textContent = "Testing...";

  try {
    const res = await fetch("/api/telegram/test", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ bot_token: token, chat_id: chat })
    });
    const data = await res.json();
    if (res.ok) {
      alert("✅ " + data.message);
    } else {
      alert("❌ " + (data.detail || "Telegram test failed."));
    }
  } catch (e) {
    alert("Network error testing Telegram connection.");
  } finally {
    btn.disabled = false;
    btn.textContent = "Test Telegram Bot";
  }
}

function openSettingsModal() {
  populateSettingsModal();
  const modal = document.getElementById("settingsModal");
  if (modal) modal.classList.remove("hidden");
}

function closeSettingsModal() {
  const modal = document.getElementById("settingsModal");
  if (modal) modal.classList.add("hidden");
}

// --- Manual Refresh Trigger ---
async function triggerManualRefresh() {
  const btn = document.getElementById("manualRefreshBtn");
  if (btn) {
    btn.classList.add("animate-spin");
  }
  try {
    await fetch("/api/market/refresh", { method: "POST" });
    await loadStocks();
  } catch (e) {
    console.error("Manual refresh error:", e);
  } finally {
    if (btn) {
      setTimeout(() => btn.classList.remove("animate-spin"), 600);
    }
  }
}

// --- Utilities & Formatters ---
function formatNum(val) {
  if (val === null || val === undefined || isNaN(val)) return "0.00";
  return Number(val).toLocaleString("en-US", { minimumFractionDigits: 2, maximumFractionDigits: 2 });
}

function formatVolumeFull(val) {
  if (!val) return "0";
  return Number(val).toLocaleString("en-US");
}

function formatVolumeCompact(num) {
  if (!num) return "0";
  if (num >= 1e9) return (num / 1e9).toFixed(2) + " B";
  if (num >= 1e6) return (num / 1e6).toFixed(2) + " M";
  if (num >= 1e3) return (num / 1e3).toFixed(1) + " K";
  return num.toString();
}

function formatDateShort(isoStr) {
  if (!isoStr) return "";
  try {
    const d = new Date(isoStr);
    return d.toLocaleTimeString([], { hour: '2-digit', minute: '2-digit', second: '2-digit' });
  } catch (e) {
    return isoStr;
  }
}

function formatConditionType(cond) {
  switch (cond) {
    case "price_above": return "Price >=";
    case "price_below": return "Price <=";
    case "pct_change_above": return "Gain >=";
    case "pct_change_below": return "Drop <=";
    case "volume_above": return "Volume >=";
    default: return cond;
  }
}

// --- Initialization ---
document.addEventListener("DOMContentLoaded", () => {
  initWebSocket();
  setupTableControls();
  loadSummary();
  loadTopMovers();
  loadStocks();
  loadAlerts();
  loadAlertHistory();
  loadSettings();

  // Alert Form Submit
  const alertForm = document.getElementById("alertCreateForm");
  if (alertForm) alertForm.addEventListener("submit", handleCreateAlert);

  // Settings Form Submit
  const settingsForm = document.getElementById("settingsForm");
  if (settingsForm) settingsForm.addEventListener("submit", handleSaveSettings);

  // Symbol Input Auto-fill Price
  const symInput = document.getElementById("alertSymbolInput");
  if (symInput) {
    symInput.addEventListener("input", (e) => {
      const sym = e.target.value.trim().toUpperCase();
      const stock = state.stocks[sym];
      const targetInput = document.getElementById("alertTargetInput");
      const priceDisplay = document.getElementById("alertCurrentPriceDisplay");
      if (stock) {
        if (!targetInput.value) targetInput.value = stock.current;
        if (priceDisplay) priceDisplay.textContent = `Current LTP: PKR ${formatNum(stock.current)} | ${stock.name}`;
      } else {
        if (priceDisplay) priceDisplay.textContent = "";
      }
    });
  }

  // Periodic PKT clock update
  setInterval(() => {
    // If summary has a time string, we can let it advance or update
  }, 1000);
});
