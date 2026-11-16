/**
 * Remark plugin that turns ``[[Some Note]]`` into MDAST link nodes with a
 * sentinel ``wikilink:`` URL scheme. The MarkdownPreview component intercepts
 * those links via the ``a`` component override and resolves them through
 * /api/vault/resolve.
 *
 * Using a real ``link`` node (instead of a custom node type) plays nicely
 * with react-markdown's typed component map.
 */

import type { Link, PhrasingContent, Root, Text } from "mdast";
import { SKIP, visit } from "unist-util-visit";

const WIKILINK_RE = /\[\[([^[\]\n]+)\]\]/g;
const SCHEME = "wikilink:";

export function remarkWikilink() {
  return (tree: Root) => {
    visit(tree, "text", (node: Text, index, parent) => {
      if (!parent || index == null) return;
      const value = node.value;
      if (!value.includes("[[")) return;

      const out: PhrasingContent[] = [];
      let last = 0;
      WIKILINK_RE.lastIndex = 0;
      let m: RegExpExecArray | null;
      while ((m = WIKILINK_RE.exec(value)) !== null) {
        if (m.index > last) {
          out.push({ type: "text", value: value.slice(last, m.index) });
        }
        const target = m[1].trim();
        const link: Link = {
          type: "link",
          url: `${SCHEME}${encodeURIComponent(target)}`,
          title: null,
          children: [{ type: "text", value: target }],
        };
        out.push(link);
        last = m.index + m[0].length;
      }
      if (last === 0) return;
      if (last < value.length) {
        out.push({ type: "text", value: value.slice(last) });
      }
      const children = (parent as { children: PhrasingContent[] }).children;
      children.splice(index, 1, ...out);
      return [SKIP, index + out.length];
    });
  };
}

export const WIKILINK_SCHEME = SCHEME;
