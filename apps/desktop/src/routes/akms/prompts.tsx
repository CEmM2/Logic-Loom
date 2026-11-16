import { PromptLibraryPage } from "../../components/prompts/PromptLibraryPage";

/**
 * AKMS-mode Prompt Library route.
 *
 * Renders the shared `PromptLibraryPage` filtered to `akms.*` slots.
 * All three mode-specific routes mount the same component,
 * differing only in the `family` prop.
 */
export default function AkmsPromptsRoute() {
  return <PromptLibraryPage family="akms" />;
}
