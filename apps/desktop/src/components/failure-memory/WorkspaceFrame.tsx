import type { ReactNode } from "react";

import type { FailureMemoryProjects } from "../../api/failureMemory";
import { useFailureMemoryProjects, useSelectedProject } from "../../hooks/failureMemory";
import { ProjectSelector } from "./ProjectSelector";

interface Props {
  title: string;
  subtitle?: ReactNode;
  children: (context: { project: string; projects: FailureMemoryProjects }) => ReactNode;
}

/**
 * Shared chrome for every Failure Memory sub-tab: title, project selector, and
 * the read-only permission badge.
 *
 * The permission badge is not decoration. A user looking at a lesson inventory
 * has no other way to tell whether this Logic-Loom can write to the repository
 * it is displaying, and "can this thing touch my registry?" is the first
 * question worth answering. It reads the mode straight from the backend rather
 * than from any local assumption.
 */
export function WorkspaceFrame({ title, subtitle, children }: Props) {
  const { data } = useFailureMemoryProjects();
  const { selected, select } = useSelectedProject(data?.projects ?? []);

  if (!data) return null;

  return (
    <div className="space-y-4 p-6">
      <header className="space-y-2">
        <div className="flex flex-wrap items-center gap-3">
          <h1 className="text-xl font-semibold">{title}</h1>
          <ModeBadge mode={data.mode} operator={data.operator} />
          {data.package_version && (
            <span className="text-xs text-muted-foreground">
              akms-failure-memory {data.package_version}
            </span>
          )}
        </div>
        {subtitle && <p className="max-w-3xl text-sm text-muted-foreground">{subtitle}</p>}
        <ProjectSelector projects={data.projects} selected={selected} onSelect={select} />
      </header>

      {selected ? (
        children({ project: selected, projects: data })
      ) : (
        <p className="text-sm text-muted-foreground">Select a project to continue.</p>
      )}
    </div>
  );
}

function ModeBadge({ mode, operator }: { mode: string; operator: boolean }) {
  return (
    <span
      data-mode={mode}
      title={
        operator
          ? "Operator mode: recording, compilation and refresh are enabled, always through the package — Logic-Loom never edits a registry itself."
          : "Logic-Loom cannot modify any configured failure-memory repository."
      }
      className={`rounded px-2 py-0.5 text-xs font-semibold ${
        operator
          ? "bg-amber-500/15 text-amber-700 dark:text-amber-300"
          : "bg-emerald-500/15 text-emerald-700 dark:text-emerald-300"
      }`}
    >
      {operator ? "operator" : "read-only"}
    </span>
  );
}
