/**
 * Prompt Library page.
 *
 * A single shared component rendered by three mode-specific routes
 * (`/akms/prompts`, `/zsum/prompts`, `/assistant/prompts`). The
 * `family` prop filters which slots are visible:
 *
 *   - `"akms"` → only `akms.*` slots
 *   - `"zsum"` → only `zsum.*` slots
 *   - `null`   → all eight canonical slots (assistant mode)
 *
 * Layout:
 *   Left   — families/slots list (filtered by family).
 *   Middle — variants for the selected slot (user + bundled).
 *   Right  — variant editor (id, name, family, slot, content textarea)
 *            + validation banner + "where used" profile list + preview.
 *
 * Profile manager appears below the editor: list profiles + their
 * active slot→variant_ref map; create / save profile.
 *
 * All three mode-specific routes share this exact component, only
 *      differing in the `family` prop.
 *
 * Scope note: this page edits **library** entities (variants + profiles
 * persisted under `<workspace>/prompt-library/`). It does NOT mutate
 * request-level overrides — those belong to the Run dialog.
 */
import { useEffect, useMemo, useState } from "react";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";

import {
  createProfile,
  createSchema,
  createVariant,
  deleteSchema,
  deleteVariant,
  duplicateVariant,
  filterSlotsByFamily,
  listProfiles,
  listSchemas,
  listSlots,
  listVariants,
  resolveEffectivePrompts,
  updateProfile,
  updateSchema,
  updateVariant,
  validateVariant,
  type EffectivePromptSet,
  type PromptProfile,
  type PromptSchema,
  type PromptVariant,
  type SlotFamily,
} from "../../api/promptLibrary";

interface PromptLibraryPageProps {
  /**
   * Family filter. `null` shows all families (assistant mode); the
   * akms-mode and zsum-mode routes pass their family explicitly.
   */
  family: SlotFamily | null;
}

