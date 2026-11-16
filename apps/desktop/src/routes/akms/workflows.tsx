import { WorkflowsPage } from "../../components/workflows/WorkflowsPage";

/**
 * AKMS-mode Workflows route.
 *
 * Renders the shared `WorkflowsPage` filtered to `executor === "claude"`.
 * The shared component follows the Prompt Library pattern: a
 * single module powers every mode-specific mount, the route only sets
 * the `executor` prop.
 */
export default function AkmsWorkflowsRoute() {
  return <WorkflowsPage executor="claude" />;
}
