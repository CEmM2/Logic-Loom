import { PromptLibraryPage } from "../../components/prompts/PromptLibraryPage";

/**
 * Zsum-mode Prompt Library route.
 *
 * Renders the shared `PromptLibraryPage` filtered to `zsum.*` slots.
 * All three mode-specific routes mount the same component,
 * differing only in the `family` prop.
 */
export default function PromptsRoute() {
  return <PromptLibraryPage family="zsum" />;
}
