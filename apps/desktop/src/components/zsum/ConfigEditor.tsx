/**
 * Config editor — schema-driven YAML form for zsum.config.yaml.
 *
 * Mirrors the Obsidian plugin's "Config" tab: vertical section nav on the
 * left, panel of fields on the right, footer with Save / Revert /
 * Reload-from-disk and a banner area for parse + dirty state.
 *
 * The server preserves comments via ruamel — we ship the user's edits as
 * a flat list of dotted-path updates and only touched paths are rewritten.
 */

import { useQuery, useQueryClient } from "@tanstack/react-query";
import { useEffect, useMemo, useRef, useState } from "react";

import { getConfigDoc, postConfigDoc } from "../../api/zsum";
import {
  ZSUM_CONFIG_SCHEMA,
  applyLocalModelDefault,
  getInValue,
  serializeFieldValue,
  toInputValue,
  type FieldDef,
  type LocalModelDefaults,
  type SectionDef,
} from "../../zsum/config-schema";
import { cn } from "../../lib/utils";

type FormValues = Record<string, string | boolean>;
type Validation = Record<string, string>;

export interface ConfigEditorProps {
  title?: string;
  navigationMode?: "internal" | "none";
  visibleSectionIds?: readonly string[];
}

function buildFormFromParsed(
  parsed: Record<string, unknown> | null,
  localModelDefaults: LocalModelDefaults | null,
): FormValues {
  const out: FormValues = {};
  for (const section of ZSUM_CONFIG_SCHEMA) {
    for (const f of section.fields) {
      const raw = parsed ? getInValue(parsed, f.path) : undefined;
      out[f.path] = toInputValue(f, applyLocalModelDefault(f.path, raw, localModelDefaults));
    }
  }
  return out;
}

function validateAll(form: FormValues): Validation {
  const errs: Validation = {};
  for (const section of ZSUM_CONFIG_SCHEMA) {
    for (const f of section.fields) {
      const r = serializeFieldValue(f, form[f.path]);
      if (!r.ok) errs[f.path] = r.error;
    }
  }
  return errs;
}

