import { Group, Panel, Separator } from "react-resizable-panels";
import { useQuery } from "@tanstack/react-query";
import { Route, Routes, useNavigate, useParams } from "react-router-dom";

import { getFile } from "../../../api/vault";
import { FrontmatterCard } from "../../../components/FrontmatterCard";
import { MarkdownPreview } from "../../../components/MarkdownPreview";
import { VaultSearch } from "../../../components/VaultSearch";
import { VaultTree } from "../../../components/VaultTree";

function FilePane({ path }: { path: string | null }) {
  const { data, isLoading, error } = useQuery({
    queryKey: ["vault", "file", path],
    queryFn: () => getFile(path!),
    enabled: !!path,
  });

  if (!path) {
    return (
      <div className="flex h-full items-center justify-center p-4 text-sm text-muted-foreground">
        Select a file from the tree.
      </div>
    );
  }
  if (isLoading) {
    return (
      <div className="flex h-full items-center justify-center p-4 text-sm text-muted-foreground">
        Loading…
      </div>
    );
  }
  if (error || !data) {
    return (
      <div className="flex h-full items-center justify-center p-4 text-sm text-red-600">
        {(error as Error)?.message ?? "Failed to load file"}
      </div>
    );
  }

  return (
    <div className="flex h-full flex-col gap-3 overflow-y-auto p-4">
      <header>
        <h2 className="text-lg font-semibold">{data.path}</h2>
        <p className="text-xs text-muted-foreground">
          {data.size} bytes · {data.mtime}
        </p>
      </header>
      <FrontmatterCard data={data.frontmatter} />
      <MarkdownPreview content={data.content} />
    </div>
  );
}

/**
 * Single layout component for the whole vault tab.
 * selectedPath is always derived from the URL so wikilink navigation
 * (which changes the URL) immediately shows the new file without needing
 * separate local state that would go stale on same-route re-renders.
 */
function VaultLayout() {
  const params = useParams();
  const nav = useNavigate();

  // Decode the wildcard segment; undefined when on the index route.
  const selectedPath = params["*"] ? decodeURIComponent(params["*"]) : null;

  const onSelect = (path: string) => {
    nav(`/zsum/vault/file/${encodeURIComponent(path)}`);
  };

  return (
    <Group orientation="horizontal" className="h-full">
      <Panel defaultSize={25} minSize={16} className="flex flex-col overflow-hidden border-r border-border">
        <div className="border-b border-border shrink-0">
          <VaultSearch />
        </div>
        <div className="flex-1 overflow-y-auto p-2">
          <VaultTree selectedPath={selectedPath} onSelect={onSelect} />
        </div>
      </Panel>

      <Separator className="w-1 bg-border hover:bg-primary/30 transition-colors cursor-col-resize" />

      <Panel defaultSize={75} minSize={40} className="overflow-hidden">
        <FilePane path={selectedPath} />
      </Panel>
    </Group>
  );
}

export default function VaultRoute() {
  return (
    <Routes>
      <Route index element={<VaultLayout />} />
      <Route path="file/*" element={<VaultLayout />} />
    </Routes>
  );
}
