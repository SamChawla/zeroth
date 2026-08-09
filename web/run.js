let source = null;
let currentJob = null;
let startTime = null;
let elapsedTimer = null;
let runProvider = "";
let compatReport = null;
let hasOwnConfig = false;
let hasOfficial = false;

/* ---------- terminal ---------- */

function logLine(text, cls = "text-zinc-400") {
  const el = $("terminal");
  if (!el) return;
  const row = document.createElement("div");
  row.className = "flex gap-3";
  const now = new Date();
  const stamp = document.createElement("span");
  stamp.className = "text-zinc-600 shrink-0 tabular-nums";
  stamp.textContent = [now.getHours(), now.getMinutes(), now.getSeconds()]
    .map((n) => String(n).padStart(2, "0")).join(":");
  const body = document.createElement("span");
  body.className = `${cls} min-w-0 break-words`;
  body.textContent = text;
  row.append(stamp, body);
  el.appendChild(row);
  el.scrollTop = el.scrollHeight;
}

/* ---------- elapsed clock ---------- */

function startClock() {
  startTime = Date.now();
  stopClock();
  elapsedTimer = setInterval(() => {
    const s = Math.floor((Date.now() - startTime) / 1000);
    const mm = String(Math.floor(s / 60)).padStart(2, "0");
    const ss = String(s % 60).padStart(2, "0");
    if ($("elapsed")) $("elapsed").textContent = `${mm}:${ss}`;
  }, 1000);
}

function stopClock() {
  if (elapsedTimer) clearInterval(elapsedTimer);
  elapsedTimer = null;
}

/* ---------- stage pill helpers ---------- */

const PILL = {
  pending: "pill pill-idle",
  active: "pill pill-running",
  complete: "pill pill-verified",
  failed: "pill pill-failed",
};

function setStage(stage, state, text) {
  const card = $(`stage-${stage}`);
  const tag = $(`stage-${stage}-tag`);
  if (!card || !tag) return;
  card.classList.toggle("opacity-60", state === "pending");
  tag.className = PILL[state] || PILL.pending;
  tag.innerHTML = state === "active"
    ? `<span class="material-symbols-outlined text-[13px] animate-spin">progress_activity</span> ${escape(text)}`
    : escape(text);
}

/* ---------- checklist ----------
   Every transition here is driven by a real pipeline event. Nothing advances on
   a timer, so a step that is stuck looks stuck rather than looking busy. */

const MARK = { pending: "○", active: "●", done: "✓", failed: "✕" };

function setCheck(id, state, text) {
  const el = $(id);
  if (!el) return;
  el.className = `check ${state}`;
  el.querySelector(".check-mark").textContent = MARK[state] || MARK.pending;
  if (text) el.querySelector(".check-text").textContent = text;
}

const ANALYZE_CHECKS = ["chk-fetch", "chk-structure", "chk-runtime", "chk-deployable", "chk-config"];
const VERIFY_CHECKS = ["chk-project", "chk-deploy", "chk-boot", "chk-health", "chk-teardown"];

function resetChecks() {
  const labels = {
    "chk-fetch": "Repository fetched", "chk-structure": "Structure analyzed",
    "chk-runtime": "Runtime detected", "chk-deployable": "Deployability assessed",
    "chk-config": "Configuration generated", "chk-project": "Project created",
    "chk-deploy": "Application deployed", "chk-boot": "Application booted",
    "chk-health": "Health checked", "chk-teardown": "Torn down",
  };
  [...ANALYZE_CHECKS, ...VERIFY_CHECKS].forEach((id) => setCheck(id, "pending", labels[id]));
  hide("checklist-verify");
}

/* A failure stops the run, so every step still pending is one that never got
   its turn - saying so beats leaving them looking merely unfinished. */
function abandonChecks(ids) {
  ids.forEach((id) => {
    const el = $(id);
    if (el && el.classList.contains("active")) setCheck(id, "failed");
  });
}

/* ---------- starting a run ---------- */

async function start(url) {
  hide("err");
  $("go").disabled = true;
  resetRun();

  try {
    const id = await startJob(url);
    currentJob = id;
    history.replaceState(null, "", `run.html?job=${encodeURIComponent(id)}`);
    listen(id);
  } catch (e) {
    fail(e.message);
  }
}

