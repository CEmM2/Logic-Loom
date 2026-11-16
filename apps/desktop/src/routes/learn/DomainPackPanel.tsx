/**
 * Domain-pack UI.
 *
 * Renders the `domain_packs[]` array from `GET /api/learn/status`:
 * a status card per domain pack, a source-pack/companion table, capability
 * badges, and a "static-only" export warning. PURE PRESENTATION — the parent owns
 * the query; this component holds no state and triggers no fetch.
 *
 * Rules (the gate):
 *   1. Driven from backend status ONLY (the `domain_packs` prop).
 *   2. NO companion-package import — this module imports only React + the FE type.
 *   3. NO hard-coded computational-mechanics (or any) domain concept. There is no
 *      literal `"compmech"`, `"constkit"`, `"mechdsl"`, `"static_markdown"`, …
 *      anywhere below: every domain id, pack id, source-pack id, role, capability
 *      NAME and capability STATUS is read out of the payload and rendered
 *      generically. The capability-status vocabulary
 *      (`available|planned|unavailable|disabled`) is used ONLY to pick a badge
 *      colour and to decide button visibility — an unknown status renders its raw
 *      string with a neutral style, never crashes.
 *   4. The first compmech pack is a REFERENCE pack, not the UI ontology — so this
 *      component works identically for ANY descriptor (proven by the non-compmech
 *      `foo_domain` test fixture in __tests__/learn_domain_packs.test.tsx).
 */

import type { LearnDomainPack, LearnSourcePack } from "../../api/learn";

interface Props {
  domainPacks: LearnDomainPack[];
}

/**
 * Known capability/source-pack status vocabulary used purely for badge STYLING
 * and button gating (this is a styling map, NOT a domain enum). Statuses
 * outside this set render with a neutral style and their raw text — never crash.
 */
const STATUS_BADGE_CLASS: Record<string, string> = {
  available: "bg-emerald-100 text-emerald-800 border border-emerald-200",
  planned: "bg-sky-100 text-sky-800 border border-sky-200",
  unavailable: "bg-zinc-100 text-zinc-600 border border-zinc-200",
  disabled: "bg-zinc-100 text-zinc-500 border border-zinc-200",
};
const NEUTRAL_BADGE_CLASS = "bg-muted text-muted-foreground border border-border";

function badgeClass(status: string | null | undefined): string {
  const key = (status ?? "").toLowerCase();
  return STATUS_BADGE_CLASS[key] ?? NEUTRAL_BADGE_CLASS;
}

function display(value: string | null | undefined): string {
  return value && value.trim().length > 0 ? value : "—";
}

/**
 * Whether an action button may render for a capability of this status.
 *
 * Rule: no buttons for unavailable executable adapters unless planned. Gate on
 * the capability STATUS only — never the capability NAME. `unavailable` (and
 * `disabled`) → no button. `available` / `planned` (and any other status) → a
 * button MAY render (planned is rendered marked/disabled by the caller).
 */
function mayShowActionButton(status: string | null | undefined): boolean {
  const key = (status ?? "").toLowerCase();
  return key !== "unavailable" && key !== "disabled";
}

/** A capability is "satisfied" (produces output) only when explicitly available. */
function isAvailable(status: string | null | undefined): boolean {
  return (status ?? "").toLowerCase() === "available";
}

function StatusBadge({
  label,
  status,
  testid,
}: {
  label: string;
  status: string | null | undefined;
  testid?: string;
}) {
  return (
    <span
      data-testid={testid}
      className={`inline-flex items-center gap-1 rounded px-1.5 py-0.5 text-[10px] font-medium uppercase tracking-wide ${badgeClass(
        status,
      )}`}
    >
      <span>{label}</span>
      <span className="opacity-70">{display(status)}</span>
    </span>
  );
}

/**
 * Capability badges + per-capability action affordances for one pack.
 *
 * `capabilities` is the descriptor's own name→status map (payload data). We
 * iterate its entries generically. An action button renders only when the
 * status permits (`mayShowActionButton`) — purely status-driven.
 */
function CapabilityBadges({
  packKey,
  capabilities,
}: {
  packKey: string;
  capabilities: Record<string, string>;
}) {
  const entries = Object.entries(capabilities);
  if (entries.length === 0) {
    return (
      <span className="text-xs text-muted-foreground">
        No capabilities advertised.
      </span>
    );
  }
  return (
    <div className="flex flex-wrap gap-1.5" data-testid={`domain-pack-caps-${packKey}`}>
      {entries.map(([name, status]) => (
        <span key={name} className="inline-flex items-center gap-1">
          <StatusBadge
            label={name}
            status={status}
            testid={`domain-pack-cap-${packKey}-${name}`}
          />
          {mayShowActionButton(status) ? (
            // Non-interactive availability affordance for an executable adapter
            // (shown only for available / planned, never for unavailable).
            // This is NOT a clickable action: Logic-Loom has no endpoint to
            // *invoke* a domain-pack adapter, so a "Use" button would be a fake
            // action. It marks executability honestly; a real invoke affordance
            // is deferred until a backend dispatch surface exists.
            <span
              data-testid={`domain-pack-cap-action-${packKey}-${name}`}
              data-available={isAvailable(status) ? "true" : "false"}
              title={
                isAvailable(status)
                  ? `Executable adapter available — invocation is not wired in this build.`
                  : `${name} is ${status} — not yet invocable.`
              }
              className="inline-flex items-center rounded border border-border px-1.5 py-0.5 text-[10px] font-medium text-muted-foreground"
            >
              {isAvailable(status) ? "executable" : "preview"}
            </span>
          ) : null}
        </span>
      ))}
    </div>
  );
}

