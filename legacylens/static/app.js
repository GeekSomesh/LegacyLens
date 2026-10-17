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

// ---------------------------------------------------------------- programs
let programs = [];
let current = null;

function renderProgramTabs() {
  $("#prog-tabs").innerHTML = programs
    .map((p) => `<button role="tab" data-prog="${esc(p.name)}" aria-selected="${current && current.name === p.name}">
        <strong>${esc(p.name)}</strong><small>${esc(p.origin)}</small></button>`)
    .join("");
  document.querySelectorAll("[data-prog]").forEach((b) => b.addEventListener("click", () => loadProgram(b.dataset.prog)));
}

function renderProgSource(lines) {
  const hit = new Set(lines || []);
  $("#prog-source").innerHTML = current.lines
    .map((l) => `<span class="ln${hit.has(l.no) ? " hit" : ""}"><span class="no">${l.no}</span>${esc(l.text)}</span>`)
    .join("");
  const first = $("#prog-source .hit");
  if (first) $("#prog-source").scrollTop = first.offsetTop - 80;
}

function stat(value, label) {
  return `<div class="stat"><b>${value}</b><span>${esc(label)}</span></div>`;
}

function renderProgram() {
  const p = current;
  const v = p.verification;
  const z = p.z3;
  $("#prog-head").innerHTML = `
    <h2>${esc(p.name)} <span class="hint">${esc(p.path)}${p.copybooks.length ? " · copybooks " + p.copybooks.map(esc).join(", ") : ""}</span></h2>
    <p class="hint">${esc(p.origin)}. Rules are ${p.mode === "whole program" ? "paths through the whole decision logic" : "per decision statement, with PERFORMed paragraphs shown as routing"}; decision fields: ${p.decision_fields.map(esc).join(", ")}.</p>
    <div class="stats">
      ${stat(p.lines.length, "source lines")}
      ${stat(p.raw_paths, "paths in the code")}
      ${stat(z.infeasible, "impossible paths (z3 proof)")}
      ${stat(p.rules.length, "rules")}
      ${stat(z.conditions_removed, "redundant conditions removed")}
      ${stat(z.all_proved ? "proved" : "not proved", "simplified = original (z3)")}
      ${z.overlaps === null ? "" : stat(z.overlaps.length === 0 ? "none" : z.overlaps.length, "overlapping rules (z3)")}
      ${v ? stat(v.agree === v.cases ? "100%" : ((100 * v.agree) / v.cases).toFixed(2) + "%",
          v.kind === "rules" ? `rules = compiled COBOL on ${v.cases.toLocaleString()} inputs`
                             : `model = compiled COBOL on ${v.cases.toLocaleString()} file runs`) : stat("static", "not run against the compiled program")}
    </div>
    ${p.findings.length ? `<h3>Findings for review</h3>` + p.findings.map((f) => `<div class="finding"><b>${esc(f.kind)}</b> · ${esc(f.message)} <span class="muted">line ${f.lines.join(", ")}</span></div>`).join("") : ""}`;
  $("#rules-hint").textContent = `${p.rules.length} rules · click one to see its lines`;
  $("#prog-source-title").textContent = p.path.split("/").pop();
  $("#rules").innerHTML = p.rules
    .map((r) => {
      const doc = r.doc
        ? `<p class="explain">${esc(r.doc.text)}<span class="hint"> · ${r.doc.source === "granite" ? "documented by IBM Granite on LinuxONE, checked against the rule" : "generated from the rule"}</span></p>`
        : "";
      const ex = r.example
        ? `<details class="example"><summary class="hint">Example input found by z3</summary><table>${Object.entries(r.example)
            .map(([k, v]) => `<tr><td><code>${esc(k.replace("#numeric", " IS NUMERIC"))}</code></td><td>${esc(String(v))}</td></tr>`).join("")}</table></details>`
        : "";
      const conds = r.conditions
        .map((c) => `<li class="${c.implied ? "implied" : ""}" title="${c.implied ? "implied by the other conditions (z3 proof)" : ""}">${esc(c.english)} <span class="muted">· <code>${esc(c.text)}</code> · line ${c.line}</span></li>`)
        .join("");
      return `<div class="rule" data-rule="${esc(r.id)}">
        <div class="head"><strong>${esc(r.id)}</strong>
          ${Object.entries(r.actions).map(([k, v]) => `<span class="pill">${esc(k)} = ${esc(v)}</span>`).join(" ")}
          ${r.z3 && r.z3.removed ? `<span class="pill ok">z3: ${r.z3.removed} implied ${r.z3.removed === 1 ? "condition" : "conditions"} struck, ${r.z3.equivalent ? "proved equivalent" : "not proved"}</span>` : ""}</div>
        ${doc}<ul class="conds">${conds || "<li class='muted'>always (no other rule applied)</li>"}</ul>${ex}
      </div>`;
    })
    .join("");
  document.querySelectorAll("#rules .rule").forEach((el) =>
    el.addEventListener("click", () => {
      document.querySelectorAll("#rules .rule").forEach((x) => x.classList.toggle("active", x === el));
      const r = current.rules.find((x) => x.id === el.dataset.rule);
      renderProgSource(r.lines);
    })
  );
  renderProgSource([]);
}