function resetRun() {
  $("terminal").innerHTML = "";
  $("evidence").innerHTML = "";
  $("services").innerHTML = "";
  $("attempt-list").innerHTML = "";
  hide("result-banner");
  hide("result-grid");
  hide("tryout");
  hide("tryout-err");
  hide("tryout-verdict");
  hide("config-source-row");
  hasOwnConfig = false;
  hasOfficial = false;
  hide("compat");
  hide("fixprompt");
  compatReport = null;
  resetChecks();
  setStage("fingerprint", "pending", "Pending");
  setStage("reason", "pending", "Pending");
  setStage("verify", "pending", "Pending");
  runProvider = "";
  $("run-provider").textContent = "—";
  $("live-dot").className = "dot bg-accent pulse";
  show("run");
  startClock();
}

function settleLog() {
  const pill = $("log-live");
  if (!pill) return;
  pill.className = "pill pill-idle";
  pill.textContent = "idle";
}

function fail(message) {
  $("err").textContent = message;
  show("err");
  $("go").disabled = false;
}

/* ---------- live event stream ---------- */

function listen(jobId) {
  if (source) source.close();
  source = new EventSource(`${API}/api/jobs/${jobId}/events`);

  source.addEventListener("close", () => {
    source.close();
    source = null;
    if ($("go")) $("go").disabled = false;
    loadGallery();
  });

  source.onmessage = (ev) => {
    let msg;
    try { msg = JSON.parse(ev.data); } catch { return; }
    handle(msg, jobId);
  };

  source.onerror = () => {
    if (source && source.readyState === EventSource.CLOSED && $("go")) {
      $("go").disabled = false;
    }
  };
}

