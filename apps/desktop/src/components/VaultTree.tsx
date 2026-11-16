import { useQuery } from "@tanstack/react-query";
import { useState } from "react";

import { getTree, type TreeNode } from "../api/vault";
import { cn } from "../lib/utils";

interface Props {
  selectedPath: string | null;
  onSelect: (path: string) => void;
}

export function VaultTree({ selectedPath, onSelect }: Props) {
  const { data: nodes = [], isLoading, error } = useQuery({
    queryKey: ["vault", "tree", "", 2],
    queryFn: () => getTree("", 2),
  });

  if (isLoading) return <div className="p-3 text-sm text-muted-foreground">Loading vault…</div>;
  if (error) {
    return (
      <div className="p-3 text-sm text-red-600">
        {(error as Error).message}
      </div>
    );
  }

  return (
    <ul className="text-sm">
      {nodes.map((n) => (
        <TreeItem key={n.path} node={n} depth={0} selectedPath={selectedPath} onSelect={onSelect} />
      ))}
    </ul>
  );
}

function TreeItem({
  node,
  depth,
  selectedPath,
  onSelect,
}: {
  node: TreeNode;
  depth: number;
  selectedPath: string | null;
  onSelect: (path: string) => void;
}) {
  const [open, setOpen] = useState(depth < 1);
  const { data: lazy } = useQuery({
    queryKey: ["vault", "tree", node.path, 2],
    queryFn: () => getTree(node.path, 2),
    enabled: node.kind === "dir" && open && !node.children,
  });
  const children = node.children ?? lazy ?? [];

  if (node.kind === "file") {
    const isSelected = selectedPath === node.path;
    return (
      <li>
        <button
          onClick={() => onSelect(node.path)}
          style={{ paddingLeft: `${0.5 + depth * 0.75}rem` }}
          className={cn(
            "block w-full truncate rounded px-1 py-0.5 text-left hover:bg-muted",
            isSelected && "bg-muted",
          )}
        >
          {node.name}
        </button>
      </li>
    );
  }

  return (
    <li>
      <button
        onClick={() => setOpen((o) => !o)}
        style={{ paddingLeft: `${0.5 + depth * 0.75}rem` }}
        className="flex w-full items-center gap-1 rounded px-1 py-0.5 text-left text-muted-foreground hover:bg-muted"
      >
        <span className="inline-block w-3">{open ? "▾" : "▸"}</span>
        <span className="truncate font-medium">{node.name}</span>
      </button>
      {open && children.length > 0 && (
        <ul>
          {children.map((c) => (
            <TreeItem
              key={c.path}
              node={c}
              depth={depth + 1}
              selectedPath={selectedPath}
              onSelect={onSelect}
            />
          ))}
        </ul>
      )}
    </li>
  );
}
