import { useQuery } from "@tanstack/react-query";

import { getAkmsStatus } from "../api/app";
import {
  getLearnExporters,
  getLearnProfiles,
  getLearnStatus,
} from "../api/learn";

export function useAkmsStatus() {
  return useQuery({
    queryKey: ["app", "akms-status"],
    queryFn: getAkmsStatus,
    staleTime: 60_000,
  });
}

export function useLearnStatus(enabled = true) {
  return useQuery({
    queryKey: ["learn", "status"],
    queryFn: getLearnStatus,
    staleTime: 60_000,
    enabled,
  });
}

/**
 * `GET /api/learn/profiles` — the generation-mode catalog with availability
 * status. Drives the capability-driven mode registry. Both
 * read-only discovery endpoints are 200-always, so `enabled` lets the caller
 * skip the fetch until the plugin is known available.
 */
export function useLearnProfiles(enabled = true) {
  return useQuery({
    queryKey: ["learn", "profiles"],
    queryFn: getLearnProfiles,
    staleTime: 60_000,
    enabled,
  });
}

/** `GET /api/learn/exporters` — exporter catalog with availability status. */
export function useLearnExporters(enabled = true) {
  return useQuery({
    queryKey: ["learn", "exporters"],
    queryFn: getLearnExporters,
    staleTime: 60_000,
    enabled,
  });
}
