// Trade Store manual test console. Vanilla JS, no dependencies.
// All user- and server-supplied text is inserted with textContent, never as HTML.
"use strict";

const EXAMPLE_TRADE = {
  trade_id: "T1",
  version: 1,
  counterparty_id: "CP-1",
  portfolio_id: "B1",
  maturity_date: "20/05/2030",
};

const state = {
  demoMode: false,
  scenarios: [],
  guidedDemo: null,
  loaded: null, // { scenario, text } of the last loaded preset, to compare outcomes
  results: [],
  busy: false,
};

// ---------------------------------------------------------------------------------------
// DOM helpers
// ---------------------------------------------------------------------------------------

const $ = (id) => document.getElementById(id);

/** Create an element whose content is plain text. */
function el(tag, text, className) {
  const node = document.createElement(tag);
  if (text !== undefined && text !== null) node.textContent = String(text);
  if (className) node.className = className;
  return node;
}

function showMessage(id, text, tone) {
  const node = $(id);
  node.textContent = text;
  node.classList.toggle("warning", tone === "warning");
  node.hidden = !text;
}

// ---------------------------------------------------------------------------------------
// Formatting
// ---------------------------------------------------------------------------------------

/** "2015-03-14" -> "14/03/2015" (the sample table's format). */
function isoToDisplay(iso) {
  const match = /^(\d{4})-(\d{2})-(\d{2})$/.exec(String(iso ?? ""));
  return match ? `${match[3]}/${match[2]}/${match[1]}` : String(iso ?? "");
}

function plural(count, word) {
  return `${count} ${word}${count === 1 ? "" : "s"}`;
}

function formatValidationIssue(issue) {
  const path = (issue.loc || []).filter((part) => part !== "body").join(".");
  return path ? `${path}: ${issue.msg}` : String(issue.msg);
}

/** Turn an error response into { code, message } for the Results table. */
function describeError(response) {
  const data = response.data;
  if (response.status === 422 && data && Array.isArray(data.detail)) {
    return { code: "", message: data.detail.map(formatValidationIssue).join("; ") };
  }
  if (data && typeof data === "object" && "error_code" in data) {
    return { code: data.error_code, message: data.message };
  }
  const text = typeof data === "string" ? data.trim().slice(0, 200) : "";
  return { code: "", message: text || `HTTP ${response.status}` };
}

// ---------------------------------------------------------------------------------------
// API helpers (relative URLs so the page works behind any host/port)
// ---------------------------------------------------------------------------------------

const api = {
  async request(method, url, body) {
    const options = { method, headers: { Accept: "application/json" } };
    if (body !== undefined) {
      options.headers["Content-Type"] = "application/json";
      options.body = JSON.stringify(body);
    }
    const response = await fetch(url, options);
    const type = response.headers.get("content-type") || "";
    let data = null;
    if (type.includes("application/json")) {
      data = await response.json().catch(() => null);
    } else {
      data = await response.text();
    }
    return { status: response.status, ok: response.ok, data };
  },
  uiConfig: () => api.request("GET", "/config/ui"),
  scenarios: () => api.request("GET", "/static/scenarios.json"),
  getClock: () => api.request("GET", "/admin/clock"),
  setClock: (iso) => api.request("PUT", "/admin/clock", { today: iso }),
  clearClock: () => api.request("DELETE", "/admin/clock"),
  submitTrade: (trade) => api.request("POST", "/trades", trade),
  listTrades: (expired) =>
    api.request("GET", expired ? `/trades?expired=${encodeURIComponent(expired)}` : "/trades"),
  sweep: () => api.request("POST", "/admin/expire-trades"),
  reset: () => api.request("DELETE", "/admin/trades"),
};

async function expectOk(promise, what) {
  const response = await promise;
  if (!response.ok) {
    throw new Error(`${what} failed (HTTP ${response.status}): ${describeError(response).message}`);
  }
  return response.data;
}

// ---------------------------------------------------------------------------------------
// Input parsing (nothing is sent if this throws)
// ---------------------------------------------------------------------------------------