export function ConfigEditor({
  title = "Config",
  navigationMode = "internal",
  visibleSectionIds,
}: ConfigEditorProps = {}) {
  const qc = useQueryClient();
  const { data, isLoading, refetch } = useQuery({
    queryKey: ["zsum", "config-doc"],
    queryFn: getConfigDoc,
  });

  const [activeSection, setActiveSection] = useState<string>("paths");
  const [draft, setDraft] = useState<FormValues | null>(null);
  const [loaded, setLoaded] = useState<FormValues | null>(null);
  const [saving, setSaving] = useState(false);
  const [feedback, setFeedback] = useState<{ kind: "ok" | "err"; text: string } | null>(null);

  // Refs mirror state so the hydration effect can read the latest values
  // without closing over them — otherwise stale draft/loaded from a previous
  // render would clobber in-progress edits when fresh data arrives.
  const draftRef = useRef<FormValues | null>(null);
  const loadedRef = useRef<FormValues | null>(null);
  useEffect(() => {
    draftRef.current = draft;
  }, [draft]);
  useEffect(() => {
    loadedRef.current = loaded;
  }, [loaded]);

  const visibleSections = useMemo<readonly SectionDef[]>(() => {
    if (!visibleSectionIds?.length) return ZSUM_CONFIG_SCHEMA;
    const allowedIds = new Set(visibleSectionIds);
    return ZSUM_CONFIG_SCHEMA.filter((section) => allowedIds.has(section.id));
  }, [visibleSectionIds]);

  // Hydrate the form when the doc arrives. Re-hydrate on full reload too.
  useEffect(() => {
    if (!data) return;
    const form = buildFormFromParsed(data.parsed, data.local_model_defaults);
    const currentDraft = draftRef.current;
    const currentLoaded = loadedRef.current;
    const hasUnsavedChanges =
      currentDraft && currentLoaded
        ? Object.keys(currentDraft).some((key) => currentDraft[key] !== currentLoaded[key])
        : false;
    setLoaded(form);
    setDraft(hasUnsavedChanges ? currentDraft : form);
  }, [data]);

  useEffect(() => {
    if (!visibleSections.length) return;
    if (!visibleSections.some((section) => section.id === activeSection)) {
      setActiveSection(visibleSections[0].id);
    }
  }, [activeSection, visibleSections]);

  const validation = useMemo(() => (draft ? validateAll(draft) : {}), [draft]);
  const dirty = useMemo(() => {
    if (!draft || !loaded) return false;
    return Object.keys(draft).some((k) => draft[k] !== loaded[k]);
  }, [draft, loaded]);
  const canSave = dirty && Object.keys(validation).length === 0 && !data?.parse_error;

  if (isLoading || !data || !draft) {
    return <div className="p-4 text-sm text-muted-foreground">Loading config…</div>;
  }

  const onChange = (path: string, value: string | boolean) => {
    setDraft({ ...draft, [path]: value });
    setFeedback(null);
  };

  const onSave = async () => {
    if (!draft || !loaded) return;
    setSaving(true);
    setFeedback(null);
    // Build a flat list of updates for changed paths only, serialized to
    // YAML-ready values.
    const updates: { path: string; value: unknown }[] = [];
    for (const section of ZSUM_CONFIG_SCHEMA) {
      for (const f of section.fields) {
        if (draft[f.path] === loaded[f.path]) continue;
        const r = serializeFieldValue(f, draft[f.path]);
        if (r.ok) updates.push({ path: f.path, value: r.value });
      }
    }
    try {
      await postConfigDoc(updates);
      setFeedback({ kind: "ok", text: "Config saved." });
      const fresh = await refetch();
      qc.invalidateQueries({ queryKey: ["zsum", "index"] });
      qc.invalidateQueries({ queryKey: ["zsum", "bbt-status"] });
      if (fresh.data) {
        const form = buildFormFromParsed(fresh.data.parsed, fresh.data.local_model_defaults);
        setLoaded(form);
        setDraft(form);
      }
    } catch (e) {
      setFeedback({ kind: "err", text: (e as Error).message });
    } finally {
      setSaving(false);
    }
  };

  const onRevert = () => {
    if (loaded) setDraft({ ...loaded });
    setFeedback(null);
  };

  const onReload = async () => {
    setFeedback(null);
    const fresh = await refetch();
    if (fresh.data) {
      const form = buildFormFromParsed(fresh.data.parsed, fresh.data.local_model_defaults);
      setLoaded(form);
      setDraft(form);
    }
  };

  const activeSections =
    navigationMode === "internal"
      ? [visibleSections.find((section) => section.id === activeSection) ?? visibleSections[0]].filter(
          Boolean,
        )
      : visibleSections;

  return (
    <div className="flex h-full flex-col overflow-hidden">
      {/* Header */}
      <div className="shrink-0 border-b border-border px-3 py-2">
        <div className="flex items-center gap-3">
          <div className="flex-1">
            <div className="text-sm font-semibold">{title}</div>
            <div className="font-mono text-[11px] text-muted-foreground">{data.path}</div>
          </div>
          <div className="flex items-center gap-1.5">
            {dirty && (
              <span className="rounded bg-amber-500/15 px-1.5 py-0.5 text-[10px] font-semibold text-amber-700 dark:text-amber-300">
                Unsaved
              </span>
            )}
            {!data.parse_error && !dirty && (
              <span className="rounded bg-emerald-500/15 px-1.5 py-0.5 text-[10px] font-semibold text-emerald-700 dark:text-emerald-300">
                Comments preserved
              </span>
            )}
          </div>
        </div>
        {data.parse_error && (
          <div className="mt-1 rounded border border-red-500/30 bg-red-500/10 px-2 py-1 font-mono text-[11px] text-red-700 dark:text-red-300">
            {data.parse_error}
          </div>
        )}
      </div>

      {/* Body: nav + panel */}
      <div className="flex flex-1 overflow-hidden">
        {navigationMode === "internal" && (
          <nav className="w-44 shrink-0 overflow-y-auto border-r border-border py-2">
            {visibleSections.map((s) => (
              <button
                key={s.id}
                onClick={() => setActiveSection(s.id)}
                className={cn(
                  "block w-full px-3 py-1 text-left text-xs hover:bg-muted",
                  activeSection === s.id && "bg-muted font-semibold",
                )}
              >
                {s.label}
              </button>
            ))}
          </nav>
        )}
        <div className="flex-1 overflow-y-auto p-4">
          <div className="flex flex-col gap-6">
            {activeSections.map((section) => (
              <section key={section.id} className="flex flex-col gap-3">
                {(navigationMode === "none" || activeSections.length > 1) && (
                  <div className="text-[11px] font-semibold uppercase tracking-wide text-muted-foreground">
                    {section.label}
                  </div>
                )}
                {section.fields.map((f) => (
                  <FieldRow
                    key={f.path}
                    field={f}
                    value={draft[f.path]}
                    error={validation[f.path]}
                    onChange={(v) => onChange(f.path, v)}
                  />
                ))}
              </section>
            ))}
          </div>
        </div>
      </div>

      {/* Footer */}
      <div className="flex shrink-0 items-center gap-2 border-t border-border px-3 py-2 text-xs">
        <span className="flex-1 text-muted-foreground">
          {dirty ? "Unsaved changes" : "Up to date"}
          {feedback && (
            <span
              className={cn(
                "ml-2",
                feedback.kind === "ok" ? "text-emerald-700" : "text-red-600",
              )}
            >
              · {feedback.text}
            </span>
          )}
        </span>
        <button
          onClick={onReload}
          disabled={saving}
          className="rounded border border-border px-3 py-1 hover:bg-muted disabled:cursor-not-allowed disabled:opacity-50"
        >
          Reload from disk
        </button>
        <button
          onClick={onRevert}
          disabled={!dirty || saving}
          className="rounded border border-border px-3 py-1 hover:bg-muted disabled:cursor-not-allowed disabled:opacity-50"
        >
          Revert
        </button>
        <button
          onClick={onSave}
          disabled={!canSave || saving}
          className="rounded bg-foreground px-3 py-1 text-background hover:opacity-90 disabled:cursor-not-allowed disabled:opacity-50"
        >
          {saving ? "Saving…" : "Save"}
        </button>
      </div>
    </div>
  );
}

