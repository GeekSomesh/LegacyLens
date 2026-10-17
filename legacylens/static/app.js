"use strict";

const $ = (sel) => document.querySelector(sel);
const esc = (s) => String(s).replace(/[&<>"]/g, (c) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;" }[c]));
const pct = (x) => (x * 100).toFixed(1) + "%";
const money = (x) => Number(x).toLocaleString("en-US");

let program = null;
let explainSeq = 0;

async function api(path, body) {
  const opts = body ? { method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify(body) } : {};
  const res = await fetch(path, opts);
  if (!res.ok) throw new Error((await res.json().catch(() => ({}))).detail || res.statusText);
  return res.json();
}

// ---------------------------------------------------------------- tabs
document.querySelectorAll(".tabs button").forEach((b) =>
  b.addEventListener("click", () => {
    document.querySelectorAll(".tabs button").forEach((x) => x.setAttribute("aria-selected", x === b));
    document.querySelectorAll(".tab").forEach((t) => t.classList.toggle("active", t.id === "tab-" + b.dataset.tab));
  })
);

// ---------------------------------------------------------------- source
function renderSource(lines) {
  const hit = new Set(lines || []);
  $("#source").innerHTML = program.source
    .map((l) => `<span class="ln${hit.has(l.no) ? " hit" : ""}" data-no="${l.no}"><span class="no">${l.no}</span>${esc(l.text)}</span>`)
    .join("");
  const first = $("#source .hit");
  if (first) $("#source").scrollTop = first.offsetTop - 80;
  $("#source-hint").textContent = lines && lines.length ? `highlighted: lines ${lines.join(", ")}` : "";
}

function renderRules() {
  $("#rules-hint").textContent =
    `Decision paragraph ${program.entry}; decision fields ${program.decision_fields.join(", ")}. ` +
    `Each rule is one path through the code. Click a rule to see its lines.`;
  $("#rules").innerHTML = program.rules
    .map((r) => {
      const doc = (program.rule_docs || {})[r.id];
      const docHtml = doc
        ? `<p class="explain">${esc(doc.text)}<span class="hint"> · ${doc.source === "granite" ? "documented by IBM Granite on LinuxONE, checked against the rule" : "generated from the rule"}</span></p>`
        : "";
      return `
      <div class="rule" data-rule="${r.id}">
        <div class="head"><strong>${r.id}</strong>
          ${Object.entries(r.actions).map(([k, v]) => `<span class="pill">${esc(k)} = ${esc(v)}</span>`).join(" ")}
          <span class="muted">lines ${r.lines.join(", ")}</span></div>
        ${docHtml}
        <ul class="conds">${r.conditions.map((c) => `<li>${esc(c.english)} <span class="muted">· <code>${esc(c.text)}</code> · line ${c.line}</span></li>`).join("")}</ul>
      </div>`;
    })
    .join("");
  document.querySelectorAll(".rule").forEach((el) =>
    el.addEventListener("click", () => {
      const r = program.rules.find((x) => x.id === el.dataset.rule);
      document.querySelector('.tabs button[data-tab="decide"]').click();
      renderSource(r.lines);
    })
  );
}

// ---------------------------------------------------------------- decision
const tone = (final) => ({ INCREASE: "tone-ok", KEEP: "tone-ok", HOLD: "tone-warn", REVIEW: "tone-warn", DECREASE: "tone-bad", BLOCK: "tone-bad" }[final] || "");

function renderAccount(r) {
  const a = r.account;
  $("#account").innerHTML = `
    <h3>COBOL input record</h3>
    <div class="mono">${esc(r.record)}</div>
    <table>
      <tr><th>Account</th><td class="num">${a.ACCT}</td></tr>
      <tr><th>Credit limit</th><td class="num">${money(a.LIMIT)}</td></tr>
      <tr><th>September statement</th><td class="num">${money(a.BILL)}</td></tr>
      <tr><th>September payment</th><td class="num">${money(a.PAID)}</td></tr>
      <tr><th>Months past due now</th><td class="num">${a.DELINQ}</td></tr>
      <tr><th>Late months in last 6</th><td class="num">${a.LATE_6M}</td></tr>
      ${r.defaulted === null ? "" : `<tr><th>Actually defaulted next month</th><td class="num">${r.defaulted ? "yes" : "no"}</td></tr>`}
    </table>`;
}

