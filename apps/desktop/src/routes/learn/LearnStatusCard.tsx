/**
 * Plugin status card (UI element 1).
 *
 * Renders the akms-learn plugin metadata reported by `GET /api/learn/status`:
 * version, plugin API, learning-packet schema, capability badges, and any
 * warnings. Pure presentation — the parent owns the query.
 */

import type { LearnStatus } from "../../api/learn";

interface Props {
  status: LearnStatus;
}

function Field({ label, value }: { label: string; value: string }) {
  return (
    <div className="flex flex-col">
      <dt className="text-xs uppercase tracking-wide text-muted-foreground">{label}</dt>
      <dd className="text-sm font-medium">{value}</dd>
    </div>
  );
}

export function LearnStatusCard({ status }: Props) {
  const warnings = status.warnings ?? [];

  return (
    <section
      data-testid="learn-status-card"
      className="rounded border border-border bg-muted/30 p-4"
    >
      <div className="mb-3 text-xs font-semibold uppercase tracking-wide text-muted-foreground">
        Plugin status
      </div>

      <dl className="grid grid-cols-2 gap-3 sm:grid-cols-3">
        <Field label="Version" value={status.version ?? "—"} />
        <Field label="Plugin API" value={status.plugin_api ?? "—"} />
        <Field label="Packet schema" value={status.learning_packet_schema ?? "—"} />
      </dl>

      <div className="mt-3">
        <div className="mb-1 text-xs uppercase tracking-wide text-muted-foreground">
          Capabilities
        </div>
        {status.capabilities.length === 0 ? (
          <span className="text-sm text-muted-foreground">None advertised.</span>
        ) : (
          <div className="flex flex-wrap gap-1.5">
            {status.capabilities.map((cap) => (
              <span
                key={cap}
                className="rounded bg-foreground/10 px-2 py-0.5 text-xs font-medium"
              >
                {cap}
              </span>
            ))}
          </div>
        )}
      </div>

      {warnings.length > 0 ? (
        <ul
          data-testid="learn-status-warnings"
          className="mt-3 list-disc space-y-1 pl-5 text-xs text-amber-700"
        >
          {warnings.map((warning, index) => (
            <li key={`${index}-${warning}`}>{warning}</li>
          ))}
        </ul>
      ) : null}
    </section>
  );
}
