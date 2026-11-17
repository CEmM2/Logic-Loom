import { useWatermark, type Watermark } from "../hooks/useWatermark";

const ORDER: readonly Watermark[] = ["dim", "off", "full"] as const;
const LABEL: Record<Watermark, string> = {
  full: "Backdrop logo: full",
  dim: "Backdrop logo: dimmed",
  off: "Backdrop logo: hidden",
};

/** Cycles the backdrop logo dimmed → hidden → full, next to the theme toggle. */
export function WatermarkToggle() {
  const { watermark, setWatermark } = useWatermark();
  const next = ORDER[(ORDER.indexOf(watermark) + 1) % ORDER.length];
  return (
    <button
      type="button"
      onClick={() => setWatermark(next)}
      title={`${LABEL[watermark]} (click to change)`}
      aria-label={`${LABEL[watermark]}. Click to change.`}
      data-testid="watermark-toggle"
      className="flex items-center gap-1.5 rounded px-2 py-1 text-sm text-muted-foreground hover:bg-muted hover:text-foreground"
    >
      <svg
        width={16}
        height={16}
        viewBox="0 0 24 24"
        fill="none"
        stroke="currentColor"
        strokeWidth={2}
        strokeLinecap="round"
        strokeLinejoin="round"
        aria-hidden
        opacity={watermark === "off" ? 0.4 : watermark === "dim" ? 0.7 : 1}
      >
        <rect x="3" y="3" width="18" height="18" rx="2" />
        <circle cx="9" cy="9" r="2" />
        <path d="m21 15-5-5L5 21" />
      </svg>
      <span>Logo</span>
    </button>
  );
}
