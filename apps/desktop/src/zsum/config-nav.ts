export interface ZsumConfigNavGroup {
  id: string;
  label: string;
  sectionIds: readonly string[];
}

export const ZSUM_CONFIG_NAV_GROUPS: readonly ZsumConfigNavGroup[] = [
  { id: "paths", label: "Paths", sectionIds: ["paths"] },
  { id: "scope", label: "Scope & Extraction", sectionIds: ["scope", "extraction"] },
  {
    id: "llm",
    label: "LLM",
    sectionIds: [
      "llm-routing",
      "llm-anthropic",
      "llm-openai-compat",
      "llm-codex-exec",
      "llm-gemini-cli",
    ],
  },
  { id: "output", label: "Output", sectionIds: ["output", "versioning"] },
  { id: "citations", label: "Citations", sectionIds: ["citations"] },
  { id: "restructure", label: "Restructure", sectionIds: ["restructure"] },
];