function handle(msg, jobId) {
  switch (msg.event) {
    case "status":
      logLine(`[status] ${msg.status}${msg.detail ? " — " + msg.detail : ""}`);
      if (["validating", "ingesting"].includes(msg.status)) {
        setStage("fingerprint", "active", "Running");
        setCheck("chk-fetch", "active");
      }
      if (["analyzing", "generating", "checking"].includes(msg.status)) setStage("reason", "active", "Generating");
      if (msg.status === "verifying") setStage("verify", "active", "Verifying");
      if (msg.status === "checking") setCheck("chk-deployable", "active");
      if (msg.status === "generating") setCheck("chk-config", "active");
      if (msg.status === "failed") {
        logLine(`[failed] ${msg.detail || ""}`, "text-red-400");
        abandonChecks([...ANALYZE_CHECKS, ...VERIFY_CHECKS]);
      }
      break;
    case "fingerprint": {
      renderEvidence(msg.fingerprint);
      const fp = msg.fingerprint || {};
      const runtime = [fp.language, fp.runtime_version].filter(Boolean).join(" ") || "unknown";
      setCheck("chk-fetch", "done");
      setCheck("chk-structure", "done");
      setCheck("chk-runtime", "done", `Runtime detected: ${runtime}`);
      setStage("fingerprint", "complete", "Complete");
    }
      break;
    case "compatibility": {
      renderCompatibility(msg.compatibility);
      const c = msg.compatibility || {};
      setCheck("chk-deployable", c.verdict === "unsupported" ? "failed" : "done",
               c.headline || "Deployability assessed");
      break;
    }
    case "manifest":
      renderServices(msg.manifest);
      setStage("reason", "active", "Generating");
      break;
    case "config":
      $("import-yaml").textContent = msg.import_yaml;
      $("zerops-yaml").textContent = msg.zerops_yaml;
      $("download").href = `${API}/api/jobs/${jobId}/bundle`;
      setStage("reason", "complete", "Complete");
      logLine("[generate] zerops-project-import.yaml + zerops.yaml written");
      // The config is the deliverable, so show it as soon as it exists rather
      // than making it wait behind a deployment the user has not asked for.
      setCheck("chk-config", "done");
      renderConfig();
      break;
    case "ready":
      stopClock();
      settleLog();
      setStage("verify", "pending", "Not run");
      $("stage-verify-note").textContent =
        "Nothing has been provisioned. Start a verification to prove this configuration boots.";
      logLine("[ready] configuration generated — nothing deployed", "text-indigo-300");
      $("live-dot").className = "dot bg-accent";
      hasOwnConfig = !!msg.has_own_config;
      hasOfficial = !!msg.has_official_recipe;
      if (msg.verifiable !== false) showTryout();
      break;
    case "verify_rejected":
      tryoutError(msg.reason === "at_capacity"
        ? "The verification queue is full right now. Try again in a few minutes."
        : "That request expired. Enter your token again.");
      showTryout();
      break;
    case "attempt_started":
      hide("tryout");
      show("checklist-verify");
      setCheck("chk-project", "active");
      setStage("verify", "active", "Verifying");
      $("stage-verify-note").textContent = `Attempt ${msg.attempt} — provisioning via ${msg.provider}.`;
      runProvider = msg.provider || "";
      $("run-provider").textContent = msg.provider;
      addAttempt(msg.attempt, msg.provider);
      logLine(`> attempt ${msg.attempt} — provisioning via ${msg.provider}`, "text-indigo-300");
      break;
    case "stage":
      if (msg.stage === "provisioning") setCheck("chk-project", "active");
      if (msg.stage === "deploying") { setCheck("chk-project", "done"); setCheck("chk-deploy", "active"); }
      if (msg.stage === "diagnosing") abandonChecks(VERIFY_CHECKS);
      logLine(`  ${msg.stage}… (attempt ${msg.attempt})`);
      appendAttempt(msg.attempt, `<div class="text-sm text-fg2 flex items-center gap-2"><span class="material-symbols-outlined text-[14px] animate-spin">progress_activity</span>${escape(msg.stage)}…</div>`);
      break;
    case "attempt_failed":
      abandonChecks(VERIFY_CHECKS);
      logLine(`FAIL attempt ${msg.attempt}: ${msg.failure_class} — ${msg.error || ""}`, "text-red-400");
      appendAttempt(msg.attempt, `
        <div><span class="pill pill-failed">Failed — ${escape(msg.failure_class)}</span></div>
        <div class="text-sm text-fg2">${escape(msg.error || "")}</div>
        ${msg.logs ? `<pre class="code-surface rounded-xl p-3 mt-1 font-mono text-[12px] leading-relaxed overflow-x-auto scroll-thin">${escape(msg.logs)}</pre>` : ""}`);
      break;
    case "repair_proposed":
      logLine(`REPAIR: ${msg.diagnosis}`, "text-amber-300");
      appendAttempt(msg.attempt, `
        <div class="rounded-xl border border-edge bg-surface p-3 flex flex-col gap-1.5">
          <span class="pill pill-warning w-fit">Repaired &amp; retried</span>
          <div class="text-sm text-fg2"><strong class="text-fg font-medium">Diagnosis:</strong> ${escape(msg.diagnosis)}</div>
          <div class="text-sm text-fg2"><strong class="text-fg font-medium">Repair:</strong> ${escape(msg.patch_summary)}</div>
        </div>`);
      break;
    case "attempt_passed":
      logLine(`PASS attempt ${msg.attempt} in ${msg.elapsed}s`, "text-emerald-400");
      appendAttempt(msg.attempt, `
        <div><span class="pill pill-verified"><span class="dot bg-success"></span> Verified in ${msg.elapsed}s</span></div>
        <div class="font-mono text-[12px] text-fg2">${escape(JSON.stringify(msg.verification || {}))}</div>`);
      VERIFY_CHECKS.slice(0, 4).forEach((id) => setCheck(id, "done"));
      setStage("verify", runProvider === "simulated" ? "failed" : "complete",
               runProvider === "simulated" ? "Simulated" : "Verified");
      break;
    case "torn_down":
      setCheck("chk-teardown", "done");
      break;
    case "kept":
      setCheck("chk-teardown", "done", "Kept in your account");
      logLine(`KEPT project ${msg.project_id} in your account — ${msg.url || "no public URL"}`, "text-emerald-400");
      break;
    case "complete":
      stopClock();
      settleLog();
      hide("tryout");
      logLine(`DONE — verified=${msg.verified}`, msg.verified ? "text-emerald-400" : "text-amber-300");
      if (!msg.verified) setStage("verify", "failed", "Not verified");
      renderResult(msg.verified, msg.live_url, msg.kept_project_id, msg.simulated);
      break;
  }
}

/* ---------- rendering ---------- */

