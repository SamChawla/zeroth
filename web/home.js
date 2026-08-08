loadGallery();

$("hero-form").addEventListener("submit", async (e) => {
  e.preventDefault();
  const url = $("hero-url").value.trim();
  if (!url) return;

  hide("hero-err");
  const btn = e.target.querySelector("button[type=submit]");
  btn.disabled = true;

  try {
    const id = await startJob(url);
    location.href = `run.html?job=${encodeURIComponent(id)}`;
  } catch (err) {
    $("hero-err").textContent = err.message;
    show("hero-err");
    btn.disabled = false;
  }
});
