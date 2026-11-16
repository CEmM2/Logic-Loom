/**
 * Capability registry for the Learn tab.
 *
 * The mode + exporter selectors were originally derived by partitioning the flat
 * `LearnStatus.capabilities` string list on the `_export` suffix. That heuristic
 * was lossy (it could not tell an infra capability from a real generation mode)
 * and asymmetric (modes later came from `status.generation_modes`, exporters
 * still from the local partition).
 *
 * The registry is now **capability-driven and symmetric**: modes
 * come from `GET /api/learn/profiles` and exporters from `GET /api/learn/exporters`.
 * Both endpoints return `{id, label, status}` with `status ∈ available |
 * unavailable | planned`, so the UI can render unavailable/planned
 * entries DISABLED instead of letting a user submit one and get a
 * `412 capability_unavailable`. The default selected mode is always an
 * `available` one.
 *
 * The `status`-bearing endpoints are the PRIMARY source. A degrade-gracefully
 * fallback to `status.generation_modes` / the `_export` partition of
 * `status.capabilities` remains so the UI never hard-crashes when an endpoint is
 * unavailable (empty list / older backend); fallback entries are all treated as
 * `available` (the legacy backend exposed no status), which matches Pass-A
 * behavior. The fallback is documented debt: it goes away once every deployment
 * serves `/profiles` + `/exporters`.
 */

import type {
  LearnExporter,
  LearnExporterStatus,
  LearnProfile,
  LearnStatus,
} from "../../api/learn";

const EXPORT_SUFFIX = "_export";

/** A capability status is "selectable" only when the backend reports it available. */
export function isSelectable(status: LearnExporterStatus): boolean {
  return status === "available";
}

/** A generation mode for the selector (sourced from `/profiles`). */
export type LearnMode = {
  /** The capability token, used directly as the compile `generation_option`. */
  id: string;
  /** Human-readable label (backend-provided, or humanized from the id). */
  label: string;
  /** Availability status from the backend (`available` | `unavailable` | `planned`). */
  status: LearnExporterStatus;
  /** True only when `status === "available"` — disabled/non-submittable otherwise. */
  selectable: boolean;
};

/** An exporter option for the selector (sourced from `/exporters`). */
export type LearnExporterOption = {
  /** Bare exporter id sent in the compile `exporters[]`, e.g. `markdown` (suffix stripped). */
  id: string;
  /** Human-readable label, e.g. `Markdown`. */
  label: string;
  /** Availability status from the backend. */
  status: LearnExporterStatus;
  /** True only when `status === "available"`. */
  selectable: boolean;
};

/** Title-case an underscore-delimited token: `deterministic_outline` → `Deterministic Outline`. */
export function humanizeToken(token: string): string {
  return token
    .split("_")
    .filter((part) => part.length > 0)
    .map((part) => part.charAt(0).toUpperCase() + part.slice(1))
    .join(" ");
}

/** Strip the trailing `_export` suffix to the bare exporter id (`markdown_export` → `markdown`). */
function bareExporterId(token: string): string {
  return token.endsWith(EXPORT_SUFFIX)
    ? token.slice(0, -EXPORT_SUFFIX.length)
    : token;
}

/**
 * Build the generation-mode list for the selector from `/profiles`.
 *
 * PRIMARY source: the `profiles` array (each `{id, label, status}`). Entries are
 * rendered in backend order; unavailable/planned entries are kept but marked
 * non-selectable so the UI shows them DISABLED.
 *
 * FALLBACK (degrade-gracefully): when `profiles` is empty/absent (endpoint
 * unavailable or older backend), derive from `status.generation_modes` (the
 * backend-filtered catalog) or, last resort, the `_export` partition of
 * `status.capabilities`. Fallback entries carry `status: "available"` because the
 * legacy backend exposed no per-capability status. This reproduces Pass-A
 * behavior and is documented interim debt.
 */
export function buildModeRegistry(
  profiles: LearnProfile[] | undefined,
  status?: Pick<LearnStatus, "capabilities" | "generation_modes">,
): LearnMode[] {
  if (profiles && profiles.length > 0) {
    return profiles.map((profile) => {
      const profileStatus = profile.status ?? "available";
      return {
        id: profile.id,
        label: profile.label ?? humanizeToken(profile.id),
        status: profileStatus,
        selectable: isSelectable(profileStatus),
      };
    });
  }

  // Fallback: no /profiles payload — degrade to the legacy status-derived list.
  const tokens =
    status?.generation_modes ??
    (status?.capabilities ?? []).filter((token) => !token.endsWith(EXPORT_SUFFIX));
  return tokens.map((token) => ({
    id: token,
    label: humanizeToken(token),
    status: "available" as LearnExporterStatus,
    selectable: true,
  }));
}

/**
 * Build the exporter list for the selector from `/exporters`.
 *
 * PRIMARY source: the `exporters` array (each `{id, label, status}`). The `id`
 * returned by the backend is the FULL `*_export` capability token; the compile
 * request's `exporters[]` expects the BARE id (`markdown`), so the suffix is
 * stripped for `id` while the label prefers the backend's.
 *
 * FALLBACK (degrade-gracefully): when `exporters` is empty/absent, derive from
 * the `_export` partition of `status.capabilities`. Fallback entries carry
 * `status: "available"`.
 */
export function buildExporterRegistry(
  exporters: LearnExporter[] | undefined,
  status?: Pick<LearnStatus, "capabilities">,
): LearnExporterOption[] {
  if (exporters && exporters.length > 0) {
    return exporters.map((exporter) => {
      const exporterStatus = exporter.status ?? "available";
      const id = bareExporterId(exporter.id);
      return {
        id,
        label: exporter.label ?? humanizeToken(id),
        status: exporterStatus,
        selectable: isSelectable(exporterStatus),
      };
    });
  }

  // Fallback: no /exporters payload — degrade to the legacy capability partition.
  return (status?.capabilities ?? [])
    .filter((token) => token.endsWith(EXPORT_SUFFIX))
    .map((token) => {
      const id = bareExporterId(token);
      return {
        id,
        label: humanizeToken(id),
        status: "available" as LearnExporterStatus,
        selectable: true,
      };
    });
}

/** The first selectable (available) mode id, or "" when none are selectable. */
export function defaultSelectableModeId(modes: LearnMode[]): string {
  return modes.find((mode) => mode.selectable)?.id ?? "";
}
