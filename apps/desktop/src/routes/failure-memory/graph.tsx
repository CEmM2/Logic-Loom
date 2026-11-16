import { OverlayCanvas } from "../../components/failure-memory/OverlayCanvas";
import { WorkspaceFrame } from "../../components/failure-memory/WorkspaceFrame";
import { useFailureMemoryRoutes } from "../../hooks/failureMemory";

/**
 * The failure-memory graph overlay.
 *
 * An overlay, not a merge: this is its own canvas, and `/api/graph` — the AKMS
 * knowledge graph — is untouched by it. Every node and edge id here is prefixed
 * with the project's `repository_id`, which is what stops two repositories that
 * both contain `src/config.py` from collapsing into a single node when more than
 * one project is in view.
 */
export default function FailureMemoryGraphRoute() {
  return (
    <WorkspaceFrame
      title="Failure Memory — Graph overlay"
      subtitle={
        <>
          <code>source-file → failure_route → failure-lesson</code>, plus{" "}
          <code>load_with</code> co-activation edges. Failure memory overlays the
          source graph; it never writes into it.
        </>
      }
    >
      {({ project }) => <Overlay project={project} />}
    </WorkspaceFrame>
  );
}

function Overlay({ project }: { project: string }) {
  const { data, isLoading } = useFailureMemoryRoutes(project);
  if (isLoading) return <p className="text-sm text-muted-foreground">Loading overlay…</p>;
  if (!data) return null;

  return (
    <div className="space-y-3">
      <div className="flex flex-wrap gap-4 text-xs text-muted-foreground">
        <span>{data.overlay.nodes.length} nodes</span>
        <span>{data.overlay.edges.length} edges</span>
        <span>
          repository_id <code className="font-mono">{data.overlay.repository_id}</code>
        </span>
      </div>
      <div className="h-[640px] rounded border border-border">
        <OverlayCanvas
          overlay={data.overlay}
          emptyMessage={
            data.compiled
              ? "This project has no compiled routes, so the overlay is empty."
              : "No route index has been compiled for this project yet."
          }
        />
      </div>
      <p className="text-xs text-muted-foreground">
        Node ids are namespaced by repository (e.g.{" "}
        <code className="font-mono">
          {data.overlay.repository_id}::file:path/to/file.py
        </code>
        ), so the same relative path in another repository is a different node.
      </p>
    </div>
  );
}
