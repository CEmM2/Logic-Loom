/**
 * Renders a [[wikilink]] inline. Resolves via /api/vault/resolve and links
 * to /zsum/vault/file/<path>. Shows as a blue link while resolving (optimistic),
 * and only dims + strikes through when the server confirms the target is gone.
 *
 * Target uses /zsum/vault/file/ (the canonical three-mode path), not the
 * legacy /vault/file/ form, because the latter hits the `/vault/*` redirect
 * in App.tsx which drops the file segment.
 */

import { useQuery } from "@tanstack/react-query";
import { Link } from "react-router-dom";

import { resolveWikilink } from "../api/vault";

export function Wikilink({ value }: { value: string }) {
  const { data } = useQuery({
    queryKey: ["vault", "resolve", value],
    queryFn: () => resolveWikilink(value),
    staleTime: 60_000,
  });

  // Confirmed exists — show as a real navigable link.
  if (data?.exists && data.path) {
    return (
      <Link
        to={`/zsum/vault/file/${encodeURIComponent(data.path)}`}
        className="text-blue-500 underline hover:text-blue-400"
      >
        {value}
      </Link>
    );
  }

  // Confirmed missing — dim and strike through so the broken link is obvious.
  if (data?.exists === false) {
    return (
      <span
        className="text-muted-foreground line-through decoration-dotted decoration-1 italic"
        title="wikilink target not found"
      >
        {value}
      </span>
    );
  }

  // Still resolving — render as a blue non-link so the page looks correct
  // while the query is in flight.
  return (
    <span className="text-blue-500 underline cursor-wait" title="resolving…">
      {value}
    </span>
  );
}
