/**
 * Discover page search bar.
 *
 * Query box + submit button. Owns its own input state; the parent owns the
 * resolved query that feeds the search call. The box is a wrapping textarea
 * (Enter submits, Shift+Enter adds a line) so a long query stays readable
 * instead of scrolling out of a one-line field. The 500-character cap
 * matches the backend's validation.
 */
import { useState } from "react";

export const MAX_QUERY_LENGTH = 500;

export interface DiscoverySearchBarProps {
  initialQuery?: string;
  isSearching?: boolean;
  onSubmit: (query: string) => void;
}

export function DiscoverySearchBar({
  initialQuery = "",
  isSearching = false,
  onSubmit,
}: DiscoverySearchBarProps) {
  const [value, setValue] = useState(initialQuery);
  const empty = !value.trim();
  return (
    <form
      data-testid="discovery-search-bar"
      className="flex items-start gap-2"
      onSubmit={(e) => {
        e.preventDefault();
        if (value.trim()) {
          onSubmit(value.trim());
        }
      }}
    >
      <textarea
        value={value}
        onChange={(e) => setValue(e.target.value)}
        onKeyDown={(e) => {
          if (e.key === "Enter" && !e.shiftKey && !e.nativeEvent.isComposing) {
            e.preventDefault();
            e.currentTarget.form?.requestSubmit();
          }
        }}
        rows={Math.min(4, Math.max(1, Math.ceil(value.length / 90)))}
        maxLength={MAX_QUERY_LENGTH}
        placeholder="Search OpenAlex and Semantic Scholar"
        className="flex-1 resize-y rounded-md border bg-background px-3 py-2 text-sm"
        aria-label="Discovery query"
      />
      <button
        type="submit"
        disabled={isSearching || empty}
        title={empty ? "Type a search term first." : undefined}
        className="rounded-md border bg-primary px-3 py-2 text-sm text-primary-foreground disabled:opacity-50"
      >
        {isSearching ? "Searching…" : "Search"}
      </button>
    </form>
  );
}