function renderDecision(r) {
  const h = r.hybrid;
  const factors = r.risk.factors
    .map((f) => `<tr><td>${esc(f.feature)}</td><td class="num">${money(Math.round(f.value * 100) / 100)}</td><td class="num">${money(Math.round(f.median * 100) / 100)}</td><td class="num">${f.effect >= 0 ? "+" : ""}${(f.effect * 100).toFixed(1)} pts</td></tr>`)
    .join("");
  $("#decision").innerHTML = `
    <div class="final ${tone(h.final)}">
      <span class="word">${esc(h.final)}</span>
      <span>${esc(h.reason)}${h.priority ? ` · priority ${esc(h.priority)}` : ""}</span>
    </div>
    <div class="row">
      <div class="box">
        <div class="label">Legacy policy (COBOL)</div>
        <div class="value">${esc(r.policy.decision)} <span class="hint">${esc(r.policy.rule)} · ${esc(r.policy.reason_code)}</span></div>
        <div class="hint">utilisation ${Number(r.policy.utilisation)}% · lines ${r.policy.lines.join(", ")}</div>
        <div class="hint">${r.cobol.agrees ? "✔ compiled COBOL on " + esc(r.platform) + " gives the same output" : "✖ compiled COBOL disagrees: " + esc(r.cobol.output)}</div>
      </div>
      <div class="box">
        <div class="label">AI default risk (ONNX on ${esc(r.risk.backend.toUpperCase())})</div>
        <div class="value">${pct(r.risk.probability)} <span class="hint">${esc(r.risk.band)}</span></div>
        <div class="meter"><span style="width:${Math.min(100, r.risk.probability * 100)}%"></span></div>
        <div class="hint">low &lt; ${pct(r.risk.thresholds.low)} · high ≥ ${pct(r.risk.thresholds.high)}</div>
      </div>
    </div>
    <h3>Why the policy decided</h3>
    <ul class="conds">${r.policy.conditions.map((c) => `<li>${esc(c.english)} <span class="muted">· <code>${esc(c.text)}</code> · line ${c.line}</span></li>`).join("")}</ul>
    <h3>What drives the AI risk</h3>
    <table><tr><th>Feature</th><th class="num">Account</th><th class="num">Typical</th><th class="num">Effect</th></tr>${factors}</table>
    <h3>Explanation</h3>
    <div class="explain" id="explanation"><span class="muted">IBM Granite is writing the explanation on LinuxONE…</span></div>
    <h3>Timing on LinuxONE</h3>
    <table>
      <tr><th>Extracted rules (Python engine)</th><td class="num">${r.timing_ms.rules_ms.toFixed(2)} ms</td></tr>
      <tr><th>Compiled COBOL (GnuCOBOL, incl. process start)</th><td class="num">${r.timing_ms.cobol_ms.toFixed(2)} ms</td></tr>
      <tr><th>ONNX risk model (Snap ML ${esc(r.risk.backend)})</th><td class="num">${r.timing_ms.risk_ms.toFixed(3)} ms</td></tr>
    </table>`;
}

async function decide(id) {
  $("#decision").innerHTML = `<p class="hint">Scoring account ${id}…</p>`;
  try {
    const r = await api("/api/decide", { id });
    renderAccount(r);
    renderDecision(r);
    renderSource(r.policy.lines);
    $("#acct-id").value = id;
    const seq = ++explainSeq;
    api("/api/explain", { id })
      .then((e) => {
        if (seq !== explainSeq) return;
        const x = e.explanation;
        const by = x.source === "granite"
          ? `IBM Granite (${esc(x.model)}) on LinuxONE · written in ${(x.ms / 1000).toFixed(1)} s${x.cached ? " (cached)" : ""} · numbers checked against the facts`
          : `template · ${esc(x.note || "")}`;
        $("#explanation").innerHTML = `${esc(x.text)}<div class="hint">${by}</div>`;
      })
      .catch((err) => { if (seq === explainSeq) $("#explanation").textContent = "Explanation failed: " + err.message; });
  } catch (err) {
    $("#decision").innerHTML = `<p class="hint">Error: ${esc(err.message)}</p>`;
  }
}

document.querySelectorAll("[data-scenario]").forEach((b) =>
  b.addEventListener("click", async () => {
    try {
      const s = await api("/api/scenario/" + b.dataset.scenario);
      decide(s.id);
    } catch (err) {
      $("#decision").innerHTML = `<p class="hint">Error: ${esc(err.message)}</p>`;
    }
  })
);
$("#id-form").addEventListener("submit", (e) => {
  e.preventDefault();
  const id = parseInt($("#acct-id").value, 10);
  if (id) decide(id);
});

// ---------------------------------------------------------------- evidence
function cellRow(label, c) {
  return `<tr><td>${esc(label)}</td><td class="num">${money(c.accounts)}</td><td class="num">${money(c.defaulted)}</td><td class="num">${c.default_rate === null ? "–" : pct(c.default_rate)}</td></tr>`;
}