export function PromptLibraryPage({ family }: PromptLibraryPageProps) {
  const qc = useQueryClient();

  const slotsQ = useQuery({
    queryKey: ["prompts", "slots"],
    queryFn: listSlots,
    staleTime: 60_000,
  });

  const schemasQ = useQuery({
    queryKey: ["prompts", "schemas", family],
    queryFn: () => listSchemas(family),
    staleTime: 30_000,
  });

  const allSchemas = useMemo<PromptSchema[]>(() => {
    const s = schemasQ.data;
    if (!s) return [];
    return [...s.schemas, ...s.bundled];
  }, [schemasQ.data]);

  const invalidateSchemas = () =>
    // queryKey includes ``family`` so the page-scoped query refetches.
    // Passing the prefix without ``family`` matches every family-keyed
    // entry too — covers the schema-edit-then-switch-family case.
    qc.invalidateQueries({ queryKey: ["prompts", "schemas"] });

  const visibleSlots = useMemo(
    () => filterSlotsByFamily(slotsQ.data?.slots ?? [], family),
    [slotsQ.data, family],
  );

  const [selectedSlot, setSelectedSlot] = useState<string | null>(null);
  const effectiveSlot = selectedSlot ?? visibleSlots[0] ?? null;

  const variantsQ = useQuery({
    queryKey: ["prompts", "variants", effectiveSlot],
    queryFn: () => listVariants(effectiveSlot ?? undefined),
    enabled: !!effectiveSlot,
  });

  const profilesQ = useQuery({
    queryKey: ["prompts", "profiles"],
    queryFn: listProfiles,
    staleTime: 30_000,
  });

  const allVariants = useMemo<PromptVariant[]>(() => {
    const v = variantsQ.data;
    if (!v) return [];
    return [...v.variants, ...v.bundled];
  }, [variantsQ.data]);

  const [selectedVariantId, setSelectedVariantId] = useState<string | null>(null);
  const selectedVariant = useMemo<PromptVariant | null>(() => {
    if (!allVariants.length) return null;
    if (selectedVariantId) {
      const m = allVariants.find((x) => x.id === selectedVariantId);
      if (m) return m;
    }
    return allVariants[0] ?? null;
  }, [allVariants, selectedVariantId]);

  // Reset variant selection when slot changes.
  useEffect(() => {
    setSelectedVariantId(null);
  }, [effectiveSlot]);

  const invalidateVariants = () =>
    qc.invalidateQueries({ queryKey: ["prompts", "variants", effectiveSlot] });
  const invalidateProfiles = () =>
    qc.invalidateQueries({ queryKey: ["prompts", "profiles"] });

  return (
    <main
      className="flex h-full"
      aria-label="Prompt Library"
      data-testid="prompt-library-page"
    >
      {/* ── Left: slots ────────────────────────────────────────────── */}
      <aside
        className="flex w-56 flex-col border-r"
        aria-label="Slots"
        data-testid="prompts-slots-list"
      >
        <header className="border-b px-4 py-3">
          <h1 className="text-sm font-semibold">Slots</h1>
          <p className="text-xs text-muted-foreground">
            {family ? `Family: ${family}` : "All families"} · {visibleSlots.length} slots
          </p>
        </header>
        <ul className="flex-1 overflow-auto" role="listbox" aria-label="Prompt slots">
          {visibleSlots.map((slot) => (
            <li key={slot}>
              <button
                type="button"
                role="option"
                aria-selected={effectiveSlot === slot}
                onClick={() => setSelectedSlot(slot)}
                data-testid={`slot-row-${slot}`}
                className={
                  "w-full border-b px-3 py-2 text-left text-xs font-mono hover:bg-accent " +
                  (effectiveSlot === slot ? "bg-accent" : "")
                }
              >
                {slot}
              </button>
            </li>
          ))}
          {!slotsQ.isLoading && visibleSlots.length === 0 && (
            <li className="px-4 py-3 text-xs text-muted-foreground">
              No slots match this family.
            </li>
          )}
        </ul>
      </aside>

      {/* ── Middle: variants list ──────────────────────────────────── */}
      <section
        className="flex w-72 flex-col border-r"
        aria-label="Variants for selected slot"
        data-testid="prompts-variants-list"
      >
        <header className="flex items-center justify-between border-b px-4 py-3">
          <div>
            <h2 className="text-sm font-semibold">Variants</h2>
            <p className="text-xs text-muted-foreground">
              {effectiveSlot ?? "—"} · {allVariants.length} total
            </p>
          </div>
          {effectiveSlot && (
            <NewVariantButton
              slot={effectiveSlot}
              onCreated={(v) => {
                setSelectedVariantId(v.id);
                invalidateVariants();
              }}
            />
          )}
        </header>
        <ul className="flex-1 overflow-auto" role="listbox" aria-label="Variants">
          {allVariants.map((v) => (
            <li key={`${v.slot}::${v.id}::${v.source}`}>
              <button
                type="button"
                role="option"
                aria-selected={selectedVariant?.id === v.id && selectedVariant?.source === v.source}
                onClick={() => setSelectedVariantId(v.id)}
                data-testid={`variant-row-${v.id}`}
                className={
                  "w-full border-b px-3 py-2 text-left text-xs hover:bg-accent " +
                  (selectedVariant?.id === v.id && selectedVariant?.source === v.source
                    ? "bg-accent"
                    : "")
                }
              >
                <div className="truncate font-medium">{v.name || v.id}</div>
                <div className="truncate text-[10px] text-muted-foreground">
                  {v.id} · {v.source}
                </div>
              </button>
            </li>
          ))}
          {!variantsQ.isLoading && allVariants.length === 0 && (
            <li className="px-4 py-3 text-xs text-muted-foreground">
              No variants for this slot.
            </li>
          )}
        </ul>
      </section>

      {/* ── Right: editor + profiles ──────────────────────────────── */}
      <section className="flex flex-1 flex-col overflow-hidden">
        {selectedVariant && effectiveSlot ? (
          <VariantEditor
            variant={selectedVariant}
            allProfiles={profilesQ.data?.profiles ?? []}
            allSchemas={allSchemas}
            onSaved={() => invalidateVariants()}
            onDuplicated={(v) => {
              setSelectedVariantId(v.id);
              invalidateVariants();
            }}
            onDeleted={() => {
              setSelectedVariantId(null);
              invalidateVariants();
            }}
          />
        ) : (
          <div className="flex flex-1 items-center justify-center text-sm text-muted-foreground">
            Select a slot and variant to edit.
          </div>
        )}

        <ProfileManager
          profiles={profilesQ.data?.profiles ?? []}
          onChanged={invalidateProfiles}
        />

        <SchemaManager
          schemas={allSchemas}
          family={family}
          onChanged={invalidateSchemas}
        />
      </section>
    </main>
  );
}

