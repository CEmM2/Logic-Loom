/**
 * Typed wrappers for the read-only vault API.
 */

import { api } from "./client";

export type TreeNode = {
  name: string;
  path: string;
  kind: "dir" | "file";
  children?: TreeNode[] | null;
};

export type VaultFile = {
  path: string;
  content: string;
  frontmatter: Record<string, unknown>;
  mtime: string;
  size: number;
};

export type ResolveResult = {
  path: string | null;
  exists: boolean;
};

export async function getTree(root = "", depth = 2): Promise<TreeNode[]> {
  const params = new URLSearchParams({ depth: String(depth) });
  if (root) params.set("root", root);
  return api(`/api/vault/tree?${params}`);
}

export async function getFile(path: string): Promise<VaultFile> {
  const params = new URLSearchParams({ path });
  return api(`/api/vault/file?${params}`);
}

export async function resolveWikilink(wikilink: string): Promise<ResolveResult> {
  const params = new URLSearchParams({ wikilink });
  return api(`/api/vault/resolve?${params}`);
}

export async function listVaultCollections(): Promise<{ name: string }[]> {
  return api("/api/vault/collections");
}
