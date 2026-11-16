/**
 * Schema metadata for the zsum config editor.
 *
 * Ported from plugin_dev/src/config.ts. Includes:
 * - field input type (text / number / toggle / dropdown / textarea-list)
 * - section grouping
 * - per-field validation/serialization
 * - helpers to walk a parsed YAML dict by dotted path
 *
 * Shape kept identical to the Obsidian plugin so users get the same form
 * and the YAML round-trip is byte-stable for unchanged fields.
 */

export type ZsumLlmProvider = "anthropic" | "openai_compat" | "codex_exec" | "gemini_cli";
export type ZsumCodexSandbox = "read-only" | "workspace-write" | "danger-full-access";
export type ZsumFieldInput = "text" | "number" | "toggle" | "dropdown" | "textarea-list";

export interface FieldOption {
  value: string;
  label: string;
}

export interface FieldDef {
  path: string; // dotted, e.g. "llm.routing.cards"
  label: string;
  section: string;
  input: ZsumFieldInput;
  description?: string;
  options?: readonly FieldOption[];
  /** Used to seed an unset field; never sent unless the user touches it. */
  defaultValue: string | boolean | null;
  /** integer: enforced when input==="number"; default true. */
  integer?: boolean;
  /** nullable: empty number string → null instead of validation error. */
  nullable?: boolean;
}

export interface SectionDef {
  id: string;
  label: string;
  fields: readonly FieldDef[];
}

export interface LocalModelDefaults {
  model: string | null;
  base_url: string | null;
  api_key: string | null;
  routing_provider: ZsumLlmProvider | null;
}

const LLM_PROVIDER_OPTIONS: readonly FieldOption[] = [
  { value: "anthropic", label: "Anthropic" },
  { value: "openai_compat", label: "OpenAI Compatible" },
  { value: "codex_exec", label: "Codex Exec" },
  { value: "gemini_cli", label: "Gemini CLI" },
];

const CODEX_SANDBOX_OPTIONS: readonly FieldOption[] = [
  { value: "read-only", label: "Read Only" },
  { value: "workspace-write", label: "Workspace Write" },
  { value: "danger-full-access", label: "Danger Full Access" },
];

const GEMINI_APPROVAL_OPTIONS: readonly FieldOption[] = [
  { value: "yolo", label: "Auto-approve (yolo)" },
  { value: "unless-allow-listed", label: "Unless Allow-listed" },
  { value: "always", label: "Always Ask" },
];

const CITATION_PROVIDER_OPTIONS: readonly FieldOption[] = [
  { value: "openalex", label: "OpenAlex" },
];

const CITATION_FALLBACK_OPTIONS: readonly FieldOption[] = [
  { value: "", label: "None" },
  { value: "semantic_scholar", label: "Semantic Scholar" },
];

const NULLABLE_BOOL_OPTIONS: readonly FieldOption[] = [
  { value: "", label: "Server Default" },
  { value: "true", label: "Enabled" },
  { value: "false", label: "Disabled" },
];

// Ported one-to-one from plugin_dev/src/config.ts FIELD_DEFINITIONS.
// Section IDs match SECTION_LABELS from the same file.
const text = (
  section: string,
  path: string,
  label: string,
  defaultValue = "",
  description?: string,
): FieldDef => ({ path, label, section, input: "text", defaultValue, description });

const num = (
  section: string,
  path: string,
  label: string,
  defaultValue: number | null,
  opts: { nullable?: boolean; integer?: boolean; description?: string } = {},
): FieldDef => ({
  path,
  label,
  section,
  input: "number",
  defaultValue: defaultValue == null ? "" : String(defaultValue),
  integer: opts.integer ?? true,
  nullable: opts.nullable ?? false,
  description: opts.description,
});

const dropdown = (
  section: string,
  path: string,
  label: string,
  defaultValue: string,
  options: readonly FieldOption[],
  description?: string,
): FieldDef => ({ path, label, section, input: "dropdown", defaultValue, options, description });

const toggle = (
  section: string,
  path: string,
  label: string,
  defaultValue: boolean,
  description?: string,
): FieldDef => ({ path, label, section, input: "toggle", defaultValue, description });

const textarea = (section: string, path: string, label: string, description?: string): FieldDef => ({
  path,
  label,
  section,
  input: "textarea-list",
  defaultValue: "",
  description,
});