// ── Variant editor ─────────────────────────────────────────────────────────

interface VariantEditorProps {
  variant: PromptVariant;
  allProfiles: PromptProfile[];
  allSchemas: PromptSchema[];
  onSaved: () => void;
  onDuplicated: (v: PromptVariant) => void;
  onDeleted: () => void;
}

function VariantEditor({
  variant,
  allProfiles,
  allSchemas,
  onSaved,
  onDuplicated,
  onDeleted,
}: VariantEditorProps) {
  const [name, setName] = useState(variant.name);
  const [description, setDescription] = useState(variant.description);
  const [content, setContent] = useState(variant.content);
  const [outputSchemaId, setOutputSchemaId] = useState<string | null>(
    variant.output_schema_id,
  );
  const [validationMsg, setValidationMsg] = useState<string | null>(null);
  const [preview, setPreview] = useState<EffectivePromptSet | null>(null);

  // Re-sync local state when the selected variant changes.
  useEffect(() => {
    setName(variant.name);
    setDescription(variant.description);
    setContent(variant.content);
    setOutputSchemaId(variant.output_schema_id);
    setValidationMsg(null);
    setPreview(null);
  }, [variant.slot, variant.id, variant.source, variant.content_hash]);

  const isReadOnly = variant.source === "bundled";

  const saveMut = useMutation({
    mutationFn: () =>
      updateVariant(variant.id, {
        id: variant.id,
        family: variant.family,
        slot: variant.slot,
        name,
        description,
        based_on: variant.based_on,
        filename: variant.filename,
        content_type: variant.content_type,
        content,
        output_schema_id: outputSchemaId,
      }),
    onSuccess: () => {
      setValidationMsg(null);
      onSaved();
    },
    onError: (e: unknown) => setValidationMsg(errMsg(e)),
  });

  const validateMut = useMutation({
    mutationFn: () =>
      validateVariant({
        filename: variant.filename,
        content_type: variant.content_type,
        content,
      }),
    onSuccess: (res) =>
      setValidationMsg(
        res.warnings.length === 0
          ? "Validation passed."
          : `Validation passed with warnings: ${res.warnings.join(" · ")}`,
      ),
    onError: (e: unknown) => setValidationMsg(errMsg(e)),
  });

  const dupMut = useMutation({
    mutationFn: () => duplicateVariant(variant.id, variant.slot),
    onSuccess: (v) => onDuplicated(v),
  });

  const deleteMut = useMutation({
    mutationFn: () => deleteVariant(variant.id, variant.slot),
    onSuccess: () => onDeleted(),
    onError: (e: unknown) => setValidationMsg(errMsg(e)),
  });

  const handleDelete = () => {
    if (
      window.confirm(
        `Delete variant "${variant.name || variant.id}" for slot ${variant.slot}?\nThis cannot be undone.`,
      )
    ) {
      deleteMut.mutate();
    }
  };

  // The resolver's variant reference: `<source>:<family>.<stem>.<id>`.
  const variantRef = `${variant.source}:${variant.slot}.${variant.id}`;

  // Preview = resolve the active profile with this variant pinned to its
  // slot. Overrides are variant *references*; an earlier revision sent the
  // editor text here, which the resolver could not parse, so the preview
  // came back empty. Unsaved edits are shown from the editor below.
  const previewMut = useMutation({
    mutationFn: () =>
      resolveEffectivePrompts({
        request_overrides: { [variant.slot]: variantRef },
      }),
    onSuccess: (res) => {
      setValidationMsg(null);
      setPreview(res);
    },
    onError: (e: unknown) => {
      setPreview(null);
      setValidationMsg(`Preview failed: ${errMsg(e)}`);
    },
  });

  // "Where used": every profile whose `active[slot]` resolves to this variant.
  const whereUsed = useMemo(() => {
    const legacyRef = `${variant.source}:${variant.slot}:${variant.id}`;
    const refShort = `${variant.slot}:${variant.id}`;
    return allProfiles.filter((p) => {
      const v = p.active[variant.slot];
      return v === variantRef || v === legacyRef || v === refShort || v === variant.id;
    });
  }, [allProfiles, variant, variantRef]);

  const dirty =
    name !== variant.name ||
    description !== variant.description ||
    content !== variant.content ||
    outputSchemaId !== variant.output_schema_id;

  return (
    <div className="flex flex-1 flex-col overflow-auto p-5" data-testid="variant-editor">
      <header className="mb-3 flex items-start justify-between gap-3">
        <div>
          <h2 className="text-base font-semibold">{variant.name || variant.id}</h2>
          <p className="text-xs text-muted-foreground">
            <code className="font-mono">{variant.id}</code> · family:{" "}
            <code className="font-mono">{variant.family}</code> · slot:{" "}
            <code className="font-mono">{variant.slot}</code> · source:{" "}
            <code className="font-mono">{variant.source}</code>
          </p>
        </div>
        <div className="flex shrink-0 gap-2">
          <button
            type="button"
            data-testid="variant-duplicate-button"
            onClick={() => dupMut.mutate()}
            disabled={dupMut.isPending}
            className="rounded border px-2 py-1 text-xs hover:bg-accent disabled:opacity-40"
          >
            {dupMut.isPending ? "Duplicating…" : "Duplicate"}
          </button>
          {!isReadOnly && (
            <button
              type="button"
              data-testid="variant-delete-button"
              onClick={handleDelete}
              disabled={deleteMut.isPending}
              className="rounded border border-red-300 px-2 py-1 text-xs text-red-700 hover:bg-red-50 disabled:opacity-40"
              title="Delete this user variant"
            >
              {deleteMut.isPending ? "Deleting…" : "Delete"}
            </button>
          )}
        </div>
      </header>

      <div className="grid gap-3 text-sm">
        <label className="flex flex-col gap-1">
          <span className="text-xs font-medium uppercase text-muted-foreground">Name</span>
          <input
            data-testid="variant-name-input"
            className="rounded border px-2 py-1 text-sm"
            value={name}
            disabled={isReadOnly}
            onChange={(e) => setName(e.target.value)}
          />
        </label>
        <label className="flex flex-col gap-1">
          <span className="text-xs font-medium uppercase text-muted-foreground">Description</span>
          <input
            data-testid="variant-description-input"
            className="rounded border px-2 py-1 text-sm"
            value={description}
            disabled={isReadOnly}
            onChange={(e) => setDescription(e.target.value)}
          />
        </label>
        <label className="flex flex-col gap-1">
          <span className="text-xs font-medium uppercase text-muted-foreground">
            Linked JSON schema (optional)
          </span>
          <select
            data-testid="variant-schema-select"
            value={outputSchemaId ?? ""}
            disabled={isReadOnly}
            onChange={(e) => setOutputSchemaId(e.target.value || null)}
            className="rounded border px-2 py-1 text-sm"
          >
            <option value="">— none —</option>
            {allSchemas.map((s) => (
              <option key={`${s.source}:${s.id}`} value={s.id}>
                {s.name} ({s.id} · {s.source})
              </option>
            ))}
          </select>
          <span className="text-[10px] text-muted-foreground">
            Pair this prompt with a JSON output schema. Edit schemas separately
            in the Schemas section below so prompts and schemas mix-and-match.
          </span>
        </label>
        <label className="flex flex-col gap-1">
          <span className="text-xs font-medium uppercase text-muted-foreground">
            Content ({variant.content_type})
          </span>
          <textarea
            data-testid="variant-content-input"
            rows={10}
            className="rounded border px-2 py-1 font-mono text-xs"
            value={content}
            disabled={isReadOnly}
            onChange={(e) => setContent(e.target.value)}
          />
        </label>
      </div>

      <div className="mt-3 flex flex-wrap items-center gap-2">
        <button
          type="button"
          data-testid="variant-save-button"
          disabled={isReadOnly || !dirty || saveMut.isPending}
          onClick={() => saveMut.mutate()}
          className="rounded bg-foreground px-3 py-1 text-sm text-background disabled:opacity-40"
        >
          {saveMut.isPending ? "Saving…" : "Save"}
        </button>
        <button
          type="button"
          data-testid="variant-validate-button"
          disabled={validateMut.isPending}
          onClick={() => validateMut.mutate()}
          className="rounded border px-3 py-1 text-sm"
        >
          {validateMut.isPending ? "Validating…" : "Validate"}
        </button>
        <button
          type="button"
          data-testid="variant-preview-button"
          disabled={previewMut.isPending}
          onClick={() => previewMut.mutate()}
          className="rounded border px-3 py-1 text-sm"
        >
          {previewMut.isPending ? "Resolving…" : "Preview"}
        </button>
        {isReadOnly && (
          <span className="text-xs text-muted-foreground">
            Bundled variants are read-only. Duplicate to edit.
          </span>
        )}
      </div>

      {validationMsg && (
        <p
          data-testid="variant-validation-banner"
          className={
            "mt-3 rounded border px-3 py-2 text-xs " +
            (validationMsg.startsWith("Validation passed")
              ? "border-green-500 text-green-700"
              : "border-red-500 text-red-700")
          }
        >
          {validationMsg}
        </p>
      )}

      {preview && (
        <section
          className="mt-3 rounded border bg-muted/20 p-3"
          aria-label="Effective prompt preview"
          data-testid="variant-preview-results"
        >
          <h3 className="mb-2 text-xs font-semibold uppercase text-muted-foreground">
            Resolved effective prompts
          </h3>
          {Object.keys(preview.slot_to_variant).length === 0 && (
            <p className="text-xs text-muted-foreground" data-testid="variant-preview-empty">
              Nothing resolved: the active profile selects no prompts.
            </p>
          )}
          <ul className="space-y-2">
            {Object.entries(preview.slot_to_variant).map(([slot, ref]) => (
              <li
                key={slot}
                data-testid={`preview-slot-${slot}`}
                className="rounded border bg-background p-2"
              >
                <div className="flex items-center justify-between text-xs">
                  <span className="font-mono">{slot}</span>
                  <span className="text-muted-foreground">
                    variant: <code className="font-mono">{ref}</code>
                  </span>
                </div>
                {slot === variant.slot && dirty ? (
                  <>
                    <p className="mt-1 text-[10px] text-muted-foreground">
                      Showing your unsaved edits.
                    </p>
                    <pre className="mt-1 max-h-24 overflow-auto whitespace-pre-wrap text-[11px] text-muted-foreground">
                      {content}
                    </pre>
                  </>
                ) : preview.contents[slot] ? (
                  <pre className="mt-1 max-h-24 overflow-auto whitespace-pre-wrap text-[11px] text-muted-foreground">
                    {preview.contents[slot]}
                  </pre>
                ) : (
                  <p className="mt-1 text-[11px] text-amber-700 dark:text-amber-400">
                    {ref ? "This variant could not be found." : "No variant selected for this slot."}
                  </p>
                )}
              </li>
            ))}
          </ul>
        </section>
      )}

      <section
        className="mt-3 rounded border p-3"
        aria-label="Where used"
        data-testid="variant-where-used"
      >
        <h3 className="mb-2 text-xs font-semibold uppercase text-muted-foreground">
          Where used
        </h3>
        {whereUsed.length === 0 ? (
          <p className="text-xs text-muted-foreground">
            Not referenced by any profile.
          </p>
        ) : (
          <ul className="space-y-1 text-xs">
            {whereUsed.map((p) => (
              <li key={p.id} data-testid={`where-used-${p.id}`}>
                <code className="font-mono">{p.id}</code> — {p.name}
              </li>
            ))}
          </ul>
        )}
      </section>
    </div>
  );
}