async function loadProgram(name) {
  current = await api("/api/programs/" + encodeURIComponent(name));
  renderProgramTabs();
  renderProgram();
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
        const facts = x.draft ? `<details><summary class="hint">Facts given to Granite</summary><div class="hint">${esc(x.draft)}</div></details>` : "";
        const rejected = x.rejected ? `<details><summary class="hint">Rejected Granite rewrite</summary><div class="hint">${esc(x.rejected)}</div></details>` : "";
        $("#explanation").innerHTML = `${esc(x.text)}<div class="hint">${by}</div>${facts}${rejected}`;
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
  const s1 = res.ibm_sam1;
  if (s1) {
    $("#ev-sam1").innerHTML = `<h2>IBM SAM1, whole file runs</h2>
      <p>IBM's batch program reads a customer file and a transaction file, CALLs SAM2 and writes an updated customer file and a report.
      LegacyLens's interpreter (calling its own model of SAM2) and the compiled program processed the same files.</p>
      <table><tr><th>Runs (IBM sample files + generated)</th><td class="num">${money(s1.runs)}</td></tr>
      <tr><th>Transactions / customer records</th><td class="num">${money(s1.transactions)} / ${money(s1.customers)}</td></tr>
      <tr><th>Undefined runs (arithmetic on text) excluded</th><td class="num">${money(s1.undefined || 0)}</td></tr>
      <tr><th>Same output file, report, console and return code</th><td class="num">${money(s1.agree)} of ${money(s1.defined_runs)} (${pct(s1.agreement)})</td></tr></table>
      <h3>Found while running</h3>${Object.entries(s1.runtime_findings || {}).map(([m, n]) => `<div class="finding">${esc(m)} <span class="muted">(${n} runs)</span></div>`).join("")}`;
  }
  const cf = res.conformance;
  if (cf) {
    $("#ev-conf").innerHTML = `<h2>Construct conformance</h2>
      <p>Small COBOL programs run on random records by GnuCOBOL and by the interpreter; every DISPLAY line compared.</p>
      <table><tr><th>Program</th><th>Covers</th><th class="num">Same</th></tr>
      ${Object.entries(cf.programs).map(([k, x]) => `<tr><td>${esc(k)}</td><td class="hint">${esc(x.covers)}</td><td class="num">${money(x.agree)}/${money(x.records)}</td></tr>`).join("")}
      <tr><th colspan="2">Total</th><th class="num">${pct(cf.agreement)}</th></tr></table>`;
  }
  const bm = res.benchmark;
  if (bm) {
    $("#ev-speed").innerHTML = `<h2>Speed on the IBM z17</h2>
      <table><tr><th>Step</th><th class="num">Throughput</th><th class="num">One decision</th></tr>
      <tr><td>Compiled COBOL policy (batch of ${money(bm.accounts)} accounts)</td><td class="num">${money(bm.cobol_batch.per_second)} / s</td><td class="num">–</td></tr>
      <tr><td>ONNX risk model, Snap ML on the AI accelerator (NNPA)</td><td class="num">${money(bm.risk_nnpa.per_second)} / s</td><td class="num">${bm.risk_nnpa.one_account_us_p50} µs</td></tr>
      <tr><td>ONNX risk model, Snap ML on the CPU</td><td class="num">${money(bm.risk_cpu.per_second)} / s</td><td class="num">${bm.risk_cpu.one_account_us_p50} µs</td></tr>
      <tr><td>Hybrid decision for the whole portfolio</td><td class="num">${money(bm.hybrid_portfolio.per_second)} / s</td><td class="num">–</td></tr>
      ${bm.api_decide ? `<tr><td>One account through the web service (incl. live COBOL check)</td><td class="num">–</td><td class="num">${bm.api_decide.ms_p50} ms (p95 ${bm.api_decide.ms_p95})</td></tr>` : ""}
      </table>
      <p class="hint">On this virtual server Snap ML's CPU path (z17 vector instructions) is faster than its NNPA path for tree models of every size we tried; both give the same decisions. We report both.</p>`;
  }
  const ib = res.ibm_sam2;
  if (ib) {
    const total = Object.values(ib.sets).reduce((a, x) => a + x.cases, 0);
    const agree = Object.values(ib.sets).reduce((a, x) => a + x.agree, 0);
    $("#ev-ibm").innerHTML = `<h2>IBM's own code: SAM2 (zopeneditor-sample)</h2>
      <p>A COBOL subroutine IBM publishes as a sample, with two copybooks, REDEFINES overlays, packed decimals and OCCURS tables.
      LegacyLens found <strong>${ib.raw_paths}</strong> paths; z3 proved <strong>${ib.z3.infeasible_paths}</strong> impossible, removed
      <strong>${ib.z3.conditions_removed}</strong> redundant conditions (every simplification proved equivalent) and found no overlapping rules.
      The <strong>${ib.rules}</strong> rules were then run against the compiled program, CALLed through a generated driver, comparing all four parameters byte for byte.</p>
      <table><tr><th>Input set</th><th class="num">Calls</th><th class="num">Same parameters</th></tr>
      ${Object.entries(ib.sets).map(([k, x]) => `<tr><td>${esc(k.replace(/_/g, " "))}</td><td class="num">${money(x.cases)}</td><td class="num">${money(x.agree)}</td></tr>`).join("")}
      <tr><th>Total</th><th class="num">${money(total)}</th><th class="num">${pct(agree / total)}</th></tr></table>`;
  }
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

// ---------------------------------------------------------------- change impact
function renderImpact(d) {
  const p = d.portfolio;
  const confirmed = d.regions.filter((r) => r.confirmed).length;
  $("#imp-head").innerHTML = `<h2>Change impact: ${esc(d.old)} → ${esc(d.new)}</h2>
    <p>Before a change to a critical policy goes live, LegacyLens shows exactly what it does. Rules are compared, z3 finds every
    combination of old and new rule whose decision differs (with an example account for each), both compiled programs confirm the
    examples, and both are run on the whole portfolio.</p>
    <div class="stats">
      ${stat(d.unchanged.length, "rules unchanged")}
      ${stat(d.changed.length, "rules with changed tests")}
      ${stat(d.added.length, "rules added")}
      ${stat(d.removed.length, "rules removed")}
      ${stat(d.regions.length, "regions where the outcome changes (z3)")}
      ${stat(`${confirmed}/${d.regions.length}`, "confirmed by both compiled programs")}
      ${stat(`${money(p.changed)} of ${money(p.accounts)}`, "accounts change outcome")}
      ${stat(p.every_change_inside_a_z3_region ? "yes" : "no", "every change inside a z3 region")}
    </div>`;
  $("#imp-moves").innerHTML = `<h2>What changes for real accounts</h2>
    <table><tr><th>From</th><th>To</th><th class="num">Accounts</th><th class="num">Defaulted next month</th></tr>
    ${p.moves.map((m) => {
      const rate = m.defaulted_next_month / m.accounts;
      const softer = /^(DECREASE|BLOCK)/.test(m.from) && /^(REVIEW|KEEP|INCREASE)/.test(m.to);
      return `<tr class="${softer && rate > 0.4 ? "alert" : ""}"><td>${esc(m.from)}</td><td>${esc(m.to)}</td><td class="num">${money(m.accounts)}</td><td class="num">${money(m.defaulted_next_month)} (${pct(rate)})</td></tr>`;
    }).join("")}</table>
    <p class="hint">Highlighted: the change makes the decision softer for a group where most accounts defaulted the next month.</p>`;
  $("#imp-diff").innerHTML = `<h2>Source change</h2><pre class="diff">${d.diff.map((l) => {
      const c = l.startsWith("+") && !l.startsWith("+++") ? "add" : l.startsWith("-") && !l.startsWith("---") ? "del" : l.startsWith("@@") ? "hunk" : "";
      return `<span class="${c}">${esc(l)}</span>`;
    }).join("")}</pre>`;
  $("#imp-regions").innerHTML = `<h2>Regions where the decision changes (found by z3)</h2>
    <table><tr><th>Old rule → new rule</th><th>Old outcome</th><th>New outcome</th><th>Example account (z3)</th><th>Compiled old → new</th></tr>
    ${d.regions.map((r) => `<tr>
      <td>${esc(r.old_rule)} → ${esc(r.new_rule)}</td>
      <td>${esc(Object.values(r.old).join(" "))}</td><td>${esc(Object.values(r.new).join(" "))}</td>
      <td class="mono">${r.example ? Object.entries(r.example).filter(([k]) => k !== "IN-ACCT").map(([k, v]) => `${esc(k)}=${esc(String(v))}`).join(" ") : "–"}</td>
      <td class="mono">${r.compiled_old ? esc(r.compiled_old.split(" ").slice(1).join(" ")) + " → " + esc(r.compiled_new.split(" ").slice(1).join(" ")) + (r.confirmed ? " ✔" : " ✖") : "–"}</td></tr>`).join("")}
    </table>`;
}

// ---------------------------------------------------------------- start
(async function init() {
  const [prog, health, res] = await Promise.all([api("/api/program"), api("/api/health"), api("/api/results")]);
  program = prog;
  renderSource([]);
  renderEvidence(res);
  api("/api/impact").then(renderImpact).catch(() => {});
  programs = await api("/api/programs");
  if (programs.length) await loadProgram(programs[0].name);
  const v = res.verification;
  $("#badges").innerHTML = [
    `<span class="badge">${esc(health.platform)} · ${esc(health.node)}</span>`,
    `<span class="badge ok">risk model: ONNX on ${esc(health.risk_backend.toUpperCase())}</span>`,
    v ? `<span class="badge ok">rules = COBOL on ${pct(v.agreement)} of ${money(v.records)} records</span>` : "",
    `<span class="badge ${health.llm ? "ok" : "warn"}">Granite LLM ${health.llm ? "online" : "offline (template)"}</span>`,
  ].join("");
})().catch((err) => { $("#decision").innerHTML = `<p class="hint">Could not load: ${esc(err.message)}</p>`; });
