export type Theme = "system" | "light" | "dark";

const KEY = "busticket.theme";

export function storedTheme(): Theme {
  const t = localStorage.getItem(KEY);
  return t === "light" || t === "dark" ? t : "system";
}

/** Apply a theme to <html>. "system" removes the override so the OS setting wins. */
export function applyTheme(theme: Theme): void {
  const root = document.documentElement;
  // Switch instantly: without this, every element with a colour transition fades
  // separately and the page looks broken for a moment.
  root.classList.add("theme-switching");
  requestAnimationFrame(() => requestAnimationFrame(() => root.classList.remove("theme-switching")));
  if (theme === "system") {
    root.removeAttribute("data-theme");
    localStorage.removeItem(KEY);
  } else {
    root.dataset.theme = theme;
    localStorage.setItem(KEY, theme);
  }
}
