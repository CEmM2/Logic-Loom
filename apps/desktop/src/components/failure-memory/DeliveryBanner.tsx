import type { DeliveryReport } from "../../api/failureMemory";

/**
 * The single most important widget in this workspace.
 *
 * Four situations produce an empty or thin "required" list, and two of them mean
 * completely different things:
 *
 *   • `no_lessons_apply` — no compiled route matches any input path. Nothing was
 *     suppressed; there was nothing to deliver.
 *   • `delivered_nowhere` — a lesson HAS a live compiled route for one of the
 *     input paths and still arrived in no lane at all, while the provider
 *     reported success. The knowledge exists, is compiled, is routed, and was
 *     not delivered.
 *
 * Rendered identically, an operator reads the second as the first and concludes
 * the lesson is inactive when it is merely unroutable. So the two are given
 * different severities, different colours, different headings and different
 * body copy, and `delivered_nowhere` names the affected lesson nodes explicitly.
 *
 * Every string here is descriptive of evidence the backend computed from the
 * compiled route index versus the provider's own records — the banner asserts
 * nothing of its own.
 */

const STYLES: Record<
  DeliveryReport["verdict"],
  { tone: string; heading: string; icon: string }
> = {
  delivered_nowhere: {
    tone: "border-red-500/60 bg-red-500/10 text-red-900 dark:text-red-200",
    heading: "Routed, and delivered nowhere",
    icon: "⛔",
  },
  downgraded_to_advisory: {
    tone: "border-amber-500/60 bg-amber-500/10 text-amber-900 dark:text-amber-200",
    heading: "Downgraded to advisory",
    icon: "⚠️",
  },
  delivered: {
    tone: "border-emerald-500/60 bg-emerald-500/10 text-emerald-900 dark:text-emerald-200",
    heading: "Delivered",
    icon: "✓",
  },
  no_lessons_apply: {
    tone: "border-border bg-muted/50 text-muted-foreground",
    heading: "No lessons apply",
    icon: "○",
  },
};

interface Props {
  delivery: DeliveryReport;
}

export function DeliveryBanner({ delivery }: Props) {
  const style = STYLES[delivery.verdict];
  return (
    <section
      role="status"
      aria-live="polite"
      data-verdict={delivery.verdict}
      className={`space-y-2 rounded border p-4 ${style.tone}`}
    >
      <h2 className="flex items-center gap-2 text-sm font-semibold">
        <span aria-hidden>{style.icon}</span>
        {style.heading}
      </h2>
      <p className="text-sm">{delivery.headline}</p>

      {delivery.verdict === "delivered_nowhere" && (
        <div className="space-y-1 text-sm">
          <p className="font-medium">
            This is not an empty result. These lessons have a live compiled route for
            one of the paths below and were not delivered in any lane:
          </p>
          <ul className="list-disc pl-5 font-mono text-xs">
            {delivery.delivered_nowhere_node_ids.map((nodeId) => (
              <li key={nodeId}>{nodeId}</li>
            ))}
          </ul>
        </div>
      )}

      {delivery.verdict === "downgraded_to_advisory" && (
        <ul className="list-disc pl-5 font-mono text-xs">
          {delivery.downgraded_node_ids.map((nodeId) => (
            <li key={nodeId}>{nodeId}</li>
          ))}
        </ul>
      )}

      {delivery.verdict === "no_lessons_apply" && (
        <p className="text-xs">
          No compiled route in this project matches any of the paths below, so the
          empty required lane is genuinely empty. If you expected a lesson here,
          check the Routes tab — an uncompiled or stale route index looks the same
          from this side.
        </p>
      )}

      {delivery.documentation_only && delivery.verdict !== "delivered" && (
        <p className="text-xs">
          Every input path classifies as documentation. AKMS drops all exact-path
          specs in that case, so a documentation lesson can only arrive through a
          matching advisory tag — and adding a single non-documentation path to the
          same task restores it to required. Documentation means any{" "}
          <code>.md</code> / <code>.mdx</code> / <code>.rst</code> / <code>.adoc</code>{" "}
          / <code>.asciidoc</code> file anywhere, not only files under{" "}
          <code>docs/</code>.
        </p>
      )}

      {delivery.tag_only_node_ids.length > 0 && (
        <p className="text-xs">
          Arrived without a compiled path route of their own — through an advisory
          tag match or a <code>load_with</code> co-activation; the headline above
          says which:{" "}
          <span className="font-mono">{delivery.tag_only_node_ids.join(", ")}</span>
        </p>
      )}

      <dl className="grid grid-cols-2 gap-x-6 gap-y-1 pt-1 text-xs sm:grid-cols-4">
        <Stat label="Input paths" value={delivery.input_paths.length} />
        <Stat label="Routed candidates" value={delivery.routed_candidate_node_ids.length} />
        <Stat label="Delivered" value={delivery.delivered_node_ids.length} />
        <Stat label="Required" value={delivery.required_node_ids.length} />
      </dl>

      {delivery.provider_derived_paths.length > 0 && (
        <p className="text-xs">
          Computed by the provider from the diff, not supplied in the request:{" "}
          <span className="font-mono">
            {delivery.provider_derived_paths.join(", ")}
          </span>
        </p>
      )}

      {delivery.empty_diff_fallback && (
        <p className="text-xs">
          The diff resolved to no paths, so AKMS fell back to the task's declared
          scope — this post-diff answer is really a pre-task answer.
        </p>
      )}

      {delivery.documentation_paths.length > 0 && (
        <p className="text-xs">
          Documentation paths:{" "}
          <span className="font-mono">{delivery.documentation_paths.join(", ")}</span>
        </p>
      )}
    </section>
  );
}

function Stat({ label, value }: { label: string; value: number }) {
  return (
    <div>
      <dt className="opacity-70">{label}</dt>
      <dd className="font-semibold" data-stat={label}>
        {value}
      </dd>
    </div>
  );
}