// ── New variant button ────────────────────────────────────────────────────

function NewVariantButton({
  slot,
  onCreated,
}: {
  slot: string;
  onCreated: (v: PromptVariant) => void;
}) {
  const family = slot.startsWith("zsum.")
    ? "zsum"
    : slot.startsWith("assistant.")
      ? "assistant"
      : "akms";
  const family_pref = family === "akms" ? ".claude/" : "prompts/";
  const mut = useMutation({
    mutationFn: () =>
      createVariant({
        slot,
        family,
        name: `New ${slot} variant`,
        filename: `${family_pref}${slot.replace(".", "_")}_new.md`,
        content_type: "markdown",
        content: "",
      }),
    onSuccess: (v) => onCreated(v),
  });
  return (
    <button
      type="button"
      data-testid="variant-new-button"
      onClick={() => mut.mutate()}
      disabled={mut.isPending}
      className="rounded border px-2 py-0.5 text-xs hover:bg-accent disabled:opacity-40"
      title="Create new variant"
    >
      {mut.isPending ? "…" : "+ New"}
    </button>
  );
}

// ── Profile manager ──────────────────────────────────────────────────────

function ProfileManager({
  profiles,
  onChanged,
}: {
  profiles: PromptProfile[];
  onChanged: () => void;
}) {
  const [selectedId, setSelectedId] = useState<string | null>(null);
  const selected = profiles.find((p) => p.id === selectedId) ?? profiles[0] ?? null;

  const [newName, setNewName] = useState("");

  const createMut = useMutation({
    mutationFn: () => createProfile({ name: newName }),
    onSuccess: (p) => {
      setNewName("");
      setSelectedId(p.id);
      onChanged();
    },
  });

  const updateActiveMut = useMutation({
    mutationFn: ({ profile, slot, ref }: { profile: PromptProfile; slot: string; ref: string }) =>
      updateProfile(profile.id, {
        id: profile.id,
        name: profile.name,
        description: profile.description,
        active: { ...profile.active, [slot]: ref },
      }),
    onSuccess: () => onChanged(),
  });

  return (
    <section
      className="border-t bg-muted/10 p-4"
      aria-label="Profile manager"
      data-testid="profile-manager"
    >
      <h3 className="mb-2 text-xs font-semibold uppercase text-muted-foreground">
        Profiles
      </h3>
      <div className="flex gap-3 text-xs">
        <ul
          className="w-48 shrink-0 space-y-1 overflow-auto"
          role="listbox"
          aria-label="Profiles"
        >
          {profiles.map((p) => (
            <li key={p.id}>
              <button
                type="button"
                role="option"
                aria-selected={selected?.id === p.id}
                onClick={() => setSelectedId(p.id)}
                data-testid={`profile-row-${p.id}`}
                className={
                  "w-full rounded border px-2 py-1 text-left hover:bg-accent " +
                  (selected?.id === p.id ? "bg-accent" : "")
                }
              >
                <div className="truncate font-medium">{p.name}</div>
                <div className="truncate text-[10px] text-muted-foreground">{p.id}</div>
              </button>
            </li>
          ))}
          {profiles.length === 0 && (
            <li className="text-muted-foreground">No profiles yet.</li>
          )}
        </ul>
        <div className="flex-1">
          {selected ? (
            <div data-testid="profile-detail">
              <p className="mb-2">
                <strong>{selected.name}</strong> —{" "}
                <code className="font-mono">{selected.id}</code>
              </p>
              {selected.description && (
                <p className="mb-2 text-muted-foreground">{selected.description}</p>
              )}
              <h4 className="mb-1 text-[11px] font-semibold uppercase text-muted-foreground">
                Slot → variant_ref
              </h4>
              <ul className="space-y-1">
                {Object.entries(selected.active).map(([slot, ref]) => (
                  <li
                    key={slot}
                    data-testid={`profile-active-${slot}`}
                    className="flex justify-between gap-2 rounded border bg-background px-2 py-1"
                  >
                    <code className="font-mono">{slot}</code>
                    <code className="font-mono text-muted-foreground">{ref}</code>
                  </li>
                ))}
                {Object.keys(selected.active).length === 0 && (
                  <li className="text-muted-foreground">No active assignments.</li>
                )}
              </ul>
              {updateActiveMut.isPending && (
                <p className="mt-1 text-muted-foreground">Saving…</p>
              )}
              {/* Hidden helper: tests can re-use updateActiveMut by wiring */}
              <button
                type="button"
                hidden
                data-testid="profile-update-active"
                onClick={() =>
                  selected &&
                  updateActiveMut.mutate({
                    profile: selected,
                    slot: Object.keys(selected.active)[0] ?? "",
                    ref: "",
                  })
                }
              />
            </div>
          ) : (
            <p className="text-muted-foreground">Select a profile to inspect.</p>
          )}

          <form
            className="mt-3 flex items-center gap-2"
            onSubmit={(e) => {
              e.preventDefault();
              if (newName.trim()) createMut.mutate();
            }}
          >
            <input
              data-testid="profile-new-name"
              className="rounded border px-2 py-1 text-xs"
              placeholder="New profile name"
              value={newName}
              onChange={(e) => setNewName(e.target.value)}
            />
            <button
              type="submit"
              data-testid="profile-create-button"
              disabled={!newName.trim() || createMut.isPending}
              className="rounded border px-2 py-1 text-xs hover:bg-accent disabled:opacity-40"
            >
              {createMut.isPending ? "Creating…" : "Create"}
            </button>
          </form>
        </div>
      </div>
    </section>
  );
}

