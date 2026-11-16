import { PromptLibraryPage } from "../../components/prompts/PromptLibraryPage";

/**
 * Assistant-mode Prompt Library route.
 *
 * Renders the shared `PromptLibraryPage` with no family filter (all
 * eight canonical slots are visible). All three
 * mode-specific routes mount the same component, differing only in
 * the `family` prop.
 */
export default function AssistantPromptsRoute() {
  return <PromptLibraryPage family={null} />;
}