function parseTrades(text) {
  if (!text.trim()) {
    throw new Error("The input is empty. Paste one trade object or an array of trades.");
  }
  let value;
  try {
    value = JSON.parse(text);
  } catch (err) {
    throw new Error(`Invalid JSON: ${err.message}`);
  }
  const trades = Array.isArray(value) ? value : [value];
  const bad = trades.findIndex((t) => t === null || typeof t !== "object" || Array.isArray(t));
  if (bad !== -1) {
    const where = Array.isArray(value) ? `Item ${bad + 1} of the array` : "The input";
    throw new Error(`${where} is not a JSON object. Each trade must look like {"trade_id": "T1", ...}.`);
  }
  return trades;
}

function showJsonError(message) {
  showMessage("json-error", message || "");
}

// ---------------------------------------------------------------------------------------
// Render functions
// ---------------------------------------------------------------------------------------

function renderClock(clock) {
  $("today-value").textContent = isoToDisplay(clock.today);
  const badge = $("today-badge");
  badge.textContent = clock.overridden ? "Simulated" : "Real";
  badge.classList.toggle("simulated", clock.overridden);
  $("date-input").value = clock.today;
}

function renderMode() {
  const demo = state.demoMode;
  $("date-bar").hidden = !demo;
  $("date-note").hidden = demo;
  $("reset-btn").hidden = !demo;
  $("guided-demo-btn").hidden = !demo;
  $("reset-before-label").hidden = !demo;
}

function renderScenarioOptions() {
  const select = $("scenario-select");
  select.replaceChildren();
  for (const scenario of state.scenarios) {
    const option = el("option", scenario.title);
    option.value = scenario.id;
    select.append(option);
  }
  if (state.demoMode && state.guidedDemo) {
    const option = el("option", state.guidedDemo.title);
    option.value = state.guidedDemo.id;
    select.append(option);
  }
  renderScenarioDescription();
}

function renderScenarioDescription() {
  const selected = selectedScenario();
  $("scenario-description").textContent = selected ? selected.description : "";
}

function outcomeBadge(row) {
  if (row.kind === "step") return el("span", "Step", "badge step");
  return row.accepted
    ? el("span", "Accepted", "badge accepted")
    : el("span", "Rejected", "badge rejected");
}

function renderResultRow(row, index) {
  const tr = el("tr");
  tr.append(
    el("td", index + 1, "num"),
    el("td", row.tradeId ?? ""),
    el("td", row.version ?? "", "num"),
  );
  const outcomeCell = el("td");
  outcomeCell.append(outcomeBadge(row));
  tr.append(
    outcomeCell,
    el("td", row.status ?? "", "num"),
    el("td", row.code ?? ""),
    el("td", row.message ?? "", "message-cell"),
  );
  return tr;
}

function renderResults() {
  const body = $("results-body");
  body.replaceChildren(...state.results.map(renderResultRow));
  if (state.results.length === 0) {
    const tr = el("tr", null, "empty");
    const td = el("td", "No submissions yet");
    td.colSpan = 7;
    tr.append(td);
    body.append(tr);
  }
}

/** Sample-table order: Trade Id ascending, then Version descending. */
function sortForStore(trades) {
  return [...trades].sort(
    (a, b) =>
      String(a.trade_id).localeCompare(String(b.trade_id), undefined, { numeric: true }) ||
      b.version - a.version,
  );
}

function renderStore(trades, filter) {
  const body = $("store-body");
  const rows = sortForStore(trades).map((t) => {
    const tr = el("tr", null, t.expired ? "expired" : "");
    tr.append(
      el("td", t.trade_id),
      el("td", t.version, "num"),
      el("td", t.counterparty_id),
      el("td", t.portfolio_id),
      el("td", isoToDisplay(t.maturity_date), "num"),
      el("td", isoToDisplay(t.created_date), "num"),
      el("td", t.expired ? "Y" : "N"),
    );
    if (t.expired) tr.title = "Expired: maturity date has passed";
    return tr;
  });
  body.replaceChildren(...rows);
  if (rows.length === 0) {
    const tr = el("tr", null, "empty");
    const td = el("td", "No trades stored");
    td.colSpan = 7;
    tr.append(td);
    body.append(tr);
  }
  const label = { true: ", expired only", false: ", active only" }[filter] || "";
  $("store-count").textContent = `(${plural(trades.length, "trade")}${label})`;
}

// ---------------------------------------------------------------------------------------
// Results log
// ---------------------------------------------------------------------------------------

function clearResults() {
  state.results = [];
  showMessage("results-summary", "");
  renderResults();
}

function addResult(row) {
  state.results.push(row);
  $("results-body").append(renderResultRow(row, state.results.length - 1));
  if (state.results.length === 1) renderResults();
}

