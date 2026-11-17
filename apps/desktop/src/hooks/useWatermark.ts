import { useCallback, useState } from "react";

/** How strongly the Logic-Loom backdrop logo shows behind the content. */
export type Watermark = "full" | "dim" | "off";

// Keep this key in sync with the no-flash init script in index.html.
export const WATERMARK_STORAGE_KEY = "logic-loom-watermark";
export const DEFAULT_WATERMARK: Watermark = "dim";

export function getStoredWatermark(): Watermark {
  try {
    const value = localStorage.getItem(WATERMARK_STORAGE_KEY);
    if (value === "full" || value === "dim" || value === "off") return value;
  } catch {
    // localStorage unavailable; fall through to the default.
  }
  return DEFAULT_WATERMARK;
}

export function applyWatermark(value: Watermark): void {
  document.documentElement.dataset.watermark = value;
}

/**
 * Backdrop watermark preference. Persisted to localStorage and applied as
 * `data-watermark` on <html>; `globals.css` keys the opacity off it.
 */
export function useWatermark() {
  const [watermark, setState] = useState<Watermark>(getStoredWatermark);
  const setWatermark = useCallback((next: Watermark) => {
    try {
      localStorage.setItem(WATERMARK_STORAGE_KEY, next);
    } catch {
      // Best-effort persistence; still apply for this session.
    }
    setState(next);
    applyWatermark(next);
  }, []);
  return { watermark, setWatermark };
}
