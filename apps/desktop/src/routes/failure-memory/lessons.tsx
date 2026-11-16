import { useState } from "react";

import { WorkspaceFrame } from "../../components/failure-memory/WorkspaceFrame";
import {
  useFailureMemoryLesson,
  useFailureMemoryLessons,
} from "../../hooks/failureMemory";
import type { LessonFilters } from "../../api/failureMemory";

/**
 * Lesson inventory with five filters (path, symbol, category, root
 * cause, related lesson) plus free text.
 *
 * "Category" is not a field in the registry schema: the two things an operator
 * means by it are `found_by.type` (how the failure was discovered) and
 * `location.area`, so the backend matches either and enumerates both in the
 * facet list.
 */
export default function FailureMemoryLessonsRoute() {
  return (
    <WorkspaceFrame
      title="Failure Memory — Lessons"
      subtitle="The canonical registry, validated with the package's own registry validator."
    >
      {({ project }) => <Lessons project={project} />}
    </WorkspaceFrame>
  );
}

function Lessons({ project }: { project: string }) {
  const [filters, setFilters] = useState<LessonFilters>({});
  const [openLesson, setOpenLesson] = useState<string | null>(null);
  const { data, isLoading } = useFailureMemoryLessons(project, filters);

  function set(key: keyof LessonFilters, value: string) {
    setFilters((previous) => ({ ...previous, [key]: value }));
  }

  return (
    <div className="space-y-4">
      <section className="grid grid-cols-2 gap-2 sm:grid-cols-3 lg:grid-cols-6">
        <Filter label="Path" value={filters.path ?? ""} onChange={(v) => set("path", v)} />
        <Filter label="Symbol" value={filters.symbol ?? ""} onChange={(v) => set("symbol", v)} />
        <Filter
          label="Category"
          value={filters.category ?? ""}
          onChange={(v) => set("category", v)}
          options={data?.facets.categories}
        />
        <Filter
          label="Root cause"
          value={filters.root_cause ?? ""}
          onChange={(v) => set("root_cause", v)}
        />
        <Filter
          label="Related to"
          value={filters.related ?? ""}
          onChange={(v) => set("related", v)}
        />
        <Filter label="Search" value={filters.q ?? ""} onChange={(v) => set("q", v)} />
      </section>

      {isLoading && <p className="text-sm text-muted-foreground">Loading lessons…</p>}

      {data && (
        <>
          <p className="text-xs text-muted-foreground">
            {data.count} of {data.total} lesson(s)
            {data.count === 0 && data.total > 0 && " — no lesson matches these filters"}
            {data.total === 0 &&
              " — this registry is empty. An empty registry validates, compiles and checks clean while proving nothing; record a lesson before trusting a green tick here."}
          </p>

          <ul className="space-y-2">
            {data.lessons.map((lesson) => (
              <li key={lesson.id} className="rounded border border-border p-3">
                <div className="flex flex-wrap items-baseline gap-2">
                  <button
                    type="button"
                    className="text-sm font-semibold underline-offset-2 hover:underline"
                    onClick={() => setOpenLesson(openLesson === lesson.id ? null : lesson.id)}
                  >
                    {lesson.id}
                  </button>
                  <code className="font-mono text-xs text-muted-foreground">
                    {lesson.node_id}
                  </code>
                  {lesson.documentation_only && (
                    <span
                      className="rounded bg-amber-500/15 px-1.5 py-0.5 text-[10px] font-semibold text-amber-700 dark:text-amber-300"
                      title="Every declared path is documentation. This lesson can only reach a task through a matching advisory tag — unless the task also declares a non-documentation path."
                    >
                      documentation-only
                    </span>
                  )}
                  {!lesson.node_generated && (
                    <span
                      className="rounded bg-muted px-1.5 py-0.5 text-[10px] font-semibold"
                      title="No compiled node file exists for this lesson yet."
                    >
                      not compiled
                    </span>
                  )}
                  {lesson.routed_paths.length === 0 && (
                    <span
                      className="rounded bg-red-500/15 px-1.5 py-0.5 text-[10px] font-semibold text-red-700 dark:text-red-300"
                      title="This lesson has no entry in the compiled route index, so no task can ever match it by path."
                    >
                      unrouted
                    </span>
                  )}
                </div>
                <p className="mt-1 text-sm">{lesson.symptom}</p>
                <p className="mt-1 text-xs text-muted-foreground">
                  {lesson.location.file}
                  {lesson.location.symbol ? ` · ${lesson.location.symbol}` : ""} ·{" "}
                  {lesson.found_by.type} · fixed {lesson.date_fixed}
                </p>
                {openLesson === lesson.id && (
                  <LessonDetailPanel project={project} lessonId={lesson.id} />
                )}
              </li>
            ))}
          </ul>
        </>
      )}
    </div>
  );
}