function renderEvidence(fp) {
  if (!fp) return;
  const tiles = [
    ["Detected stack", `${fp.language}${fp.runtime_version ? " " + fp.runtime_version : ""}${fp.framework ? " · " + fp.framework : ""}`],
    ["Databases", (fp.databases || []).join(", ") || "none"],
    ["Cache / worker", `${(fp.caches || []).join(", ") || "none"}${fp.has_worker ? " · worker" : ""}`],
    ["Config files", (fp.present_files || []).join(", ") || "none"],
  ];
  $("evidence").innerHTML = tiles.map(([label, value]) => `
    <div class="bg-surface2 border border-edge rounded-xl p-3.5 animate-fade-up">
      <div class="text-[11px] uppercase tracking-wider text-fg3 mb-1.5">${escape(label)}</div>
      <div class="font-mono text-[13px] text-fg truncate" title="${escape(value)}">${escape(value)}</div>
    </div>`).join("");
}

/* Deployability. Amber for "needs changes" rather than red: the config still
   gets generated, so it is a caveat, not a failure. */
const VERDICT = {
  deployable:    { pill: "pill pill-verified", label: "Deployable", icon: "check_circle", tone: "text-success" },
  needs_changes: { pill: "pill pill-warning",  label: "Needs changes", icon: "build", tone: "text-warning" },
  unsupported:   { pill: "pill pill-failed",   label: "Not deployable", icon: "block", tone: "text-danger" },
};

const FINDING = {
  blocker: { icon: "block", tone: "text-danger", label: "Blocker" },
  change:  { icon: "build", tone: "text-warning", label: "Change needed" },
  note:    { icon: "info", tone: "text-fg3", label: "Note" },
};

function renderCompatibility(report) {
  if (!report) return;
  compatReport = report;
  const v = VERDICT[report.verdict] || VERDICT.needs_changes;

  $("compat-verdict").className = v.pill;
  $("compat-verdict").textContent = v.label;
  $("compat-headline").textContent = report.headline || "";
  $("compat-icon").innerHTML = `<span class="material-symbols-outlined text-[20px] ${v.tone}">${v.icon}</span>`;

  // Blockers first, then changes, then notes: severity order is the reading order.
  const order = { blocker: 0, change: 1, note: 2 };
  const findings = [...(report.findings || [])].sort(
    (a, b) => (order[a.level] ?? 3) - (order[b.level] ?? 3));

  $("compat-findings").innerHTML = findings.map((f) => {
    const meta = FINDING[f.level] || FINDING.note;
    return `
      <div class="flex gap-3 rounded-xl border border-edge bg-surface2 p-3.5">
        <span class="material-symbols-outlined text-[18px] ${meta.tone} shrink-0 mt-0.5">${meta.icon}</span>
        <div class="min-w-0">
          <div class="font-medium text-sm mb-0.5">${escape(f.title)}</div>
          <p class="text-sm text-fg2">${escape(f.detail)}</p>
          ${f.evidence ? `<p class="font-mono text-[12px] text-fg3 mt-1.5">${escape(f.evidence)}</p>` : ""}
        </div>
      </div>`;
  }).join("");

  const prompt = report.fix_prompt || "";
  if (prompt) {
    $("fixprompt-text").textContent = prompt;
    show("fixprompt");
  } else {
    hide("fixprompt");
  }

  show("compat");
}

function renderServices(manifest) {
  if (!manifest || !manifest.services) return;
  $("services").innerHTML = manifest.services.map((s) => `
    <div class="bg-surface2 border border-edge rounded-xl p-3.5 animate-fade-up">
      <div class="flex items-center gap-2 flex-wrap">
        <span class="font-mono text-[13px] font-semibold text-fg" data-hostname>${escape(s.hostname)}</span>
        <span class="font-mono text-[12px] text-fg3">${escape(s.type)}</span>
        ${s.public ? '<span class="pill pill-running">public</span>' : ""}
      </div>
      <div class="text-sm text-fg2 mt-1.5">${escape(s.reason || "No reason recorded.")}</div>
    </div>`).join("");
}

function addAttempt(n, provider) {
  if ($(`attempt-${n}`)) return;
  const el = document.createElement("div");
  el.className = "bg-surface2 border border-edge rounded-xl p-4 animate-fade-up";
  el.id = `attempt-${n}`;
  el.innerHTML = `
    <div class="flex items-center justify-between gap-3 mb-2.5">
      <span class="text-[11px] uppercase tracking-wider text-fg3">Attempt ${n}</span>
      <span class="font-mono text-[11px] text-fg3">${escape(provider)}</span>
    </div>
    <div id="attempt-${n}-body" class="flex flex-col gap-2"></div>`;
  $("attempt-list").appendChild(el);
}

