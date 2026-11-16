/**
 * Discover page filter row.
 *
 * Owns the four filter knobs from guide section 5.2:
 *   - only_with_pdf (PINNED DEFAULT: false; hard rule per guide section 5.1)
 *   - open_access_only
 *   - year_from
 *   - year_to
 *
 * State lives on the parent so a single search call sees consistent
 * filters.
 */
import type { DiscoverSearchFilters } from "../../../api/assistantDiscover";

export interface DiscoveryFiltersProps {
  filters: DiscoverSearchFilters;
  onChange: (next: DiscoverSearchFilters) => void;
}

export function DiscoveryFilters({ filters, onChange }: DiscoveryFiltersProps) {
  return (
    <div
      data-testid="discovery-filters"
      className="flex flex-wrap items-end gap-3 rounded-md border bg-card p-3 text-sm"
    >
      <label className="flex items-center gap-2">
        <input
          type="checkbox"
          checked={filters.only_with_pdf ?? false}
          onChange={(e) =>
            onChange({ ...filters, only_with_pdf: e.target.checked })
          }
        />
        Only with PDF
      </label>
      <label className="flex items-center gap-2">
        <input
          type="checkbox"
          checked={filters.open_access_only ?? false}
          onChange={(e) =>
            onChange({ ...filters, open_access_only: e.target.checked })
          }
        />
        Open access only
      </label>
      <label className="flex items-center gap-2">
        From year
        <input
          type="number"
          className="w-20 rounded border px-1 py-0.5"
          value={filters.year_from ?? ""}
          onChange={(e) =>
            onChange({
              ...filters,
              year_from: e.target.value ? Number(e.target.value) : null,
            })
          }
        />
      </label>
      <label className="flex items-center gap-2">
        To year
        <input
          type="number"
          className="w-20 rounded border px-1 py-0.5"
          value={filters.year_to ?? ""}
          onChange={(e) =>
            onChange({
              ...filters,
              year_to: e.target.value ? Number(e.target.value) : null,
            })
          }
        />
      </label>
    </div>
  );
}
