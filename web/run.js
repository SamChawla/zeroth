let source = null;
let currentJob = null;
let startTime = null;
let elapsedTimer = null;

/* ---------- terminal ---------- */

function logLine(text, cls = "text-outline") {
  const el = $("terminal");
  if (!el) return;
  const div = document.createElement("div");
  div.className = cls;
  div.textContent = text;
  el.appendChild(div);
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
  pending: "font-label-caps text-label-caps text-outline bg-surface-container px-2 py-1 rounded-DEFAULT",
  active: "font-label-caps text-label-caps text-tertiary bg-tertiary/10 px-2 py-1 rounded-DEFAULT flex items-center gap-1 w-fit",
  complete: "font-label-caps text-label-caps text-primary bg-primary/10 px-2 py-1 rounded-DEFAULT",
  failed: "font-label-caps text-label-caps text-error bg-error/10 px-2 py-1 rounded-DEFAULT",
};

function setStage(stage, state, text) {
  const card = $(`stage-${stage}`);
  const tag = $(`stage-${stage}-tag`);
  if (!card || !tag) return;
  card.classList.toggle("opacity-60", state === "pending");
  tag.className = PILL[state] || PILL.pending;
  tag.innerHTML = state === "active"
    ? `<span class="material-symbols-outlined text-[14px] animate-spin">sync</span> ${escape(text)}`
    : escape(text);
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
  setStage("fingerprint", "pending", "Pending");
  setStage("reason", "pending", "Pending");
  setStage("verify", "pending", "Pending");
  $("run-provider").textContent = "—";
  $("live-dot").className = "w-3 h-3 rounded-full bg-primary pulse-dot";
  show("run");
  startClock();
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
      if (["validating", "ingesting"].includes(msg.status)) setStage("fingerprint", "active", "Running");
      if (["analyzing", "generating"].includes(msg.status)) setStage("reason", "active", "Generating");
      if (msg.status === "verifying") setStage("verify", "active", "Verifying");
      if (msg.status === "failed") logLine(`[failed] ${msg.detail || ""}`, "text-error");
      break;
    case "fingerprint":
      renderEvidence(msg.fingerprint);
      setStage("fingerprint", "complete", "Complete");
      break;
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
      renderConfig();
      break;
    case "ready":
      stopClock();
      setStage("verify", "pending", "Not run");
      $("stage-verify-note").textContent =
        "Nothing has been provisioned. Start a verification to prove this configuration boots.";
      logLine("[ready] configuration generated — nothing deployed", "text-tertiary");
      $("live-dot").className = "w-3 h-3 rounded-full bg-tertiary";
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
      setStage("verify", "active", "Verifying");
      $("stage-verify-note").textContent = `Attempt ${msg.attempt} — provisioning via ${msg.provider}.`;
      $("run-provider").textContent = msg.provider;
      addAttempt(msg.attempt, msg.provider);
      logLine(`> attempt ${msg.attempt} — provisioning via ${msg.provider}`, "text-primary-fixed");
      break;
    case "stage":
      logLine(`  ${msg.stage}… (attempt ${msg.attempt})`);
      appendAttempt(msg.attempt, `<div class="font-body-sm text-body-sm text-on-surface-variant">${escape(msg.stage)}…</div>`);
      break;
    case "attempt_failed":
      logLine(`FAIL attempt ${msg.attempt}: ${msg.failure_class} — ${msg.error || ""}`, "text-error");
      appendAttempt(msg.attempt, `
        <div class="font-label-caps text-label-caps text-error">Failed — ${escape(msg.failure_class)}</div>
        <div class="font-body-sm text-body-sm text-on-surface-variant">${escape(msg.error || "")}</div>
        ${msg.logs ? `<pre class="font-code-sm text-code-sm text-error/80 bg-[#050810] rounded p-3 mt-2 overflow-x-auto">${escape(msg.logs)}</pre>` : ""}`);
      break;
    case "repair_proposed":
      logLine(`REPAIR: ${msg.diagnosis}`, "text-tertiary");
      appendAttempt(msg.attempt, `
        <div class="font-body-sm text-body-sm text-on-surface-variant"><strong class="text-on-surface">Diagnosis:</strong> ${escape(msg.diagnosis)}</div>
        <div class="font-body-sm text-body-sm text-on-surface-variant"><strong class="text-on-surface">Repair:</strong> ${escape(msg.patch_summary)}</div>`);
      break;
    case "attempt_passed":
      logLine(`PASS attempt ${msg.attempt} in ${msg.elapsed}s`, "text-primary-fixed");
      appendAttempt(msg.attempt, `
        <div class="font-label-caps text-label-caps text-primary">Verified in ${msg.elapsed}s</div>
        <div class="font-body-sm text-body-sm text-on-surface-variant">${escape(JSON.stringify(msg.verification || {}))}</div>`);
      setStage("verify", "complete", "Verified");
      break;
    case "kept":
      logLine(`KEPT project ${msg.project_id} in your account — ${msg.url || "no public URL"}`, "text-primary-fixed");
      break;
    case "complete":
      stopClock();
      hide("tryout");
      logLine(`DONE — verified=${msg.verified}`, msg.verified ? "text-primary-fixed" : "text-tertiary");
      if (!msg.verified) setStage("verify", "failed", "Not verified");
      renderResult(msg.verified, msg.live_url, msg.kept_project_id);
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
    <div class="bg-surface-container p-3 rounded-lg border border-white/5">
      <div class="font-label-caps text-label-caps text-on-surface-variant mb-1">${escape(label)}</div>
      <div class="font-code-sm text-code-sm text-on-surface truncate">${escape(value)}</div>
    </div>`).join("");
}

function renderServices(manifest) {
  if (!manifest || !manifest.services) return;
  $("services").innerHTML = manifest.services.map((s) => `
    <div class="bg-surface-container p-3 rounded-lg border border-white/5">
      <div class="flex items-center gap-2 font-code-sm text-code-sm text-on-surface">
        <span class="font-bold">${escape(s.hostname)}</span>
        ${s.public ? '<span class="font-label-caps text-label-caps text-primary bg-primary/10 px-1.5 py-0.5 rounded">public</span>' : ""}
        <span class="text-outline">${escape(s.type)}</span>
      </div>
      <div class="font-body-sm text-body-sm text-on-surface-variant mt-1">${escape(s.reason || "No reason recorded.")}</div>
    </div>`).join("");
}

function addAttempt(n, provider) {
  if ($(`attempt-${n}`)) return;
  const el = document.createElement("div");
  el.className = "bg-surface-container rounded-lg border border-white/5 p-4";
  el.id = `attempt-${n}`;
  el.innerHTML = `<div class="font-label-caps text-label-caps text-on-surface-variant mb-2">Attempt ${n} — via ${escape(provider)}</div><div id="attempt-${n}-body" class="flex flex-col gap-2"></div>`;
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
    || '<p class="font-body-sm text-body-sm text-on-surface-variant">No services were generated.</p>';
  renderBootLog(null);
  show("result-grid");
}

function renderBootLog(verified) {
  const state = verified === null
    ? { dot: "bg-outline", pill: "text-outline bg-surface-container", label: "NOT DEPLOYED" }
    : verified
      ? { dot: "bg-emerald-400", pill: "text-emerald-400 bg-emerald-400/10", label: "HEALTHY" }
      : { dot: "bg-outline", pill: "text-outline bg-surface-container", label: "UNVERIFIED" };

  $("boot-log").innerHTML = "";
  document.querySelectorAll("#services > div").forEach((svcEl) => {
    const hostname = svcEl.querySelector(".font-bold")?.textContent || "";
    $("boot-log").insertAdjacentHTML("beforeend", `
      <div class="flex items-center justify-between p-3 rounded bg-surface/50 border border-white/5">
        <div class="flex items-center gap-3">
          <span class="w-2 h-2 rounded-full ${state.dot}"></span>
          <span class="font-code-sm text-code-sm font-bold">${escape(hostname)}</span>
        </div>
        <span class="font-label-caps text-label-caps ${state.pill} px-2 py-1 rounded">${state.label}</span>
      </div>`);
  });
}

function renderResult(verified, liveUrl, keptProjectId) {
  const banner = $("result-banner");
  banner.className = `glass-panel rounded-xl p-8 flex flex-col md:flex-row items-center justify-between gap-6 border-l-4 ${
    verified ? "border-l-emerald-400 bg-emerald-500/10" : "border-l-tertiary bg-tertiary/10"
  }`;

  const kept = verified && keptProjectId
    ? `<p class="font-body-sm text-body-sm text-on-surface-variant mt-2">Left running in your account as project <span class="font-code-sm">${escape(keptProjectId)}</span>.</p>`
    : verified
      ? '<p class="font-body-sm text-body-sm text-on-surface-variant mt-2">The throwaway project has been destroyed.</p>'
      : "";
  const link = verified && liveUrl
    ? `<a href="${escape(liveUrl)}" target="_blank" rel="noopener" class="flex items-center gap-2 px-6 py-3 bg-primary text-on-primary rounded font-body-sm hover:brightness-110 transition-all whitespace-nowrap">
         <span class="material-symbols-outlined text-[18px]">open_in_new</span> Open it
       </a>`
    : "";

  banner.innerHTML = `
    <div class="flex items-center gap-6">
      <div class="w-16 h-16 rounded-full flex items-center justify-center shrink-0 ${verified ? "bg-success-container" : "bg-surface-container-high"}">
        <span class="material-symbols-outlined text-4xl ${verified ? "text-on-success-container" : "text-tertiary"}">${verified ? "check_circle" : "report"}</span>
      </div>
      <div>
        <h1 class="font-headline-xl text-headline-xl ${verified ? "text-primary-fixed" : "text-tertiary-fixed"} mb-2">${verified ? "VERIFIED" : "NOT VERIFIED"}</h1>
        <p class="font-body-lg text-body-lg text-on-surface-variant">${verified
          ? "Zeroth deployed this repository and confirmed it started."
          : "It did not come up within the attempt limit. Review the trail above before deploying by hand."}</p>
        ${kept}
      </div>
    </div>
    ${link}`;
  show("result-banner");

  renderBootLog(verified);
  renderConfig();
}

/* ---------- try it out ---------- */

function showTryout() {
  $("tryout-go").disabled = false;
  show("tryout");
}

function tryoutError(message) {
  const el = $("tryout-err");
  el.textContent = message;
  show("tryout-err");
  $("tryout-go").disabled = false;
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
      body: JSON.stringify({ target, token: target === "account" ? token : null }),
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
    $("live-dot").className = "w-3 h-3 rounded-full bg-primary pulse-dot";
    setStage("verify", "active", "Verifying");
    startClock();
    logLine(`> verification requested — target ${target}`, "text-primary-fixed");
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
}

/* ---------- config tabs ---------- */

function initTabs() {
  const importTab = $("tab-import"), zeropsTab = $("tab-zerops");
  const importPre = $("import-yaml"), zeropsPre = $("zerops-yaml");
  const activate = (tab, other, pre, otherPre) => {
    tab.classList.add("border-primary", "text-primary");
    tab.classList.remove("border-transparent", "text-on-surface-variant");
    other.classList.remove("border-primary", "text-primary");
    other.classList.add("border-transparent", "text-on-surface-variant");
    pre.classList.remove("hidden");
    otherPre.classList.add("hidden");
  };
  importTab.addEventListener("click", () => activate(importTab, zeropsTab, importPre, zeropsPre));
  zeropsTab.addEventListener("click", () => activate(zeropsTab, importTab, zeropsPre, importPre));
}

/* ---------- entry ---------- */

async function init() {
  initTabs();
  initTryout();
  const jobId = new URLSearchParams(location.search).get("job");

  if (jobId) {
    currentJob = jobId;
    hide("console-panel");
    show("viewing-note");
    resetRun();
    try {
      const res = await fetch(`${API}/api/jobs/${jobId}`);
      if (res.ok) {
        const job = await res.json();
        $("repo-name").textContent = job.repo_name || job.repo_url;
        $("repo-link").href = job.repo_url;
        document.title = `${job.repo_name || job.repo_url} — Zeroth`;
      }
    } catch { /* the live stream still renders without this */ }
    listen(jobId);
    return;
  }

  $("run-form").addEventListener("submit", (e) => {
    e.preventDefault();
    const url = $("repo").value.trim();
    if (!url) return;
    $("repo-name").textContent = url;
    $("repo-link").href = url;
    start(url);
  });
}

init();
