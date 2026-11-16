import { NavLink } from "react-router-dom";

import { useLearnStatus } from "../hooks/app";
import { useFailureMemoryProjects } from "../hooks/failureMemory";
import type { Mode } from "./TopModeNav";

/** Section tabs shown in the side nav for each top-level mode. */
export const subnav: Record<Mode, readonly string[]> = {
  zsum: ["Overview", "Pipeline", "Vault", "Collections", "Prompts", "Runs", "Settings"],
  akms: ["Overview", "Batches", "Picker", "Nodes", "Graph", "Prompts", "Workflows", "Runs", "Settings"],
  assistant: ["Overview", "Discover", "Batches", "Workflows", "Outputs", "Prompts", "Runs", "Settings"],
  // MechDSL executable-bridge plugin.
  mechdsl: ["Overview", "Compile", "Artifacts"],
  // Failure Memory workspace. The "Record" tab
  // is appended conditionally below, only when the backend reports operator mode.
  "failure-memory": ["Overview", "Lessons", "Routes", "Preview", "Graph"],
  // akms-records stores (read-only).
  records: ["Overview", "Browse", "Schemas", "Graph"],
};

const NAV_LINK_CLASS = ({ isActive }: { isActive: boolean }) =>
  `rounded px-3 py-1.5 text-sm ${
    isActive
      ? "font-semibold bg-accent text-accent-foreground"
      : "text-muted-foreground hover:text-foreground hover:bg-accent/50"
  }`;

interface SectionSideNavProps {
  mode: Mode;
}

export function SectionSideNav({ mode }: SectionSideNavProps) {
  const items = subnav[mode];
  // Learn tab: only visible in AKMS mode when the backend reports it available.
  // Safe default: hidden on loading / error / unavailable, so a status-fetch
  // failure can never default to the wrong visibility — we default to NOT showing.
  const learnStatusQuery = useLearnStatus(mode === "akms");
  const learnAvailable =
    mode === "akms" && learnStatusQuery.data?.akms_learn_available === true;
  // Record tab: visible only when the backend reports operator mode. Same safe
  // default as Learn — hidden while loading, on error, and in read-only mode.
  // Hiding it is convenience; the 403 from every operator endpoint is the
  // actual control.
  const failureMemoryQuery = useFailureMemoryProjects();
  const operatorAvailable =
    mode === "failure-memory" && failureMemoryQuery.data?.operator === true;

  return (
    <aside
      className="sticky top-0 h-full w-60 border-r border-border bg-background overflow-y-auto"
      aria-label={`${mode} section navigation`}
    >
      <nav className="flex flex-col gap-1 p-2">
        {items.map((item) => {
          const slug = item.toLowerCase();
          const to = item === "Overview" ? `/${mode}` : `/${mode}/${slug}`;
          return (
            <NavLink
              key={item}
              to={to}
              end={item === "Overview"}
              className={NAV_LINK_CLASS}
            >
              {item}
            </NavLink>
          );
        })}
        {operatorAvailable && (
          <NavLink to="/failure-memory/record" className={NAV_LINK_CLASS}>
            Record
          </NavLink>
        )}
        {learnAvailable && (
          <NavLink
            to="/akms/learn"
            className={NAV_LINK_CLASS}
          >
            Learn
          </NavLink>
        )}
      </nav>
    </aside>
  );
}
