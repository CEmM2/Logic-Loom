/**
 * Prompt Library API client (routes at /api/prompts).
 *
 * The Prompt Library page consumes this module to list,
 * inspect, edit, duplicate, activate, and manage variants + profiles. The
 * backend exposes CRUD over the eight canonical slot ids (zsum.* +
 * akms.*) plus validate / import / export / resolve convenience endpoints.
 *
 * Note: variants are addressed by a combined (slot, variant_id) pair —
 * `variant_id` goes in the URL path, `slot` is a required query param.
 */
import { api } from "./client";

// ── Slot families ────────────────────────────────────────────────────────────

export type SlotFamily = "zsum" | "akms" | "assistant";
export type VariantSource = "bundled" | "user";
export type VariantContentType = "markdown" | "text";

// ── Types mirroring services/logic_loom_api/.../prompts/models.py ────────────

export type PromptVariant = {
  schema_version: 1;
  id: string;
  family: SlotFamily;
  slot: string;
  name: string;
  description: string;
  source: VariantSource;
  based_on: string | null;
  filename: string;
  content_type: VariantContentType;
  content_hash: string;
  content: string;
  output_schema_id: string | null;
};

export type PromptSchema = {
  schema_version: 1;
  id: string;
  name: string;
  description: string;
  source: VariantSource;
  family?: SlotFamily | null;
  content: Record<string, unknown>;
};

export type ListSchemasResponse = {
  schemas: PromptSchema[];
  bundled: PromptSchema[];
};

export type SchemaPayload = {
  id?: string | null;
  name: string;
  description?: string;
  family?: SlotFamily | null;
  content: Record<string, unknown>;
};

export type PromptProfile = {
  schema_version: 1;
  id: string;
  name: string;
  description: string;
  active: Record<string, string>;
};

export type ActivePromptProfile = {
  schema_version: 1;
  profile_id: string | null;
  active: Record<string, string>;
};

export type EffectivePromptSet = {
  schema_version: 1;
  slot_to_variant: Record<string, string>;
  contents: Record<string, string>;
};

export type VariantPayload = {
  id?: string | null;
  family?: SlotFamily | null;
  slot: string;
  name: string;
  description?: string;
  based_on?: string | null;
  filename: string;
  content_type?: VariantContentType;
  content?: string;
  output_schema_id?: string | null;
};

export type ProfilePayload = {
  id?: string | null;
  name: string;
  description?: string;
  active?: Record<string, string>;
};

export type ListVariantsResponse = {
  variants: PromptVariant[];
  bundled: PromptVariant[];
};

export type ListProfilesResponse = {
  profiles: PromptProfile[];
  active: ActivePromptProfile;
};

export type ResolveRequest = {
  batch_prompt_profile_id?: string | null;
  batch_prompt_overrides?: Record<string, string>;
  request_profile_id?: string | null;
  request_overrides?: Record<string, string>;
};

// ── Slot enumeration ─────────────────────────────────────────────────────────

export async function listSlots() {
  return api<{ slots: string[] }>("/api/prompts/slots");
}

// ── Variants ────────────────────────────────────────────────────────────────

export async function listVariants(slot?: string | null) {
  const q = slot ? `?slot=${encodeURIComponent(slot)}` : "";
  return api<ListVariantsResponse>(`/api/prompts/variants${q}`);
}

export async function getVariant(variantId: string, slot: string) {
  return api<PromptVariant>(
    `/api/prompts/variant/${encodeURIComponent(variantId)}?slot=${encodeURIComponent(slot)}`,
  );
}

export async function createVariant(body: VariantPayload) {
  return api<PromptVariant>("/api/prompts/variant", {
    method: "POST",
    headers: { "content-type": "application/json" },
    body: JSON.stringify(body),
  });
}

export async function updateVariant(variantId: string, body: VariantPayload) {
  return api<PromptVariant>(`/api/prompts/variant/${encodeURIComponent(variantId)}`, {
    method: "PUT",
    headers: { "content-type": "application/json" },
    body: JSON.stringify(body),
  });
}

export async function deleteVariant(variantId: string, slot: string) {
  return api<{ deleted: true; slot: string; id: string }>(
    `/api/prompts/variant/${encodeURIComponent(variantId)}?slot=${encodeURIComponent(slot)}`,
    { method: "DELETE" },
  );
}