export const ZSUM_CONFIG_SCHEMA: readonly SectionDef[] = [
  {
    id: "paths",
    label: "Paths",
    fields: [
      text("paths", "bbt_json", "Better BibTeX JSON", "~/Zotero/zot.json"),
      text("paths", "zotero_sqlite", "Zotero SQLite", "~/Zotero/zotero.sqlite"),
      text("paths", "zotero_pdf_base", "PDF Base Path"),
      text("paths", "vault_output", "Vault Output", "./vault"),
      text("paths", "cache_dir", "Cache Directory", "./cache"),
    ],
  },
  {
    id: "scope",
    label: "Scope",
    fields: [textarea("scope", "collections", "Collections", "One per line.")],
  },
  {
    id: "extraction",
    label: "Extraction",
    fields: [
      num("extraction", "max_first_pages", "Max First Pages", 3),
      num("extraction", "max_last_pages", "Max Last Pages", 2),
      num("extraction", "max_text_chars", "Max Text Chars", 24000),
      num("extraction", "min_text_chars", "Min Text Chars", 500),
      num("extraction", "max_pdf_pages", "Max PDF Pages", null, { nullable: true }),
    ],
  },
  {
    id: "llm-routing",
    label: "LLM Routing",
    fields: [
      dropdown("llm-routing", "llm.routing.cards", "Cards Provider", "anthropic", LLM_PROVIDER_OPTIONS),
      dropdown("llm-routing", "llm.routing.digests", "Digests Provider", "anthropic", LLM_PROVIDER_OPTIONS),
      dropdown("llm-routing", "llm.routing.redigest", "Redigest Provider", "anthropic", LLM_PROVIDER_OPTIONS),
    ],
  },
  {
    id: "llm-anthropic",
    label: "LLM Anthropic",
    fields: [
      text("llm-anthropic", "llm.anthropic.model", "Anthropic Model"),
      text("llm-anthropic", "llm.anthropic.api_key_env", "Anthropic API Key Env", "ANTHROPIC_API_KEY"),
      num("llm-anthropic", "llm.anthropic.max_tokens_card", "Anthropic Card Tokens", 1024),
      num("llm-anthropic", "llm.anthropic.max_tokens_digest", "Anthropic Digest Tokens", 2048),
    ],
  },
  {
    id: "llm-openai-compat",
    label: "LLM OpenAI Compatible",
    fields: [
      text("llm-openai-compat", "llm.openai_compat.base_url", "OpenAI Compatible Base URL", "http://localhost:1234/v1"),
      text("llm-openai-compat", "llm.openai_compat.model", "OpenAI Compatible Model"),
      text("llm-openai-compat", "llm.openai_compat.api_key", "OpenAI Compatible API Key", "lm-studio"),
      num("llm-openai-compat", "llm.openai_compat.max_tokens_card", "OpenAI Compatible Card Tokens", 1024),
      num("llm-openai-compat", "llm.openai_compat.max_tokens_digest", "OpenAI Compatible Digest Tokens", 2048),
      dropdown(
        "llm-openai-compat",
        "llm.openai_compat.enable_thinking",
        "OpenAI Compatible Thinking",
        "",
        NULLABLE_BOOL_OPTIONS,
      ),
      toggle(
        "llm-openai-compat",
        "llm.openai_compat.structured_output",
        "Structured Output",
        false,
        "Enable JSON Schema structured output via response_format. Requires a compatible server (LM Studio, vLLM).",
      ),
    ],
  },
  {
    id: "llm-codex-exec",
    label: "LLM Codex Exec",
    fields: [
      text("llm-codex-exec", "llm.codex_exec.model", "Codex Model"),
      text("llm-codex-exec", "llm.codex_exec.binary", "Codex Binary", "codex"),
      dropdown("llm-codex-exec", "llm.codex_exec.sandbox", "Codex Sandbox", "read-only", CODEX_SANDBOX_OPTIONS),
      num("llm-codex-exec", "llm.codex_exec.max_tokens_card", "Codex Card Tokens", 1024),
      num("llm-codex-exec", "llm.codex_exec.max_tokens_digest", "Codex Digest Tokens", 2048),
    ],
  },
  {
    id: "llm-gemini-cli",
    label: "LLM Gemini CLI",
    fields: [
      text("llm-gemini-cli", "llm.gemini_cli.model", "Gemini Model", "", "Leave empty to let Gemini CLI auto-select a model."),
      text("llm-gemini-cli", "llm.gemini_cli.binary", "Gemini Binary", "gemini"),
      dropdown(
        "llm-gemini-cli",
        "llm.gemini_cli.approval_mode",
        "Gemini Approval Mode",
        "yolo",
        GEMINI_APPROVAL_OPTIONS,
        "Tool approval mode. 'yolo' auto-approves (safe since zsum uses no tools).",
      ),
      num("llm-gemini-cli", "llm.gemini_cli.max_tokens_card", "Gemini Card Tokens", 1024),
      num("llm-gemini-cli", "llm.gemini_cli.max_tokens_digest", "Gemini Digest Tokens", 2048),
    ],
  },
  {
    id: "output",
    label: "Output",
    fields: [toggle("output", "generate_paper_notes", "Generate Paper Notes", true)],
  },
  {
    id: "versioning",
    label: "Versioning",
    fields: [num("versioning", "prompt_version", "Prompt Version", 1)],
  },
  {
    id: "citations",
    label: "Citations",
    fields: [
      toggle("citations", "citations.enabled", "Enable Citations", true),
      dropdown("citations", "citations.provider", "Citations Provider", "openalex", CITATION_PROVIDER_OPTIONS),
      dropdown("citations", "citations.fallback", "Citations Fallback", "semantic_scholar", CITATION_FALLBACK_OPTIONS),
      text("citations", "citations.email", "Citations Email"),
      text("citations", "citations.api_key", "Citations API Key"),
      num("citations", "citations.rate_limit_delay", "Citations Rate Limit Delay", 0.1, { integer: false }),
      num("citations", "citations.max_age_days", "Citations Max Age Days", 30),
      num("citations", "citations.top_n", "Citations Top N", 10),
      num("citations", "citations.timeout_seconds", "Citations Timeout Seconds", 10.0, { integer: false }),
    ],
  },
  {
    id: "restructure",
    label: "Restructure",
    fields: [num("restructure", "restructure.max_digest_chars", "Restructure Max Digest Chars", 800)],
  },
];

