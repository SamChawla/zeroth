const API = window.ZEROTH_API || "";

const $ = (id) => document.getElementById(id);
const show = (id) => $(id) && $(id).classList.remove("hidden");
const hide = (id) => $(id) && $(id).classList.add("hidden");

function escape(s) {
  return String(s).replace(/[&<>"']/g, (c) =>
    ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[c]));
}

/* ---------- theme ----------
   Three settings, two themes: "system" is stored as a preference but always
   resolved to a concrete data-theme before paint, so no page ever renders in
   an in-between state. The pre-paint resolution lives inline in each document
   head; this block owns the toggle and keeps it in sync with the OS. */

const THEME_KEY = "zeroth-theme";
const THEMES = ["light", "dark", "system"];

function storedTheme() {
  const value = localStorage.getItem(THEME_KEY);
  return THEMES.includes(value) ? value : "system";
}

function systemTheme() {
  return window.matchMedia("(prefers-color-scheme: dark)").matches ? "dark" : "light";
}

function applyTheme(preference) {
  const resolved = preference === "system" ? systemTheme() : preference;
  document.documentElement.setAttribute("data-theme", resolved);
  const meta = document.querySelector('meta[name="theme-color"]');
  if (meta) meta.setAttribute("content", resolved === "dark" ? "#09090B" : "#F8FAFC");
  document.querySelectorAll("[data-theme-option]").forEach((btn) => {
    const active = btn.dataset.themeOption === preference;
    btn.classList.toggle("bg-surface", active);
    btn.classList.toggle("text-fg", active);
    btn.classList.toggle("text-fg3", !active);
    btn.setAttribute("aria-pressed", String(active));
  });
}

function setTheme(preference) {
  localStorage.setItem(THEME_KEY, preference);
  applyTheme(preference);
}

function initTheme() {
  applyTheme(storedTheme());
  document.querySelectorAll("[data-theme-option]").forEach((btn) => {
    btn.addEventListener("click", () => setTheme(btn.dataset.themeOption));
  });
  // Follow the OS only while the user is actually on "system".
  window.matchMedia("(prefers-color-scheme: dark)").addEventListener("change", () => {
    if (storedTheme() === "system") applyTheme("system");
  });
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
        ? `${jobs.length} run${jobs.length === 1 ? "" : "s"} · ${verified} verified`
        : "";
    }
    if (!jobs.length) return jobs;
    hide(emptyId);
    show("gallery-table");
    if ($(targetId)) $(targetId).innerHTML = jobs.map(galleryRow).join("");
    return jobs;
  } catch {
    /* the gallery is decoration on every page; never block rendering on it */
  }
}

function galleryRow(j) {
  const status = j.verified
    ? '<span class="pill pill-verified"><span class="dot bg-success"></span> Verified</span>'
    : '<span class="pill pill-idle">Unverified</span>';
  const services = (j.services || []).slice(0, 3).map((s) =>
    `<span class="pill pill-idle">${escape(s)}</span>`).join(" ");

  return `
    <tr class="border-t border-edge hover:bg-surface2/60 transition-colors">
      <td class="py-3 pr-4">
        <a class="font-mono text-sm text-fg hover:text-accent transition-colors" href="run.html?job=${encodeURIComponent(j.id)}">${escape(j.repo_name)}</a>
      </td>
      <td class="py-3 pr-4">${status}</td>
      <td class="py-3 pr-4 text-sm text-fg2 hidden sm:table-cell">${escape(j.framework || "unknown")}</td>
      <td class="py-3 pr-4 hidden lg:table-cell">${services}</td>
      <td class="py-3 pr-4 text-sm text-fg2 hidden md:table-cell">${j.attempts} attempt${j.attempts === 1 ? "" : "s"}${j.repaired ? " · repaired" : ""}</td>
      <td class="py-3 text-right">
        <a class="text-sm text-accent hover:underline" href="run.html?job=${encodeURIComponent(j.id)}">View</a>
      </td>
    </tr>`;
}

/* ---------- a small, honest status light: is the API actually up? ---------- */

async function checkApiHealth() {
  const el = $("api-status");
  if (!el) return;
  const dot = el.querySelector(".dot");
  const label = el.querySelector("[data-label]");
  try {
    const res = await fetch(`${API}/healthz`);
    if (res.ok) {
      if (dot) dot.className = "dot bg-success pulse";
      if (label) label.textContent = "API online";
      return;
    }
  } catch { /* fall through to the offline state */ }
  if (dot) dot.className = "dot bg-danger";
  if (label) label.textContent = "API unreachable";
}

initTheme();
checkApiHealth();
