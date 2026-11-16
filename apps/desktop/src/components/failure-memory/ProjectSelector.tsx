import type { FailureMemoryProjectSummary } from "../../api/failureMemory";

interface Props {
  projects: FailureMemoryProjectSummary[];
  selected: string | null;
  onSelect: (id: string) => void;
}

/**
 * Project selector for the workspace header.
 *
 * Shows the project's own `repository_id` next to its Logic-Loom id, because
 * those are different namespaces and the overlay is keyed on the former: two
 * projects can carry the same relative paths and never merge precisely because
 * `repository_id` differs. A project that failed to load stays in the list with
 * its error, rather than vanishing — a missing row is much harder to debug than
 * a red one.
 */
export function ProjectSelector({ projects, selected, onSelect }: Props) {
  const current = projects.find((project) => project.id === selected) ?? null;

  return (
    <div className="flex flex-wrap items-center gap-3">
      <label className="text-sm font-medium" htmlFor="failure-memory-project">
        Project
      </label>
      <select
        id="failure-memory-project"
        className="rounded border border-border bg-background px-2 py-1 text-sm"
        value={selected ?? ""}
        onChange={(event) => onSelect(event.target.value)}
      >
        {projects.map((project) => (
          <option key={project.id} value={project.id}>
            {project.id}
            {project.repository_id ? ` — ${project.repository_id}` : ""}
            {project.available ? "" : " (unavailable)"}
          </option>
        ))}
      </select>

      {current && current.available && (
        <span className="text-xs text-muted-foreground">
          repository_id <code className="font-mono">{current.repository_id}</code> ·
          namespace <code className="font-mono">{current.node_namespace}</code> ·{" "}
          <span title={current.root ?? ""}>{current.root}</span>
        </span>
      )}

      {current && !current.available && (
        <span className="text-xs text-red-600 dark:text-red-400">
          {current.error_code}: {current.error}
        </span>
      )}
    </div>
  );
}