function appendAttempt(n, html) {
  addAttempt(n, "…");
  const body = $(`attempt-${n}-body`);
  if (body) body.insertAdjacentHTML("beforeend", html);
}

/* The config panel stands on its own: it is what the user came for, and it is
   meaningful whether or not anyone ever asks for a deployment. */
function renderConfig() {
  $("why-services").innerHTML = Array.from(document.querySelectorAll("#services > div")).map((el) => el.outerHTML).join("")
    || '<p class="text-sm text-fg2">No services were generated.</p>';
  renderBootLog(null);
  show("result-grid");
}

function renderBootLog(verified) {
  const state = verified === null
    ? { dot: "bg-fg3", pill: "pill pill-idle", label: "Not deployed" }
    : verified
      ? { dot: "bg-success", pill: "pill pill-verified", label: "Healthy" }
      : { dot: "bg-fg3", pill: "pill pill-idle", label: "Unverified" };

  $("boot-log").innerHTML = "";
  document.querySelectorAll("#services > div").forEach((svcEl) => {
    const hostname = svcEl.querySelector("[data-hostname]")?.textContent || "";
    $("boot-log").insertAdjacentHTML("beforeend", `
      <div class="flex items-center justify-between gap-3 p-3 rounded-xl bg-surface2 border border-edge">
        <div class="flex items-center gap-2.5 min-w-0">
          <span class="dot ${state.dot}"></span>
          <span class="font-mono text-[13px] truncate">${escape(hostname)}</span>
        </div>
        <span class="${state.pill}">${state.label}</span>
      </div>`);
  });
}

function renderResult(verified, liveUrl, keptProjectId, simulated = false) {
  // A simulated run passes the pipeline but deploys nothing, so it is shown as
  // its own outcome rather than as a verification. Green here would be a claim
  // the run did not earn.
  const proven = verified && !simulated;
  const banner = $("result-banner");
  banner.className = "card card-raised p-7 md:p-8 flex flex-col md:flex-row md:items-center justify-between gap-6";
  banner.style.borderLeft = `3px solid rgb(var(${proven ? "--success" : "--warning"}))`;

  const attempts = document.querySelectorAll("#attempt-list > div").length || 1;
  const environment = simulated ? "Simulated" : keptProjectId ? "Your account" : "Ephemeral";
  const meta = [
    ["Status", simulated ? "Not deployed" : verified ? "Healthy" : "Not verified"],
    ["Attempts", String(attempts)],
    ["Environment", environment],
    ["Verification", proven ? "Passed" : simulated ? "Not run" : "Failed"],
  ];

  const kept = simulated
    ? `<p class="text-sm text-fg2 mt-2.5">No Zerops project was created and nothing was built. Set
       <span class="font-mono">ZCLI_TOKEN</span> and <span class="font-mono">PATHFINDER_PROVIDER=zcli</span> to deploy for real.</p>`
    : keptProjectId
      ? `<p class="text-sm text-fg2 mt-2.5">Left running in your account as project <span class="font-mono">${escape(keptProjectId)}</span>.</p>`
      : verified
        ? '<p class="text-sm text-fg2 mt-2.5">The throwaway project has been destroyed, as designed.</p>'
        : "";

  banner.innerHTML = `
    <div class="flex items-start gap-5 min-w-0">
      <div class="w-11 h-11 rounded-xl flex items-center justify-center shrink-0 ${proven ? "bg-success/12 text-success" : "bg-warning/14 text-warning"}">
        <span class="material-symbols-outlined text-[24px]">${proven ? "check_circle" : simulated ? "science" : "error"}</span>
      </div>
      <div class="min-w-0">
        <h2 class="text-xl font-semibold tracking-tight mb-1.5">${proven ? "Deployment verified" : simulated ? "Simulated — nothing was deployed" : "Deployment verification failed"}</h2>
        <p class="text-fg2">${proven
          ? "The generated Zerops configuration deployed and the application booted correctly."
          : simulated
            ? "This run used the offline provider. The repair loop and the configuration below are real, but no project was created and nothing was built — so this is not a verification."
            : "The configuration did not come up within the attempt limit. The trail above shows each attempt and what it hit."}</p>
        ${kept}
        <dl class="grid grid-cols-2 sm:grid-cols-4 gap-x-6 gap-y-3 mt-5">
          ${meta.map(([k, v]) => `
            <div>
              <dt class="text-[11px] uppercase tracking-wider text-fg3 mb-0.5">${escape(k)}</dt>
              <dd class="text-sm font-medium ${k === "Status" && proven ? "text-success" : ""}">${escape(v)}</dd>
            </div>`).join("")}
        </dl>
      </div>
    </div>
    <div class="flex flex-col gap-2.5 shrink-0">
      ${proven && liveUrl
        ? `<a href="${escape(liveUrl)}" target="_blank" rel="noopener" class="btn btn-primary">
             <span class="material-symbols-outlined text-[18px]">open_in_new</span> View deployment
           </a>` : ""}
      <a href="#result-grid" class="btn btn-secondary">
        <span class="material-symbols-outlined text-[18px]">code</span> View configuration
      </a>
    </div>`;
  show("result-banner");

  if (proven) {
    const badge = $("config-badge");
    badge.className = "pill pill-verified";
    badge.textContent = "Verified";
    show("config-badge");
  }

  renderBootLog(proven);
  renderConfig();
}