function addStep(message) {
  addResult({ kind: "step", tradeId: "–", version: "–", status: "", code: "", message });
}

// ---------------------------------------------------------------------------------------
// Actions
// ---------------------------------------------------------------------------------------

/** Run an async action with buttons disabled and the loading indicator shown. */
async function withBusy(task) {
  if (state.busy) return;
  setBusy(true);
  try {
    await task();
  } catch (err) {
    showMessage("store-message", err.message, "warning");
  } finally {
    setBusy(false);
  }
}

function setBusy(on) {
  state.busy = on;
  $("busy").hidden = !on;
  for (const control of document.querySelectorAll("button, select, textarea, input")) {
    control.disabled = on;
  }
}

async function refreshStore() {
  const filter = $("store-filter").value;
  const trades = await expectOk(api.listTrades(filter), "Loading trades");
  renderStore(trades, filter);
}

async function loadClock() {
  renderClock(await expectOk(api.getClock(), "Reading the clock"));
}

async function setClock(iso) {
  renderClock(await expectOk(api.setClock(iso), "Setting the date"));
}

async function clearClock() {
  renderClock(await expectOk(api.clearClock(), "Restoring the real date"));
}

async function resetStore() {
  const result = await expectOk(api.reset(), "Resetting the store");
  return result.deleted_count;
}

async function runSweep() {
  const result = await expectOk(api.sweep(), "Expiry sweep");
  const text =
    `Sweep run for ${isoToDisplay(result.run_date)}: ` +
    `${plural(result.expired_count, "trade")} marked expired.`;
  showMessage("store-message", text);
  await refreshStore();
  return { ...result, text };
}

/** POST one trade and log the outcome. Returns { status, error_code? }. */
async function submitOne(trade) {
  let response;
  try {
    response = await api.submitTrade(trade);
  } catch (err) {
    addResult({
      accepted: false,
      tradeId: trade.trade_id,
      version: trade.version,
      status: "–",
      code: "",
      message: `Network error: ${err.message}`,
    });
    return { status: 0 };
  }
  if (response.status === 201) {
    const t = response.data;
    addResult({
      accepted: true,
      tradeId: t.trade_id,
      version: t.version,
      status: 201,
      code: "",
      message: `Stored. Created ${isoToDisplay(t.created_date)}, Expired ${t.expired ? "Y" : "N"}.`,
    });
    return { status: 201 };
  }
  const error = describeError(response);
  addResult({
    accepted: false,
    tradeId: typeof trade.trade_id === "string" ? trade.trade_id : JSON.stringify(trade.trade_id),
    version: trade.version === undefined ? "" : JSON.stringify(trade.version),
    status: response.status,
    code: error.code,
    message: error.message,
  });
  return error.code ? { status: response.status, error_code: error.code } : { status: response.status };
}

/** Submit trades sequentially, in array order; never stop on a rejection. */
async function submitTrades(trades) {
  clearResults();
  showMessage("store-message", "");
  if (state.demoMode && $("reset-before").checked) {
    addStep(`Store reset before submitting: ${plural(await resetStore(), "trade")} deleted.`);
    $("reset-before").checked = false;
  }
  const outcomes = [];
  for (const trade of trades) {
    outcomes.push(await submitOne(trade));
  }
  await refreshStore();
  showMessage("results-summary", summarise(outcomes));
}

function summarise(outcomes) {
  if (outcomes.length === 0) {
    return "Nothing to submit (empty array).";
  }
  const accepted = outcomes.filter((o) => o.status === 201).length;
  let text =
    `${plural(outcomes.length, "trade")} submitted: ` +
    `${accepted} accepted, ${outcomes.length - accepted} rejected.`;
  const loaded = state.loaded;
  if (loaded && loaded.text === $("json-input").value) {
    const matches = JSON.stringify(outcomes) === JSON.stringify(loaded.scenario.expected);
    text += matches
      ? " This matches the scenario's expected outcome."
      : " This differs from the scenario's expected outcome (was the store or date changed?).";
  }
  return text;
}

// ---------------------------------------------------------------------------------------
// Scenarios
// ---------------------------------------------------------------------------------------

function selectedScenario() {
  const id = $("scenario-select").value;
  if (state.guidedDemo && id === state.guidedDemo.id) return state.guidedDemo;
  return state.scenarios.find((s) => s.id === id) || null;
}

