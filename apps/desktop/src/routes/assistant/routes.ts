/**
 * Assistant sub-route URL constants.
 *
 * Pinned here so every caller references the same URLs and does not drift
 * from guide §2.2. Sub-tabs verbatim from `SectionSideNav`:
 *   Overview · Discover · Batches · Workflows · Outputs · Prompts · Runs · Settings
 *
 * The Overview sub-tab is reached at `/assistant` (index route); the
 * other seven mount at `/assistant/<slug>`.
 */

export const ASSISTANT_ROUTE_OVERVIEW = "/assistant";
export const ASSISTANT_ROUTE_DISCOVER = "/assistant/discover";
export const ASSISTANT_ROUTE_BATCHES = "/assistant/batches";
export const ASSISTANT_ROUTE_WORKFLOWS = "/assistant/workflows";
export const ASSISTANT_ROUTE_OUTPUTS = "/assistant/outputs";
export const ASSISTANT_ROUTE_PROMPTS = "/assistant/prompts";
export const ASSISTANT_ROUTE_RUNS = "/assistant/runs";
export const ASSISTANT_ROUTE_SETTINGS = "/assistant/settings";

/** Ordered list mirroring `SectionSideNav.subnav.assistant`. */
export const ASSISTANT_SUBROUTES = [
  { label: "Overview", path: ASSISTANT_ROUTE_OVERVIEW },
  { label: "Discover", path: ASSISTANT_ROUTE_DISCOVER },
  { label: "Batches", path: ASSISTANT_ROUTE_BATCHES },
  { label: "Workflows", path: ASSISTANT_ROUTE_WORKFLOWS },
  { label: "Outputs", path: ASSISTANT_ROUTE_OUTPUTS },
  { label: "Prompts", path: ASSISTANT_ROUTE_PROMPTS },
  { label: "Runs", path: ASSISTANT_ROUTE_RUNS },
  { label: "Settings", path: ASSISTANT_ROUTE_SETTINGS },
] as const;
