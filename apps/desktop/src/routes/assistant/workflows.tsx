import { WorkflowsPage } from "../../components/workflows/WorkflowsPage";

/**
 * Assistant-mode Workflows route (the primary mount of the shared page).
 *
 * Renders the shared `WorkflowsPage` unfiltered — assistant mode shows
 * both Claude and zsum workflows in a single catalog.
 */
export default function AssistantWorkflowsRoute() {
  return <WorkflowsPage executor={null} />;
}