async function loadScenario() {
  const scenario = selectedScenario();
  if (!scenario) return;
  showMessage("scenario-note", "");
  if (scenario === state.guidedDemo) {
    showMessage("scenario-note", 'Click "Run guided demo" to run every step automatically.');
    $("guided-demo-btn").focus();
    return;
  }
  const text = JSON.stringify(scenario.trades, null, 2);
  $("json-input").value = text;
  state.loaded = { scenario, text };
  showJsonError("");

  if (state.demoMode) {
    if (scenario.date === null) await clearClock();
    else await setClock(scenario.date);
    $("reset-before").checked = Boolean(scenario.reset);
  } else if (scenario.date !== null) {
    showMessage(
      "scenario-note",
      `Demo mode is off, so the date ${isoToDisplay(scenario.date)} could not be simulated. ` +
        "Rules use the real date, so results will differ from the description.",
      "warning",
    );
  }
}

async function runGuidedDemo() {
  const demo = state.guidedDemo;
  clearResults();
  showMessage("store-message", "");
  for (const step of demo.steps) {
    if (step.action === "reset") {
      addStep(`Store reset: ${plural(await resetStore(), "trade")} deleted.`);
    } else if (step.action === "set_date") {
      await setClock(step.date);
      addStep(`Simulated date set to ${isoToDisplay(step.date)}.`);
    } else if (step.action === "submit") {
      await submitOne(step.trade);
    } else if (step.action === "sweep") {
      addStep((await runSweep()).text);
    }
    await refreshStore();
  }
  showMessage("results-summary", demo.final_message);
}

// ---------------------------------------------------------------------------------------
// Event wiring
// ---------------------------------------------------------------------------------------

function wireEvents() {
  $("submit-btn").addEventListener("click", () => {
    let trades;
    try {
      trades = parseTrades($("json-input").value);
    } catch (err) {
      showJsonError(err.message);
      return;
    }
    showJsonError("");
    withBusy(() => submitTrades(trades));
  });

  $("format-btn").addEventListener("click", () => {
    const text = $("json-input").value;
    try {
      const formatted = JSON.stringify(JSON.parse(text), null, 2);
      if (state.loaded && state.loaded.text === text) state.loaded.text = formatted;
      $("json-input").value = formatted;
      showJsonError("");
    } catch (err) {
      showJsonError(`Invalid JSON: ${err.message}`);
    }
  });

  $("clear-btn").addEventListener("click", () => {
    $("json-input").value = "";
    state.loaded = null;
    showJsonError("");
  });

  $("clear-results-btn").addEventListener("click", clearResults);
  $("scenario-select").addEventListener("change", renderScenarioDescription);
  $("load-scenario-btn").addEventListener("click", () => withBusy(loadScenario));
  $("guided-demo-btn").addEventListener("click", () => withBusy(runGuidedDemo));

  $("set-date-btn").addEventListener("click", () => {
    const value = $("date-input").value;
    if (!value) {
      showMessage("store-message", "Pick a date first.", "warning");
      return;
    }
    withBusy(() => setClock(value));
  });
  $("real-date-btn").addEventListener("click", () => withBusy(clearClock));

  $("refresh-btn").addEventListener("click", () => withBusy(refreshStore));
  $("store-filter").addEventListener("change", () => withBusy(refreshStore));
  $("sweep-btn").addEventListener("click", () => withBusy(runSweep));
  $("reset-btn").addEventListener("click", () => {
    if (!window.confirm("Delete every stored trade? This cannot be undone.")) return;
    withBusy(async () => {
      const deleted = await resetStore();
      showMessage("store-message", `Store reset: ${plural(deleted, "trade")} deleted.`);
      await refreshStore();
    });
  });
}

async function init() {
  wireEvents();
  $("json-input").value = JSON.stringify(EXAMPLE_TRADE, null, 2);
  renderResults();
  await withBusy(async () => {
    const [config, presets] = await Promise.all([api.uiConfig(), api.scenarios()]);
    state.demoMode = Boolean(config.ok && config.data && config.data.demo_mode);
    if (presets.ok && presets.data) {
      state.scenarios = presets.data.scenarios || [];
      state.guidedDemo = presets.data.guided_demo || null;
    }
    renderMode();
    renderScenarioOptions();
    if (state.demoMode) await loadClock();
    await refreshStore();
  });
}

document.addEventListener("DOMContentLoaded", init);