function errMsg(e: unknown): string {
  return e instanceof Error ? e.message : String(e);
}

// ── Schema manager (decoupled JSON output schemas) ─────────────────────────

function SchemaManager({
  schemas,
  family,
  onChanged,
}: {
  schemas: PromptSchema[];
  family: SlotFamily | null;
  onChanged: () => void;
}) {
  const [selectedId, setSelectedId] = useState<string | null>(null);
  const selected = schemas.find((s) => s.id === selectedId) ?? schemas[0] ?? null;
  const isBundled = selected?.source === "bundled";

  const [draftText, setDraftText] = useState<string>("");
  const [draftName, setDraftName] = useState<string>("");
  const [draftDesc, setDraftDesc] = useState<string>("");
  const [error, setError] = useState<string | null>(null);
  const [newName, setNewName] = useState("");

  useEffect(() => {
    if (selected) {
      setDraftText(JSON.stringify(selected.content, null, 2));
      setDraftName(selected.name);
      setDraftDesc(selected.description);
      setError(null);
    }
  }, [selected?.id, selected?.source]);

  const saveMut = useMutation({
    mutationFn: async () => {
      if (!selected) return;
      const parsed = JSON.parse(draftText);
      return updateSchema(selected.id, {
        id: selected.id,
        name: draftName,
        description: draftDesc,
        // Preserve the schema's existing family tag on update; the
        // family-aware listing endpoint will keep filtering correctly.
        family: selected.family ?? null,
        content: parsed,
      });
    },
    onSuccess: () => {
      setError(null);
      onChanged();
    },
    onError: (e: unknown) => setError(errMsg(e)),
  });

  const createMut = useMutation({
    mutationFn: () =>
      createSchema({
        name: newName,
        description: "",
        // New user schemas inherit the visible family so they
        // stay scoped to the page they were created on. Pass null when
        // viewing All families so the schema is created unscoped.
        family: family ?? null,
        content: { type: "object", properties: {}, additionalProperties: false },
      }),
    onSuccess: (s) => {
      setNewName("");
      setSelectedId(s.id);
      onChanged();
    },
    onError: (e: unknown) => setError(errMsg(e)),
  });

  const deleteMut = useMutation({
    mutationFn: (id: string) => deleteSchema(id),
    onSuccess: () => {
      setSelectedId(null);
      onChanged();
    },
    onError: (e: unknown) => setError(errMsg(e)),
  });

  const handleSave = () => {
    setError(null);
    try {
      JSON.parse(draftText);
    } catch (e) {
      setError(`Invalid JSON: ${(e as Error).message}`);
      return;
    }
    saveMut.mutate();
  };

  const handleDelete = () => {
    if (!selected || isBundled) return;
    if (
      window.confirm(
        `Delete schema "${selected.name}"? Variants referencing it will fall back to no schema.`,
      )
    ) {
      deleteMut.mutate(selected.id);
    }
  };

  return (
    <section
      className="border-t bg-muted/10 p-4"
      aria-label="Schema manager"
      data-testid="schema-manager"
    >
      <h3 className="mb-2 text-xs font-semibold uppercase text-muted-foreground">
        Schemas
      </h3>
      <div className="flex gap-3 text-xs">
        <ul
          className="w-48 shrink-0 space-y-1 overflow-auto"
          role="listbox"
          aria-label="Schemas"
        >
          {schemas.map((s) => (
            <li key={`${s.source}:${s.id}`}>
              <button
                type="button"
                role="option"
                aria-selected={selected?.id === s.id && selected?.source === s.source}
                onClick={() => setSelectedId(s.id)}
                data-testid={`schema-row-${s.id}`}
                className={
                  "w-full rounded border px-2 py-1 text-left hover:bg-accent " +
                  (selected?.id === s.id && selected?.source === s.source
                    ? "bg-accent"
                    : "")
                }
              >
                <div className="truncate font-medium">{s.name}</div>
                <div className="truncate text-[10px] text-muted-foreground">
                  {s.id} · {s.source}
                </div>
              </button>
            </li>
          ))}
          {schemas.length === 0 && (
            <li className="text-muted-foreground">No schemas yet.</li>
          )}
        </ul>
        <div className="flex-1">
          {selected ? (
            <div className="flex flex-col gap-2" data-testid="schema-detail">
              <input
                data-testid="schema-name-input"
                className="rounded border px-2 py-1 text-xs"
                value={draftName}
                disabled={isBundled}
                onChange={(e) => setDraftName(e.target.value)}
                placeholder="Schema name"
              />
              <input
                data-testid="schema-description-input"
                className="rounded border px-2 py-1 text-xs"
                value={draftDesc}
                disabled={isBundled}
                onChange={(e) => setDraftDesc(e.target.value)}
                placeholder="Description"
              />
              <textarea
                data-testid="schema-content-input"
                rows={12}
                className="rounded border px-2 py-1 font-mono text-[11px]"
                value={draftText}
                disabled={isBundled}
                onChange={(e) => setDraftText(e.target.value)}
              />
              <div className="flex items-center gap-2">
                <button
                  type="button"
                  data-testid="schema-save-button"
                  disabled={isBundled || saveMut.isPending}
                  onClick={handleSave}
                  className="rounded bg-foreground px-3 py-1 text-xs text-background disabled:opacity-40"
                >
                  {saveMut.isPending ? "Saving…" : "Save"}
                </button>
                {!isBundled && (
                  <button
                    type="button"
                    data-testid="schema-delete-button"
                    onClick={handleDelete}
                    disabled={deleteMut.isPending}
                    className="rounded border border-red-300 px-3 py-1 text-xs text-red-700 hover:bg-red-50 disabled:opacity-40"
                  >
                    {deleteMut.isPending ? "Deleting…" : "Delete"}
                  </button>
                )}
                {isBundled && (
                  <span className="text-muted-foreground">
                    Bundled schemas are read-only. Duplicate to edit.
                  </span>
                )}
                {error && <span className="text-red-600">{error}</span>}
              </div>
            </div>
          ) : (
            <p className="text-muted-foreground">Select a schema to inspect.</p>
          )}

          <form
            className="mt-3 flex items-center gap-2"
            onSubmit={(e) => {
              e.preventDefault();
              if (newName.trim()) createMut.mutate();
            }}
          >
            <input
              data-testid="schema-new-name"
              className="rounded border px-2 py-1 text-xs"
              placeholder="New schema name"
              value={newName}
              onChange={(e) => setNewName(e.target.value)}
            />
            <button
              type="submit"
              data-testid="schema-create-button"
              disabled={!newName.trim() || createMut.isPending}
              className="rounded border px-2 py-1 text-xs hover:bg-accent disabled:opacity-40"
            >
              {createMut.isPending ? "Creating…" : "Create"}
            </button>
          </form>
        </div>
      </div>
    </section>
  );
}

export default PromptLibraryPage;
