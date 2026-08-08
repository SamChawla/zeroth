const API = window.ZEROTH_API || "";

const $ = (id) => document.getElementById(id);
const show = (id) => $(id).classList.remove("hidden");
const hide = (id) => $(id).classList.add("hidden");

function escape(s) {
  return String(s).replace(/[&<>"']/g, (c) =>
    ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[c]));
}

/* ---------- starting a run (shared by the home hero and the run page) ---------- */

async function startJob(url) {
  const res = await fetch(`${API}/api/jobs`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ repo_url: url }),
  });
  const data = await res.json();
  if (!res.ok) throw new Error(data.detail || "Could not start the run.");
  return data.id;
}

/* ---------- gallery: the showcase every page can pull in ---------- */

async function loadGallery(targetId = "gallery", emptyId = "gallery-empty") {
  try {
    const res = await fetch(`${API}/api/gallery`);
    const jobs = await res.json();
    if ($("gallery-summary")) {
      const verified = jobs.filter((j) => j.verified).length;
      $("gallery-summary").textContent = jobs.length
        ? `${jobs.length} run(s) · ${verified} verified`
        : "";
    }
    if (!jobs.length) return jobs;
    if ($(emptyId)) hide(emptyId);
    $(targetId).innerHTML = jobs.map(galleryCard).join("");
    return jobs;
  } catch {
    /* gallery is decoration on most pages; never block the page on it */
  }
}

function galleryCard(j) {
  return `
    <a href="run.html?job=${encodeURIComponent(j.id)}"
       class="glass-panel rounded-lg p-4 hover:bg-white/5 transition-colors cursor-pointer border border-white/5 block">
      <div class="flex justify-between items-start gap-2 mb-2">
        <span class="font-code-md text-code-md text-secondary truncate">${escape(j.repo_name)}</span>
        ${j.verified
          ? `<span class="shrink-0 flex items-center gap-1 font-label-caps text-label-caps text-emerald-400 bg-emerald-400/10 px-2 py-1 rounded"><span class="w-1.5 h-1.5 rounded-full bg-emerald-400"></span> Verified</span>`
          : `<span class="shrink-0 font-label-caps text-label-caps text-outline bg-surface-container px-2 py-1 rounded">Unverified</span>`}
      </div>
      <div class="font-body-sm text-body-sm text-on-surface-variant mb-4">${j.attempts} attempt(s)${j.repaired ? " · self-repaired" : ""}</div>
      <div class="flex gap-2 flex-wrap">
        <span class="font-label-caps text-label-caps text-outline bg-surface-container px-2 py-1 rounded">${escape(j.framework || "unknown")}</span>
        ${(j.services || []).map((s) => `<span class="font-label-caps text-label-caps text-outline bg-surface-container px-2 py-1 rounded">${escape(s)}</span>`).join("")}
      </div>
    </a>`;
}

/* ---------- a small, honest status light: is the API actually up? ---------- */

async function checkApiHealth() {
  const el = $("api-status");
  if (!el) return;
  const dot = el.querySelector("span");
  try {
    const res = await fetch(`${API}/healthz`);
    if (res.ok) {
      dot.className = "w-2 h-2 rounded-full bg-emerald-500 inline-block pulse-dot";
      el.lastChild.textContent = " API online";
      return;
    }
  } catch { /* fall through to offline state */ }
  dot.className = "w-2 h-2 rounded-full bg-error inline-block";
  el.lastChild.textContent = " API unreachable";
}

checkApiHealth();
