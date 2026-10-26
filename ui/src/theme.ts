// docs/ui.md#颜色: the appearance chosen in settings, or the system's while none is chosen.
import { getCurrentWindow } from "@tauri-apps/api/window";

export type Theme = "system" | "light" | "dark";

const DARK = "(prefers-color-scheme: dark)";

export function savedTheme(): Theme {
  const theme = localStorage.getItem("theme");
  return theme === "light" || theme === "dark" ? theme : "system";
}

export function setTheme(theme: Theme) {
  if (theme === "system") localStorage.removeItem("theme");
  else localStorage.setItem("theme", theme);
  apply();
}

/** Applies the appearance now and again whenever the system's changes. */
export function followTheme() {
  apply();
  matchMedia(DARK).addEventListener("change", apply);
}

function apply() {
  const theme = savedTheme();
  document.documentElement.dataset.theme = theme === "system" ? (matchMedia(DARK).matches ? "dark" : "light") : theme;
  // The title bar follows too; the system's own appearance again when none is chosen.
  void getCurrentWindow().setTheme(theme === "system" ? null : theme);
}