/* ---------- try it out ---------- */

function showTryout() {
  // The verdict decides what is even on offer here. Presenting the same
  // confident deploy button after the checker said it will fail is what made
  // the deployability stage decorative.
  const verdict = compatReport && compatReport.verdict;
  const btn = $("tryout-go");
  const note = $("tryout-verdict");
  btn.disabled = false;
  btn.classList.remove("btn-secondary");
  btn.classList.add("btn-primary");

  if (verdict === "unsupported") {
    btn.disabled = true;
    note.innerHTML = '<span class="material-symbols-outlined text-[17px] text-danger shrink-0">block</span>' +
      "<span>There is no Zerops runtime for this stack, so there is nothing to deploy it onto.</span>";
    show("tryout-verdict");
  } else if (verdict === "needs_changes") {
    const changes = (compatReport.findings || []).filter((f) => f.level === "change").length;
    btn.classList.remove("btn-primary");
    btn.classList.add("btn-secondary");
    btn.innerHTML = '<span class="material-symbols-outlined text-[18px]">play_arrow</span> Deploy anyway';
    note.innerHTML = '<span class="material-symbols-outlined text-[17px] text-warning shrink-0">build</span>' +
      `<span>The check found ${changes} change${changes === 1 ? "" : "s"} above that will most likely make this fail. ` +
      "Fixing them first is cheaper than watching it break.</span>";
    show("tryout-verdict");
  } else {
    btn.innerHTML = '<span class="material-symbols-outlined text-[18px]">play_arrow</span> Try it out';
    hide("tryout-verdict");
  hide("config-source-row");
  hasOwnConfig = false;
  hasOfficial = false;
  }
  // Only offer candidates that actually exist for this repository.
  const anyChoice = hasOwnConfig || hasOfficial;
  $("choice-repo").hidden = !hasOwnConfig;
  $("choice-official").hidden = !hasOfficial;
  if (!hasOwnConfig) {
    const fallback = document.querySelector(
      `input[name="config-source"][value="${hasOfficial ? "official" : "generated"}"]`);
    if (fallback) fallback.checked = true;
  }
  $("config-source-row").classList.toggle("hidden", !anyChoice);
  $("config-source-row").classList.toggle("flex", anyChoice);
  show("tryout");
}

function tryoutError(message) {
  const el = $("tryout-err");
  el.textContent = message;
  show("tryout-err");
  $("tryout-go").disabled = false;
}

function selectedConfigSource() {
  if (!hasOwnConfig && !hasOfficial) return "generated";
  const picked = document.querySelector('input[name="config-source"]:checked');
  return picked ? picked.value : (hasOwnConfig ? "repository" : "official");
}

function selectedTarget() {
  const picked = document.querySelector('input[name="verify-target"]:checked');
  return picked ? picked.value : "ephemeral";
}