function SourcePackTable({
  packKey,
  sourcePacks,
}: {
  packKey: string;
  sourcePacks: LearnSourcePack[];
}) {
  if (sourcePacks.length === 0) {
    return (
      <p
        data-testid={`domain-pack-sources-empty-${packKey}`}
        className="text-xs text-muted-foreground"
      >
        No source packs.
      </p>
    );
  }
  return (
    <table
      data-testid={`domain-pack-sources-${packKey}`}
      className="w-full border-collapse text-left text-xs"
    >
      <thead>
        <tr className="text-muted-foreground">
          <th className="border-b border-border py-1 pr-3 font-medium uppercase tracking-wide">
            Source pack
          </th>
          <th className="border-b border-border py-1 pr-3 font-medium uppercase tracking-wide">
            Role
          </th>
          <th className="border-b border-border py-1 font-medium uppercase tracking-wide">
            Status
          </th>
        </tr>
      </thead>
      <tbody>
        {sourcePacks.map((sp, index) => {
          const id = display(sp.source_pack_id);
          return (
            <tr
              key={`${id}-${index}`}
              data-testid={`domain-pack-source-row-${packKey}-${id}`}
            >
              <td className="py-1 pr-3 font-mono">{id}</td>
              <td className="py-1 pr-3">{display(sp.role)}</td>
              <td className="py-1">
                <span
                  data-testid={`domain-pack-source-status-${packKey}-${id}`}
                  className={`inline-flex rounded px-1.5 py-0.5 text-[10px] font-medium uppercase tracking-wide ${badgeClass(
                    sp.status,
                  )}`}
                >
                  {display(sp.status)}
                </span>
              </td>
            </tr>
          );
        })}
      </tbody>
    </table>
  );
}

function DomainPackCard({ pack, index }: { pack: LearnDomainPack; index: number }) {
  // A stable key for testids — pack_id is the natural id, falling back to the
  // array index so a descriptor missing an id still renders deterministically.
  const packKey = pack.pack_id && pack.pack_id.trim().length > 0 ? pack.pack_id : `${index}`;
  const capabilities = pack.capabilities ?? {};
  const sourcePacks = pack.source_packs ?? [];

  // "static-only" / limited-export detection — generic, status-driven:
  // a pack is limited when it advertises ≥1 AVAILABLE capability AND ≥1
  // capability that is NOT available. The warning names whichever capabilities
  // are not available, from the payload — no capability NAME is hard-coded.
  const capEntries = Object.entries(capabilities);
  const availableCaps = capEntries.filter(([, status]) => isAvailable(status));
  const limitedCaps = capEntries.filter(([, status]) => !isAvailable(status));
  const isStaticOnly = availableCaps.length > 0 && limitedCaps.length > 0;

  return (
    <section
      data-testid={`domain-pack-card-${packKey}`}
      className="rounded border border-border bg-muted/30 p-4"
    >
      <div className="flex flex-wrap items-center gap-2">
        <span className="text-sm font-semibold" data-testid={`domain-pack-id-${packKey}`}>
          {display(pack.pack_id)}
        </span>
        <span
          data-testid={`domain-pack-domain-${packKey}`}
          className="rounded bg-foreground/10 px-1.5 py-0.5 text-[10px] uppercase tracking-wide text-muted-foreground"
        >
          {display(pack.domain_id)}
        </span>
        <span
          data-testid={`domain-pack-status-${packKey}`}
          className={`inline-flex rounded px-1.5 py-0.5 text-[10px] font-medium uppercase tracking-wide ${badgeClass(
            pack.status,
          )}`}
        >
          {display(pack.status)}
        </span>
      </div>

      <div className="mt-3">
        <div className="mb-1 text-xs uppercase tracking-wide text-muted-foreground">
          Capabilities
        </div>
        <CapabilityBadges packKey={packKey} capabilities={capabilities} />
      </div>

      {isStaticOnly ? (
        <p
          data-testid={`domain-pack-static-warning-${packKey}`}
          className="mt-3 rounded border border-amber-200 bg-amber-50 p-2 text-xs text-amber-800"
        >
          This pack can only export statically: the following{" "}
          {limitedCaps.length === 1 ? "capability is" : "capabilities are"} not available —{" "}
          {limitedCaps.map(([name, status]) => `${name} (${status})`).join(", ")}.
          Selecting an export that depends on {limitedCaps.length === 1 ? "it" : "them"}{" "}
          will fall back to the static output.
        </p>
      ) : null}

      <div className="mt-3">
        <div className="mb-1 text-xs uppercase tracking-wide text-muted-foreground">
          Source packs
        </div>
        <SourcePackTable packKey={packKey} sourcePacks={sourcePacks} />
      </div>
    </section>
  );
}

/**
 * Domain-pack section. Renders nothing (well — an explicit empty state) when the
 * payload carries no domain packs: an empty registry is a valid state (rule 4
 * above), and an absent `domain_packs` (older payload / unavailable path) is treated
 * the same as an empty array by the default below.
 */
export function DomainPackPanel({ domainPacks }: Props) {
  if (domainPacks.length === 0) {
    return (
      <section
        data-testid="domain-pack-panel-empty"
        className="rounded border border-dashed border-border p-4 text-sm text-muted-foreground"
      >
        No domain packs discovered.
      </section>
    );
  }

  return (
    <section data-testid="domain-pack-panel" className="flex flex-col gap-3">
      <div className="text-xs font-semibold uppercase tracking-wide text-muted-foreground">
        Domain packs
      </div>
      {domainPacks.map((pack, index) => (
        <DomainPackCard
          key={pack.pack_id && pack.pack_id.trim().length > 0 ? pack.pack_id : `pack-${index}`}
          pack={pack}
          index={index}
        />
      ))}
    </section>
  );
}