// ── Helpers (mirror config.ts validation helpers) ──────────────────
export function getInValue(root: unknown, dottedPath: string): unknown {
  const tokens = dottedPath.split(".");
  let cur: unknown = root;
  for (const t of tokens) {
    if (cur && typeof cur === "object" && !Array.isArray(cur) && t in (cur as Record<string, unknown>)) {
      cur = (cur as Record<string, unknown>)[t];
    } else {
      return undefined;
    }
  }
  return cur;
}

export function isBlankConfigValue(raw: unknown): boolean {
  return raw == null || (typeof raw === "string" && raw.trim() === "");
}

export function applyLocalModelDefault(
  dottedPath: string,
  raw: unknown,
  defaults?: LocalModelDefaults | null,
): unknown {
  if (!defaults?.model || !isBlankConfigValue(raw)) {
    return raw;
  }

  switch (dottedPath) {
    case "llm.routing.cards":
    case "llm.routing.digests":
    case "llm.routing.redigest":
      return defaults.routing_provider ?? raw;
    case "llm.openai_compat.base_url":
      return defaults.base_url ?? raw;
    case "llm.openai_compat.model":
      return defaults.model;
    case "llm.openai_compat.api_key":
      return defaults.api_key ?? raw;
    default:
      return raw;
  }
}

/** Convert a raw YAML value into the form-input string/boolean. */
export function toInputValue(field: FieldDef, raw: unknown): string | boolean {
  if (field.input === "toggle") {
    if (typeof raw === "boolean") return raw;
    if (raw === "true") return true;
    if (raw === "false") return false;
    return raw == null ? Boolean(field.defaultValue) : Boolean(raw);
  }
  if (field.input === "textarea-list") {
    if (Array.isArray(raw)) {
      return raw
        .map((entry) => String(entry).trim())
        .filter((e) => e.length > 0)
        .join("\n");
    }
    return raw == null ? "" : String(raw);
  }
  if (raw == null) return field.defaultValue == null ? "" : String(field.defaultValue);
  return String(raw);
}

export type SerializeResult =
  | { ok: true; value: unknown }
  | { ok: false; error: string };

/** Convert a form-input string/boolean into a YAML-ready value (or an error). */
export function serializeFieldValue(field: FieldDef, input: string | boolean): SerializeResult {
  if (field.input === "toggle") {
    if (typeof input !== "boolean") return { ok: false, error: "Expected a boolean value." };
    return { ok: true, value: input };
  }
  if (field.input === "dropdown") {
    const allowed = new Set((field.options ?? []).map((o) => o.value));
    const v = String(input);
    if (!allowed.has(v)) {
      return { ok: false, error: `Expected one of: ${[...allowed].join(", ")}` };
    }
    // Special-cased nullable boolean dropdown: "" → null, "true"/"false" → bool.
    if (field.options === NULLABLE_BOOL_OPTIONS) {
      if (v === "") return { ok: true, value: null };
      if (v === "true") return { ok: true, value: true };
      if (v === "false") return { ok: true, value: false };
    }
    return { ok: true, value: v };
  }
  if (field.input === "number") {
    const trimmed = String(input).trim();
    if (trimmed === "") {
      if (field.nullable) return { ok: true, value: null };
      return { ok: false, error: "A numeric value is required." };
    }
    const n = Number(trimmed);
    if (!Number.isFinite(n)) return { ok: false, error: "Value must be a valid number." };
    if ((field.integer ?? true) && !Number.isInteger(n)) {
      return { ok: false, error: "Value must be an integer." };
    }
    return { ok: true, value: n };
  }
  if (field.input === "textarea-list") {
    return {
      ok: true,
      value: String(input)
        .split(/\r?\n/)
        .map((s) => s.trim())
        .filter((s) => s.length > 0),
    };
  }
  // text
  return { ok: true, value: String(input) };
}