async function requestVerify() {
  const target = selectedTarget();
  const token = $("zerops-token").value.trim();
  hide("tryout-err");

  if (target === "account" && !token) {
    tryoutError("Deploying to your own account needs a Zerops personal access token.");
    return;
  }

  $("tryout-go").disabled = true;
  try {
    const res = await fetch(`${API}/api/jobs/${currentJob}/verify`, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({
        target,
        token: target === "account" ? token : null,
        acknowledge: (compatReport && compatReport.verdict) === "needs_changes",
        config_source: selectedConfigSource(),
      }),
    });
    if (!res.ok) {
      const body = await res.json().catch(() => ({}));
      tryoutError(body.detail || `Could not start verification (${res.status}).`);
      return;
    }
    // Drop the token from the page as soon as it has been handed over.
    $("zerops-token").value = "";
    hide("tryout");
    hide("result-banner");
    $("live-dot").className = "dot bg-accent pulse";
    setStage("verify", "active", "Verifying");
    startClock();
    logLine(`> verification requested — target ${target}`, "text-indigo-300");
    listen(currentJob);
  } catch (e) {
    tryoutError(e.message);
  }
}

function initTryout() {
  document.querySelectorAll('input[name="verify-target"]').forEach((radio) => {
    radio.addEventListener("change", () => {
      $("token-row").classList.toggle("hidden", selectedTarget() !== "account");
      $("token-row").classList.toggle("flex", selectedTarget() === "account");
    });
  });
  $("tryout-go").addEventListener("click", requestVerify);

  $("fixprompt-toggle").addEventListener("click", (e) => {
    const pre = $("fixprompt-text");
    const open = pre.classList.toggle("hidden");
    e.currentTarget.innerHTML = open
      ? '<span class="material-symbols-outlined text-[15px]">visibility</span> Show'
      : '<span class="material-symbols-outlined text-[15px]">visibility_off</span> Hide';
  });

  $("fixprompt-copy").addEventListener("click", async (e) => {
    const btn = e.currentTarget;
    const original = btn.innerHTML;
    try {
      await navigator.clipboard.writeText($("fixprompt-text").textContent || "");
      btn.innerHTML = '<span class="material-symbols-outlined text-[15px]">check</span> Copied';
    } catch {
      btn.innerHTML = '<span class="material-symbols-outlined text-[15px]">close</span> Failed';
    }
    setTimeout(() => { btn.innerHTML = original; }, 1600);
  });
}

/* ---------- config tabs ---------- */

function initTabs() {
  const importTab = $("tab-import"), zeropsTab = $("tab-zerops");
  const importPre = $("import-yaml"), zeropsPre = $("zerops-yaml");
  const activate = (tab, other, pre, otherPre) => {
    tab.classList.add("border-accent", "text-accent");
    tab.classList.remove("border-transparent", "text-fg2");
    other.classList.remove("border-accent", "text-accent");
    other.classList.add("border-transparent", "text-fg2");
    pre.classList.remove("hidden");
    otherPre.classList.add("hidden");
  };
  importTab.addEventListener("click", () => activate(importTab, zeropsTab, importPre, zeropsPre));
  zeropsTab.addEventListener("click", () => activate(zeropsTab, importTab, zeropsPre, importPre));

  $("copy-config").addEventListener("click", async (e) => {
    const visible = $("import-yaml").classList.contains("hidden") ? $("zerops-yaml") : $("import-yaml");
    const btn = e.currentTarget;
    const original = btn.innerHTML;
    try {
      await navigator.clipboard.writeText(visible.textContent || "");
      btn.innerHTML = '<span class="material-symbols-outlined text-[15px]">check</span> Copied';
    } catch {
      btn.innerHTML = '<span class="material-symbols-outlined text-[15px]">close</span> Failed';
    }
    setTimeout(() => { btn.innerHTML = original; }, 1600);
  });
}

/* ---------- rebuilding a finished run from its stored record ---------- */

const TERMINAL_STATES = ["ready", "done", "failed"];

