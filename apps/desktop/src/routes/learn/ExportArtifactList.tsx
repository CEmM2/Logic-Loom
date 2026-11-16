/**
 * Export artifact list (UI element 8).
 *
 * Renders `LearnCompileResponse.exports` (`{path, type}[]`) and links each to the
 * artifact-file endpoint via `learnArtifactUrl(artifact_id, <relative path>)`.
 *
 * Resilience note: `exports[].path` MAY be an absolute
 * filesystem path. The artifact endpoint is keyed by a relative `{path}` segment,
 * so we build the href best-effort from the basename — and always render the raw
 * path text regardless. A malformed/absolute path must never crash the list.
 *
 * `learnArtifactUrl` is async (it awaits the random API base), so hrefs are
 * resolved in an effect and held in state.
 */

import { useEffect, useState } from "react";

import { learnArtifactUrl, type LearnArtifact } from "../../api/learn";

interface Props {
  artifactId: string;
  exports: LearnArtifact[];
}

/** Last path segment of a (possibly absolute) path, used as the URL `{path}` key. */
function basename(path: string): string {
  const parts = path.split("/").filter((segment) => segment.length > 0);
  return parts.length > 0 ? parts[parts.length - 1] : path;
}

export function ExportArtifactList({ artifactId, exports }: Props) {
  // Map of export path → resolved href (best-effort; absent until resolved).
  const [hrefs, setHrefs] = useState<Record<string, string>>({});

  useEffect(() => {
    let cancelled = false;
    void Promise.all(
      exports.map(async (artifact) => {
        try {
          const url = await learnArtifactUrl(artifactId, basename(artifact.path));
          return [artifact.path, url] as const;
        } catch {
          return [artifact.path, ""] as const;
        }
      }),
    ).then((entries) => {
      if (cancelled) return;
      const next: Record<string, string> = {};
      for (const [path, url] of entries) {
        if (url) next[path] = url;
      }
      setHrefs(next);
    });
    return () => {
      cancelled = true;
    };
  }, [artifactId, exports]);

  if (exports.length === 0) {
    return <p className="text-sm text-muted-foreground">No artifacts exported.</p>;
  }

  return (
    <ul data-testid="learn-export-list" className="space-y-1 text-sm">
      {exports.map((artifact, index) => {
        const href = hrefs[artifact.path];
        return (
          <li
            key={`${index}-${artifact.path}`}
            className="flex items-center gap-2 rounded border border-border px-2 py-1"
          >
            <span className="rounded bg-foreground/10 px-1.5 py-0.5 text-xs font-medium">
              {artifact.type}
            </span>
            {href ? (
              <a
                href={href}
                target="_blank"
                rel="noreferrer"
                className="truncate text-blue-600 hover:underline"
                title={artifact.path}
              >
                {artifact.path}
              </a>
            ) : (
              <span className="truncate text-muted-foreground" title={artifact.path}>
                {artifact.path}
              </span>
            )}
          </li>
        );
      })}
    </ul>
  );
}
