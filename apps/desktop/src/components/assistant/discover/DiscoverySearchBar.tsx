/**
 * Discover page search bar.
 *
 * Single-line query input + submit button. Owns its own input state;
 * the parent owns the resolved query that feeds the search call.
 */
import { useState } from "react";

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
  return (
    <form
      data-testid="discovery-search-bar"
      className="flex gap-2"
      onSubmit={(e) => {
        e.preventDefault();
        if (value.trim()) {
          onSubmit(value.trim());
        }
      }}
    >
      <input
        type="text"
        value={value}
        onChange={(e) => setValue(e.target.value)}
        placeholder="Search OpenAlex and Semantic Scholar"
        className="flex-1 rounded-md border bg-background px-3 py-2 text-sm"
        aria-label="Discovery query"
      />
      <button
        type="submit"
        disabled={isSearching || !value.trim()}
        className="rounded-md border bg-primary px-3 py-2 text-sm text-primary-foreground disabled:opacity-50"
      >
        {isSearching ? "Searching…" : "Search"}
      </button>
    </form>
  );
}
