import { useQuery } from "@tanstack/react-query";

import { listVaultCollections } from "../../api/vault";
import { useCollections } from "../../hooks/picker";
import type { FilterSpec } from "../../api/picker";

interface Props {
  value: FilterSpec;
  onChange: (next: FilterSpec) => void;
}

export function FilterBar({ value, onChange }: Props) {
  const { data: akmsCollections = [] } = useCollections();
  const { data: vaultCollections = [] } = useQuery({
    queryKey: ["vault", "collections"],
    queryFn: listVaultCollections,
    enabled: akmsCollections.length === 0,
  });

  const collections =
    akmsCollections.length > 0
      ? akmsCollections
      : vaultCollections.map((c) => ({ name: c.name, paper_count: 0 }));

  const update = (patch: Partial<FilterSpec>) => onChange({ ...value, ...patch });

  return (
    <div className="flex flex-wrap items-center gap-2 border-b border-border p-3 text-sm">
      <input
        className="min-w-[14rem] flex-1 rounded border border-border bg-background px-2 py-1"
        placeholder="Search title / author / abstract…"
        value={value.q ?? ""}
        onChange={(e) => update({ q: e.target.value || undefined })}
      />
      <select
        className="rounded border border-border bg-background px-2 py-1"
        value={value.collection ?? ""}
        onChange={(e) => update({ collection: e.target.value || undefined })}
      >
        <option value="">All collections</option>
        {collections.map((c) => (
          <option key={c.name} value={c.name}>
            {c.paper_count > 0 ? `${c.name} (${c.paper_count})` : c.name}
          </option>
        ))}
      </select>
      <input
        type="number"
        className="w-24 rounded border border-border bg-background px-2 py-1"
        placeholder="From"
        value={value.year_from ?? ""}
        onChange={(e) =>
          update({ year_from: e.target.value ? Number(e.target.value) : undefined })
        }
      />
      <input
        type="number"
        className="w-24 rounded border border-border bg-background px-2 py-1"
        placeholder="To"
        value={value.year_to ?? ""}
        onChange={(e) =>
          update({ year_to: e.target.value ? Number(e.target.value) : undefined })
        }
      />
      <label className="flex items-center gap-1">
        <input
          type="checkbox"
          checked={value.only_with_pdf ?? false}
          onChange={(e) => update({ only_with_pdf: e.target.checked || undefined })}
        />
        PDF only
      </label>
    </div>
  );
}
