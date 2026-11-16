type Props = {
  label: string;
  className?: string;
  progressPercent?: number | null;
};

export function IndeterminateProgressBar({
  label,
  className = "",
  progressPercent = null,
}: Props) {
  const clampedPercent =
    typeof progressPercent === "number"
      ? Math.max(0, Math.min(100, Math.round(progressPercent)))
      : null;

  return (
    <div
      role="status"
      aria-live="polite"
      className={`space-y-1 ${className}`.trim()}
    >
      <div className="flex items-center justify-between gap-2 text-[11px] text-muted-foreground">
        <div className="flex items-center gap-2">
          <span className="inline-block h-2 w-2 animate-pulse rounded-full bg-primary" />
          <span>{label}</span>
        </div>
        {clampedPercent !== null && <span className="font-mono text-[10px]">{clampedPercent}%</span>}
      </div>
      <div className="h-1.5 overflow-hidden rounded-full bg-muted/80">
        {clampedPercent !== null ? (
          <div
            className="h-full rounded-full bg-primary transition-[width] duration-300 ease-out"
            style={{ width: `${clampedPercent}%` }}
          />
        ) : (
          <div className="h-full w-2/5 animate-pulse rounded-full bg-primary" />
        )}
      </div>
    </div>
  );
}