// Apply a saved theme before first paint, so the page never flashes the wrong one.
// Kept as a file (not inline) so the Content-Security-Policy can forbid inline scripts.
try {
  var t = localStorage.getItem("aashon.theme");
  if (t === "light" || t === "dark") document.documentElement.dataset.theme = t;
} catch (e) {}
