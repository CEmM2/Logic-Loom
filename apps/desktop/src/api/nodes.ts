import { api, apiUrl } from "./client";

export type NodeFile = {
  path: string;
  name: string;
  filename: string;
  folder: string;
  relative: string;
  id: string;
  title: string;
  domain: string;
  status: string;
  akms_schema: string;
  is_node: boolean;
  // Provenance fields — undefined on older payloads → treat as Tier B.
  is_established?: boolean;
  tier?: "A" | null;
  verified_by?: string | null;
  verified_at?: string | null;
  version?: string | null;
  source_ref?: string | null;
};

export type CopyToVaultResult = {
  status: string;
  original_id: string;
  copy_id: string;
  copy_path: string;
  // The copy is always a Tier-B draft (backend contract, routes/nodes.py copy_to_vault).
  tier: "B";
  node_status: "draft";
};

export type NodeContent = {
  path: string;
  content: string;
  frontmatter: Record<string, string>;
};

export type PromoteParams = {
  source: string;
  vault?: string;
  promote?: boolean;
  execute?: boolean;
};

export async function listNodes(): Promise<NodeFile[]> {
  return api<NodeFile[]>("/api/nodes/files");
}

export async function readNode(path: string): Promise<NodeContent> {
  return api<NodeContent>(`/api/nodes/file?${new URLSearchParams({ path })}`);
}

export async function openInEditor(path: string): Promise<{ status: string; cmd: string }> {
  return api("/api/nodes/open", {
    method: "POST",
    headers: { "content-type": "application/json" },
    body: JSON.stringify({ path }),
  });
}

export async function copyToVault(path: string): Promise<CopyToVaultResult> {
  return api<CopyToVaultResult>("/api/nodes/copy-to-vault", {
    method: "POST",
    headers: { "content-type": "application/json" },
    body: JSON.stringify({ path }),
  });
}

export async function createPromoteStream(params: PromoteParams): Promise<EventSource> {
  const qs = new URLSearchParams({ source: params.source });
  if (params.vault) qs.set("vault", params.vault);
  if (params.promote) qs.set("promote", "true");
  if (params.execute) qs.set("execute", "true");
  return new EventSource(await apiUrl(`/api/nodes/promote/stream?${qs}`));
}
