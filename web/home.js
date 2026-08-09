loadGallery();

const byokProvider = $("byok-provider");
if (byokProvider) {
  byokProvider.addEventListener("change", () => {
    $("byok-baseurl-row").hidden = byokProvider.value !== "custom";
  });
}

$("hero-form").addEventListener("submit", async (e) => {
  e.preventDefault();
  const url = $("hero-url").value.trim();
  if (!url) return;

  hide("hero-err");
  const btn = e.target.querySelector("button[type=submit]");
  btn.disabled = true;

  const provider = $("byok-provider") && $("byok-provider").value;
  const key = $("byok-key") && $("byok-key").value.trim();
  const extra = provider && key ? {
    llm_provider: provider,
    llm_api_key: key,
    llm_model: ($("byok-model").value || "").trim() || null,
    llm_base_url: ($("byok-baseurl").value || "").trim() || null,
  } : {};

  try {
    const id = await startJob(url, extra);
    if ($("byok-key")) $("byok-key").value = "";
    location.href = `run.html?job=${encodeURIComponent(id)}`;
  } catch (err) {
    $("hero-err").textContent = err.message;
    show("hero-err");
    btn.disabled = false;
  }
});