// ── Field row ────────────────────────────────────────────────────
function FieldRow({
  field,
  value,
  error,
  onChange,
}: {
  field: FieldDef;
  value: string | boolean;
  error?: string;
  onChange: (v: string | boolean) => void;
}) {
  return (
    <div className="grid grid-cols-[180px_1fr] items-start gap-3 text-xs">
      <div className="pt-1.5">
        <div className="font-medium">{field.label}</div>
        {field.description && (
          <div className="text-[11px] text-muted-foreground">{field.description}</div>
        )}
        <div className="font-mono text-[10px] text-muted-foreground">{field.path}</div>
      </div>
      <div className="flex flex-col gap-1">
        <FieldInput field={field} value={value} onChange={onChange} />
        {error && <div className="text-[11px] text-red-600">{error}</div>}
      </div>
    </div>
  );
}

function FieldInput({
  field,
  value,
  onChange,
}: {
  field: FieldDef;
  value: string | boolean;
  onChange: (v: string | boolean) => void;
}) {
  if (field.input === "toggle") {
    return (
      <label className="flex items-center gap-2 text-xs">
        <input
          type="checkbox"
          checked={Boolean(value)}
          onChange={(e) => onChange(e.target.checked)}
        />
        <span className="text-muted-foreground">{value ? "Enabled" : "Disabled"}</span>
      </label>
    );
  }
  if (field.input === "dropdown") {
    return (
      <select
        value={String(value)}
        onChange={(e) => onChange(e.target.value)}
        className="rounded border border-border bg-background px-2 py-1 text-xs"
      >
        {(field.options ?? []).map((o) => (
          <option key={o.value} value={o.value}>
            {o.label}
          </option>
        ))}
      </select>
    );
  }
  if (field.input === "textarea-list") {
    return (
      <textarea
        value={String(value)}
        rows={4}
        onChange={(e) => onChange(e.target.value)}
        placeholder="One per line"
        className="rounded border border-border bg-background px-2 py-1 font-mono text-[11px]"
      />
    );
  }
  // text or number
  return (
    <input
      type={field.input === "number" ? "number" : "text"}
      value={String(value)}
      onChange={(e) => onChange(e.target.value)}
      className="rounded border border-border bg-background px-2 py-1 font-mono text-[11px]"
    />
  );
}