export async function duplicateVariant(
  variantId: string,
  slot: string,
  newId?: string | null,
) {
  const params = new URLSearchParams({ slot });
  if (newId) params.set("new_id", newId);
  return api<PromptVariant>(
    `/api/prompts/variant/${encodeURIComponent(variantId)}/duplicate?${params}`,
    { method: "POST" },
  );
}

export async function activateSlot(slot: string, variantRef: string) {
  return api<ActivePromptProfile>(
    `/api/prompts/slot/${encodeURIComponent(slot)}/activate`,
    {
      method: "POST",
      headers: { "content-type": "application/json" },
      body: JSON.stringify({ variant_ref: variantRef }),
    },
  );
}

// ── Profiles ────────────────────────────────────────────────────────────────

export async function listProfiles() {
  return api<ListProfilesResponse>("/api/prompts/profiles");
}

export async function createProfile(body: ProfilePayload) {
  return api<PromptProfile>("/api/prompts/profiles", {
    method: "POST",
    headers: { "content-type": "application/json" },
    body: JSON.stringify(body),
  });
}

export async function updateProfile(profileId: string, body: ProfilePayload) {
  return api<PromptProfile>(`/api/prompts/profiles/${encodeURIComponent(profileId)}`, {
    method: "PUT",
    headers: { "content-type": "application/json" },
    body: JSON.stringify(body),
  });
}

// ── Validate / Import / Export ──────────────────────────────────────────────

export async function validateVariant(payload: {
  filename: string;
  content_type?: VariantContentType;
  content?: string;
}) {
  return api<{
    ok: true;
    filename: string;
    content_type: VariantContentType;
    warnings: string[];
  }>("/api/prompts/validate", {
    method: "POST",
    headers: { "content-type": "application/json" },
    body: JSON.stringify(payload),
  });
}

export async function importBundle(payload: {
  variants?: VariantPayload[];
  profiles?: ProfilePayload[];
}) {
  return api<{ imported_variants: number; imported_profiles: number }>(
    "/api/prompts/import",
    {
      method: "POST",
      headers: { "content-type": "application/json" },
      body: JSON.stringify(payload),
    },
  );
}

export async function exportBundle(profileId: string) {
  return api<{ profile: PromptProfile; variants: PromptVariant[] }>(
    `/api/prompts/export/${encodeURIComponent(profileId)}`,
  );
}

// ── Resolve (effective prompts preview) ─────────────────────────────────────

export async function resolveEffectivePrompts(body: ResolveRequest) {
  return api<EffectivePromptSet>("/api/prompts/resolve", {
    method: "POST",
    headers: { "content-type": "application/json" },
    body: JSON.stringify(body),
  });
}

// ── Schemas (decoupled JSON output schemas) ─────────────────────────────────

export async function listSchemas(family?: SlotFamily | null) {
  const path = family
    ? `/api/prompts/schemas?family=${encodeURIComponent(family)}`
    : "/api/prompts/schemas";
  return api<ListSchemasResponse>(path);
}

export async function getSchema(schemaId: string) {
  return api<PromptSchema>(`/api/prompts/schemas/${encodeURIComponent(schemaId)}`);
}

export async function createSchema(payload: SchemaPayload) {
  return api<PromptSchema>("/api/prompts/schemas", {
    method: "POST",
    headers: { "content-type": "application/json" },
    body: JSON.stringify(payload),
  });
}

export async function updateSchema(schemaId: string, payload: SchemaPayload) {
  return api<PromptSchema>(`/api/prompts/schemas/${encodeURIComponent(schemaId)}`, {
    method: "PUT",
    headers: { "content-type": "application/json" },
    body: JSON.stringify(payload),
  });
}

export async function deleteSchema(schemaId: string) {
  return api<{ deleted: boolean; id: string }>(
    `/api/prompts/schemas/${encodeURIComponent(schemaId)}`,
    { method: "DELETE" },
  );
}

// ── Slot family helpers ─────────────────────────────────────────────────────

export function slotFamily(slotId: string): SlotFamily {
  if (slotId.startsWith("zsum.")) return "zsum";
  if (slotId.startsWith("assistant.")) return "assistant";
  return "akms";
}

export function filterSlotsByFamily(slots: string[], family: SlotFamily | null): string[] {
  if (!family) return slots;
  return slots.filter((s) => slotFamily(s) === family);
}