function LessonDetailPanel({ project, lessonId }: { project: string; lessonId: string }) {
  const { data, isLoading } = useFailureMemoryLesson(project, lessonId);
  if (isLoading) return <p className="mt-2 text-xs text-muted-foreground">Loading…</p>;
  if (!data) return null;
  return (
    <div className="mt-3 space-y-3 border-t border-border pt-3 text-sm">
      <Field label="Root cause" value={data.lesson.root_cause} />
      <Field label="Fix" value={data.lesson.fix} />
      <Field label="Prevention" value={data.lesson.prevention} />
      {data.lesson.notes && <Field label="Notes" value={data.lesson.notes} />}

      <div>
        <div className="text-xs font-semibold text-muted-foreground">Routes</div>
        {data.routes.length === 0 ? (
          <p className="text-xs">
            No compiled route points at this lesson, so no task can match it by path.
          </p>
        ) : (
          <ul className="font-mono text-xs">
            {data.routes.map((route) => (
              <li key={`${route.kind}:${route.key}`}>
                {route.kind}: {route.key}
                {route.documentation ? " (documentation)" : ""}
                {route.source_exists === false ? " — missing in this checkout" : ""}
              </li>
            ))}
          </ul>
        )}
      </div>

      {data.related_lessons.length > 0 && (
        <div>
          <div className="text-xs font-semibold text-muted-foreground">
            Co-activated (load_with)
          </div>
          <ul className="text-xs">
            {data.related_lessons.map((related) => (
              <li key={related.id}>
                {related.id} — {related.symptom}
              </li>
            ))}
          </ul>
        </div>
      )}

      {data.node_markdown && (
        <details>
          <summary className="cursor-pointer text-xs font-semibold text-muted-foreground">
            Compiled node ({data.node_path})
          </summary>
          <pre className="mt-2 max-h-96 overflow-auto rounded bg-muted p-3 text-[11px]">
            {data.node_markdown}
          </pre>
        </details>
      )}
    </div>
  );
}

function Field({ label, value }: { label: string; value: string }) {
  return (
    <div>
      <div className="text-xs font-semibold text-muted-foreground">{label}</div>
      <p>{value}</p>
    </div>
  );
}

function Filter({
  label,
  value,
  onChange,
  options,
}: {
  label: string;
  value: string;
  onChange: (value: string) => void;
  options?: string[];
}) {
  const listId = options ? `facet-${label.replace(/\s+/g, "-").toLowerCase()}` : undefined;
  return (
    <label className="flex flex-col gap-1 text-xs">
      <span className="text-muted-foreground">{label}</span>
      <input
        className="rounded border border-border bg-background px-2 py-1 text-sm"
        value={value}
        list={listId}
        onChange={(event) => onChange(event.target.value)}
      />
      {options && (
        <datalist id={listId}>
          {options.map((option) => (
            <option key={option} value={option} />
          ))}
        </datalist>
      )}
    </label>
  );
}
