(() => {
  const host = location.hostname;
  const params = new URLSearchParams(location.search);
  const preview = host.startsWith("next.") || host.endsWith(".workers.dev") || params.has("preview");
  if (!preview) {
    return;
  }

  const STABLE = "https://docs.femforge.io";
  const ACK = "femforge-preview-ack";
  const icon = '<svg viewBox="0 0 24 24" aria-hidden="true"><path fill="currentColor" d="M1 21h22L12 2 1 21zm12-3h-2v-2h2v2zm0-4h-2v-4h2v4z"/></svg>';

  const stableUrl = () => STABLE + location.pathname + location.hash;

  const bar = document.createElement("div");
  bar.className = "ff-preview-bar";
  bar.setAttribute("role", "status");
  bar.innerHTML =
    icon +
    '<span class="ff-preview-bar__text">Preview docs for unreleased code. Pages here can describe behaviour that is broken or untested.</span>' +
    '<a class="ff-preview-bar__link" href="' + stableUrl() + '">Go to the released docs</a>';
  document.body.appendChild(bar);
  document.body.classList.add("ff-preview");

  const dialog = document.createElement("dialog");
  dialog.className = "ff-preview-dialog";
  dialog.innerHTML =
    '<div class="ff-preview-dialog__icon">' + icon + "</div>" +
    "<h2>You are reading the preview docs</h2>" +
    "<p>This site is built from unreleased code. What it describes can be broken, untested or about to change. The released docs match the version you get from <code>uv add femforge</code>.</p>" +
    '<div class="ff-preview-dialog__actions">' +
    '<a class="md-button md-button--primary" href="' + stableUrl() + '">Take me to the released docs</a>' +
    '<button type="button" class="md-button">Continue here</button>' +
    "</div>";
  document.body.appendChild(dialog);
  dialog.querySelector("button").addEventListener("click", () => {
    try {
      sessionStorage.setItem(ACK, "1");
    } catch {
      // Storage can be unavailable; the dialog then returns on the next page.
    }
    dialog.close();
  });

  let acknowledged = false;
  try {
    acknowledged = sessionStorage.getItem(ACK) === "1";
  } catch {
    acknowledged = false;
  }
  if (!acknowledged) {
    dialog.showModal();
  }

  const update = () => {
    for (const link of document.querySelectorAll(".ff-preview-bar__link, .ff-preview-dialog a")) {
      link.href = stableUrl();
    }
  };
  if (window.document$ && typeof window.document$.subscribe === "function") {
    window.document$.subscribe(update);
  }
})();
