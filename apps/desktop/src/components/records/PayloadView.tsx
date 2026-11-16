/**
 * Generic, schema-aware payload renderer.
 *
 * Payloads follow user-registered JSON Schemas, so nothing here knows a field
 * name in advance. When the record's schema is available its `properties`
 * supply titles, descriptions and field order; any key the schema does not
 * declare is still shown, after the declared ones.
 */

type JsonSchema = {
  title?: string;
  description?: string;
  properties?: Record<string, JsonSchema>;
  items?: JsonSchema;
};

interface Props {
  value: unknown;
  schema?: JsonSchema | null;
}

export function PayloadView({ value, schema }: Props) {
  if (value === null || value === undefined) {
    return <span className="text-muted-foreground">—</span>;
  }
  if (Array.isArray(value)) {
    if (value.length === 0) return <span className="text-muted-foreground">[ ]</span>;
    return (
      <ul className="list-disc space-y-1 pl-5">
        {value.map((item, index) => (
          <li key={index}>
            <PayloadView value={item} schema={schema?.items ?? null} />
          </li>
        ))}
      </ul>
    );
  }
  if (typeof value === "object") {
    const object = value as Record<string, unknown>;
    const declared = Object.keys(schema?.properties ?? {});
    const keys = [
      ...declared.filter((key) => key in object),
      ...Object.keys(object).filter((key) => !declared.includes(key)),
    ];
    if (keys.length === 0) return <span className="text-muted-foreground">{"{ }"}</span>;
    return (
      <dl className="grid grid-cols-[minmax(8rem,max-content)_1fr] gap-x-4 gap-y-2">
        {keys.map((key) => {
          const fieldSchema = schema?.properties?.[key];
          return (
            <div key={key} className="contents">
              <dt
                className="text-xs font-semibold text-muted-foreground"
                title={fieldSchema?.description ?? (declared.includes(key) ? "" : "not declared in the schema")}
              >
                {fieldSchema?.title ?? key}
                {!declared.includes(key) && schema ? <span className="ml-1 opacity-60">*</span> : null}
              </dt>
              <dd className="min-w-0 break-words text-sm">
                <PayloadView value={object[key]} schema={fieldSchema ?? null} />
              </dd>
            </div>
          );
        })}
      </dl>
    );
  }
  if (typeof value === "boolean") return <code className="font-mono text-xs">{String(value)}</code>;
  if (typeof value === "number") return <code className="font-mono text-xs">{value}</code>;
  return <span className="whitespace-pre-wrap">{String(value)}</span>;
}