function renderEvidence(res) {
  const v = res.verification;
  if (v) {
    $("#ev-verify").innerHTML = `<h2>Rules vs compiled COBOL</h2>
      <p>${v.rules} rules extracted from ${esc(v.program)}; output lines compared byte for byte with the GnuCOBOL build on <strong>${esc(v.platform)}</strong>.</p>
      <table><tr><th>Input set</th><th class="num">Records</th><th class="num">Agree</th></tr>
      ${Object.entries(v.sets).map(([k, s]) => `<tr><td>${esc(k)}</td><td class="num">${money(s.records)}</td><td class="num">${money(s.agree)}</td></tr>`).join("")}
      <tr><th>Total</th><th class="num">${money(v.records)}</th><th class="num">${pct(v.agreement)}</th></tr></table>
      <h3>Records per rule</h3>
      <table>${Object.entries(v.per_rule).map(([k, n]) => `<tr><td>${esc(k)}</td><td class="num">${money(n)}</td></tr>`).join("")}</table>`;
  }
  const m = res.model;
  if (m) {
    const b = m.backends;
    $("#ev-model").innerHTML = `<h2>ONNX risk model</h2>
      <p><code>${esc(m.model)}</code> (${money(m.onnx_bytes)} bytes), trained on ${esc(m.platform)}, ${money(m.test_accounts)} held-out test accounts.</p>
      <table>
        <tr><th>ROC AUC</th><td class="num">${m.metrics_test.auc}</td></tr>
        <tr><th>Average precision</th><td class="num">${m.metrics_test.average_precision}</td></tr>
        <tr><th>High band: precision / recall</th><td class="num">${pct(m.metrics_test.high_band_precision)} / ${pct(m.metrics_test.high_band_recall)}</td></tr>
        <tr><th>Test default rate</th><td class="num">${pct(m.metrics_test.test_default_rate)}</td></tr>
      </table>
      <h3>Same ONNX file, three runtimes</h3>
      <table><tr><th>Runtime</th><th class="num">One account</th><th class="num">Band agreement</th></tr>
        ${b.nnpa && !b.nnpa.error ? `<tr><td>Snap ML, NNPA (zDNN)</td><td class="num">${b.nnpa.latency_ms_one_account} ms</td><td class="num">${pct(b.nnpa.band_agreement_vs_onnx)}</td></tr>` : ""}
        ${b.cpu && !b.cpu.error ? `<tr><td>Snap ML, CPU</td><td class="num">${b.cpu.latency_ms_one_account} ms</td><td class="num">${pct(b.cpu.band_agreement_vs_onnx)}</td></tr>` : ""}
        <tr><td>ONNX reference (exact)</td><td class="num">${b.onnx_reference.latency_ms_one_account} ms</td><td class="num">reference</td></tr>
        <tr><td>scikit-learn</td><td class="num">${b.sklearn.latency_ms_one_account} ms</td><td class="num">–</td></tr>
      </table>`;
  }
  const h = res.hybrid && res.hybrid.test;
  if (h) {
    const li = h.limit_increases;
    $("#ev-hybrid").innerHTML = `<h2>Policy alone vs policy + AI (held-out test accounts)</h2>
      <p>${money(h.accounts)} accounts, ${pct(h.default_rate)} defaulted the next month. The combination table was chosen on validation accounts.</p>
      <div class="row">
        <div><h3>Limit increases</h3>
          <table><tr><th></th><th class="num">Accounts</th><th class="num">Defaulted</th><th class="num">Rate</th></tr>
          ${cellRow("Policy grants", li.policy_granted)}${cellRow("Hybrid grants", li.hybrid_granted)}${cellRow("Hybrid holds", li.hybrid_held)}</table>
          <p class="hint">Defaulters kept from getting an increase: <strong>${pct(li.defaulters_avoided_share)}</strong>.</p></div>
        <div><h3>Review queue priority</h3>
          <table><tr><th></th><th class="num">Accounts</th><th class="num">Defaulted</th><th class="num">Rate</th></tr>
          ${Object.entries(h.review_queue).map(([k, c]) => cellRow(k, c)).join("")}${cellRow("Early warnings (KEEP → REVIEW)", h.early_warnings)}</table></div>
      </div>
      <h3>Policy decisions</h3>
      <table><tr><th></th><th class="num">Accounts</th><th class="num">Defaulted</th><th class="num">Rate</th></tr>
      ${Object.entries(h.policy).map(([k, c]) => cellRow(k, c)).join("")}</table>`;
  }
}

// ---------------------------------------------------------------- start
(async function init() {
  const [prog, health, res] = await Promise.all([api("/api/program"), api("/api/health"), api("/api/results")]);
  program = prog;
  renderSource([]);
  renderRules();
  renderEvidence(res);
  const v = res.verification;
  $("#badges").innerHTML = [
    `<span class="badge">${esc(health.platform)} · ${esc(health.node)}</span>`,
    `<span class="badge ok">risk model: ONNX on ${esc(health.risk_backend.toUpperCase())}</span>`,
    v ? `<span class="badge ok">rules = COBOL on ${pct(v.agreement)} of ${money(v.records)} records</span>` : "",
    `<span class="badge ${health.llm ? "ok" : "warn"}">Granite LLM ${health.llm ? "online" : "offline (template)"}</span>`,
  ].join("");
})().catch((err) => { $("#decision").innerHTML = `<p class="hint">Could not load: ${esc(err.message)}</p>`; });
