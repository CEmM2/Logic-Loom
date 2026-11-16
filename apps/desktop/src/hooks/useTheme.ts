import { useCallback, useEffect, useState } from "react";

export type Theme = "light" | "dark" | "system";
export type ResolvedTheme = "light" | "dark";

// Keep this key in sync with the no-flash init script in index.html.
const STORAGE_KEY = "logic-loom-theme";
const DARK_QUERY = "(prefers-color-scheme: dark)";

function getStoredTheme(): Theme {
  try {
    const value = localStorage.getItem(STORAGE_KEY);
    if (value === "light" || value === "dark" || value === "system") {
      return value;
    }
  } catch {
    // localStorage unavailable (SSR/sandbox) — fall through to default.
  }
  return "system";
}

function darkMediaQuery(): MediaQueryList | null {
  if (typeof window === "undefined" || typeof window.matchMedia !== "function") {
    return null;
  }
  return window.matchMedia(DARK_QUERY);
}

function systemPrefersDark(): boolean {
  return darkMediaQuery()?.matches ?? false;
}

export function resolveTheme(theme: Theme): ResolvedTheme {
  if (theme === "system") return systemPrefersDark() ? "dark" : "light";
  return theme;
}

function applyTheme(theme: Theme): void {
  document.documentElement.classList.toggle("dark", resolveTheme(theme) === "dark");
}

/**
 * Theme state for the app shell. Persists the user's choice
 * (light/dark/system) to localStorage and toggles the `.dark` class on
 * <html>, which the Tailwind `darkMode: ["class"]` config keys off. When the
 * choice is "system", it tracks OS preference changes live.
 */
export function useTheme() {
  const [theme, setThemeState] = useState<Theme>(getStoredTheme);

  const setTheme = useCallback((next: Theme) => {
    try {
      localStorage.setItem(STORAGE_KEY, next);
    } catch {
      // Best-effort persistence; still apply for the current session.
    }
    setThemeState(next);
    applyTheme(next);
  }, []);

  // Follow OS preference live while in "system" mode.
  useEffect(() => {
    if (theme !== "system") return;
    const mq = darkMediaQuery();
    if (!mq) return;
    const onChange = () => applyTheme("system");
    mq.addEventListener("change", onChange);
    return () => mq.removeEventListener("change", onChange);
  }, [theme]);

  return { theme, resolvedTheme: resolveTheme(theme), setTheme };
}