function hydrate(job) {
  $("repo-name").textContent = job.repo_name || job.repo_url;
  $("repo-link").href = job.repo_url;
  if ($("repo-link-2")) $("repo-link-2").href = job.repo_url;
  document.title = `${job.repo_name || job.repo_url} — Zeroth`;
  stopClock();
  $("live-dot").className = TERMINAL_STATES.includes(job.status) ? "dot bg-fg3" : "dot bg-accent pulse";

  if (job.compatibility) renderCompatibility(job.compatibility);
  if (job.fingerprint) {
    renderEvidence(job.fingerprint);
    setStage("fingerprint", "complete", "Complete");
  }
  if (job.manifest) {
    renderServices(job.manifest);
    setStage("reason", "complete", "Complete");
  }

  const artifact = (kind) => (job.artifacts || []).find((a) => a.kind === kind);
  hasOwnConfig = !!artifact("repo_zerops_yaml");
  hasOfficial = !!artifact("official_zerops_yaml");
  const importYaml = artifact("import_yaml");
  const zeropsYaml = artifact("zerops_yaml");
  if (importYaml) $("import-yaml").textContent = importYaml.content;
  if (zeropsYaml) $("zerops-yaml").textContent = zeropsYaml.content;
  if (importYaml || zeropsYaml) {
    $("download").href = `${API}/api/jobs/${job.id}/bundle`;
    renderConfig();
  }

  (job.runs || []).forEach((r) => {
    addAttempt(r.attempt_no, job.verify_target || "zerops");
    if (r.status === "passed") {
      appendAttempt(r.attempt_no, `
        <div><span class="pill pill-verified"><span class="dot bg-success"></span> Verified</span></div>
        <div class="font-mono text-[12px] text-fg2">${escape(JSON.stringify(r.verification || {}))}</div>`);
    } else {
      appendAttempt(r.attempt_no, `
        <div><span class="pill pill-failed">Failed — ${escape(r.failure_class)}</span></div>
        <div class="text-sm text-fg2">${escape(r.failure_message || "")}</div>`);
    }
    if (r.diagnosis) {
      appendAttempt(r.attempt_no, `
        <div class="rounded-xl border border-edge bg-surface p-3 flex flex-col gap-1.5">
          <span class="pill pill-warning w-fit">Repaired &amp; retried</span>
          <div class="text-sm text-fg2"><strong class="text-fg font-medium">Diagnosis:</strong> ${escape(r.diagnosis)}</div>
          ${r.patch_summary ? `<div class="text-sm text-fg2"><strong class="text-fg font-medium">Repair:</strong> ${escape(r.patch_summary)}</div>` : ""}
        </div>`);
    }
  });

  // A replayed run has no live stream, so say that rather than showing an
  // empty console that reads as broken.
  logLine(`[replay] ${job.repo_name || "run"} — finished, live events are not retained`, "text-zinc-500");
  if (job.status === "ready") {
    setStage("verify", "pending", "Not run");
    $("stage-verify-note").textContent =
      "Nothing has been provisioned. Start a verification to prove this configuration boots.";
    showTryout();
  } else if (job.status === "done") {
    setStage("verify", job.verified ? "complete" : "failed", job.verified ? "Verified" : "Not verified");
    $("stage-verify-note").textContent = job.stage_detail || "";
    renderResult(job.verified, job.live_url, job.kept_project_id, job.provider === "simulated");
  } else if (job.status === "failed") {
    setStage("verify", "failed", "Failed");
    $("stage-verify-note").textContent = job.error || job.stage_detail || "The run failed.";
    logLine(`[failed] ${job.error || job.stage_detail || ""}`, "text-red-400");
  }
}

/* ---------- entry ---------- */

async function init() {
  loadGallery();
  initTabs();
  initTryout();
  const jobId = new URLSearchParams(location.search).get("job");

  if (jobId) {
    currentJob = jobId;
    hide("console-panel");
    show("viewing-note");
    resetRun();
    let job = null;
    try {
      const res = await fetch(`${API}/api/jobs/${jobId}`);
      if (res.ok) job = await res.json();
    } catch { /* fall back to the live stream below */ }

    if (job) hydrate(job);

    // The event replay buffer expires after a day, so a finished run has to be
    // rebuilt from its stored record - otherwise every showcase link older than
    // 24h opens blank. A terminal job has nothing live left to send, so there is
    // no reason to open a stream for it at all.
    if (!job || !TERMINAL_STATES.includes(job.status)) listen(jobId);
    return;
  }

  $("run-form").addEventListener("submit", (e) => {
    e.preventDefault();
    const url = $("repo").value.trim();
    if (!url) return;
    $("repo-name").textContent = url;
    $("repo-link").href = url;
    if ($("repo-link-2")) $("repo-link-2").href = url;
    start(url);
  });
}

init();
