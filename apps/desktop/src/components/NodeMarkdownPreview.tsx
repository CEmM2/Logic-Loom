import katex from "katex";
import "katex/dist/katex.min.css";
import ReactMarkdown, { defaultUrlTransform } from "react-markdown";
import rehypeKatex from "rehype-katex";
import remarkGfm from "remark-gfm";
import remarkMath from "remark-math";
import { useNavigate } from "react-router-dom";

import { Wikilink } from "./Wikilink";

const WIKILINK_SCHEME = "wikilink:";

function urlTransform(url: string): string {
  if (url.startsWith(WIKILINK_SCHEME)) return url;
  return defaultUrlTransform(url);
}

function preprocessWikilinks(text: string): string {
  return text.replace(/\[\[([^\[\]\n]+)\]\]/g, (_, inner) => {
    const target = inner.trim();
    return `[${target}](${WIKILINK_SCHEME}${encodeURIComponent(target)})`;
  });
}

// Convert $$\begin{algorithmic}...\end{algorithmic}$$ to fenced `algorithm` blocks.
// These environments are not supported by KaTeX, so we render them as styled
// pseudocode with inline math support per-line.
function preprocessAlgorithmicBlocks(text: string): string {
  return text.replace(/\$\$\s*\n([\s\S]*?)\n\s*\$\$/g, (match, inner) => {
    if (!/\\begin\{algorithmic\}/.test(inner)) return match;
    const body = inner
      .replace(/\\begin\{algorithm[^}]*\}|\\end\{algorithm[^}]*\}/g, "")
      .replace(/\\begin\{algorithmic[^}]*\}|\\end\{algorithmic[^}]*\}/g, "")
      .split("\n")
      .map((l: string) => l.trimEnd())
      .filter((l: string) => l.trim().length > 0)
      .join("\n");
    return "```algorithm\n" + body + "\n```";
  });
}

// Render inline $...$ within a plain text string using KaTeX.
function renderInlineMath(text: string): string {
  return text.replace(/\$([^$\n]+)\$/g, (_, math) => {
    try {
      return katex.renderToString(math.trim(), { throwOnError: false, displayMode: false });
    } catch {
      return `$${math}$`;
    }
  });
}

// Nesting-aware commands that increase indent for subsequent lines.
const INDENT_OPEN = /^\\(If|For|While|Function|Procedure|Repeat)\b/;
// Commands that close a block — dedent before rendering.
const INDENT_CLOSE = /^\\(EndIf|EndFor|EndWhile|EndFunction|EndProcedure|Until)\b/;

function parseAlgorithmLine(raw: string): string {
  return raw
    .replace(/^\\State\s*/, "")
    .replace(/^\\If\{([^}]+)\}/, "if $1 then")
    .replace(/^\\ElsIf\{([^}]+)\}/, "else if $1 then")
    .replace(/^\\Else\b/, "else")
    .replace(/^\\EndIf\b/, "end if")
    .replace(/^\\For\{([^}]+)\}/, "for $1 do")
    .replace(/^\\EndFor\b/, "end for")
    .replace(/^\\While\{([^}]+)\}/, "while $1 do")
    .replace(/^\\EndWhile\b/, "end while")
    .replace(/^\\Return\s*/, "return ")
    .replace(/^\\Require\s*/, "require: ")
    .replace(/^\\Ensure\s*/, "ensure: ")
    .replace(/^\\Function\{([^}]+)\}\{([^}]+)\}/, "function $1($2)")
    .replace(/^\\EndFunction\b/, "end function")
    .replace(/^\\Procedure\{([^}]+)\}\{([^}]+)\}/, "procedure $1($2)")
    .replace(/^\\EndProcedure\b/, "end procedure")
    .replace(/^\\Comment\{([^}]+)\}/, "// $1")
    .replace(/\\gets|\\leftarrow/g, "←")
    .replace(/\\rightarrow/g, "→")
    .replace(/\\neq/g, "≠")
    .replace(/\\leq/g, "≤")
    .replace(/\\geq/g, "≥");
}

function AlgorithmBlock({ code }: { code: string }) {
  let indent = 0;
  const items: Array<{ key: number; depth: number; html: string }> = [];

  code.split("\n").forEach((line, i) => {
    const trimmed = line.trim();
    if (!trimmed) return;

    if (INDENT_CLOSE.test(trimmed)) indent = Math.max(0, indent - 1);
    const depth = indent;
    if (INDENT_OPEN.test(trimmed)) indent++;

    const parsed = parseAlgorithmLine(trimmed);
    items.push({ key: i, depth, html: renderInlineMath(parsed) });
  });

  return (
    <div className="my-3 overflow-x-auto rounded border border-indigo-200 bg-indigo-50/40 dark:border-indigo-800 dark:bg-indigo-950/20">
      <div className="border-b border-indigo-200 px-3 py-1 text-[10px] font-semibold uppercase tracking-wider text-indigo-600 dark:border-indigo-800 dark:text-indigo-400">
        Algorithm
      </div>
      <div className="px-4 py-2 font-mono text-sm leading-relaxed">
        {items.map(({ key, depth, html }) => (
          <div
            key={key}
            style={{ paddingLeft: `${depth * 1.5}rem` }}
            className="py-0.5"
            dangerouslySetInnerHTML={{ __html: html }}
          />
        ))}
      </div>
    </div>
  );
}

interface Props {
  content: string;
}

export function NodeMarkdownPreview({ content }: Props) {
  const navigate = useNavigate();
  const processed = preprocessWikilinks(preprocessAlgorithmicBlocks(content));

  return (
    <div className="prose prose-sm max-w-none break-words">
      <ReactMarkdown
        remarkPlugins={[remarkGfm, remarkMath]}
        rehypePlugins={[[rehypeKatex, { throwOnError: false, trust: true }]]}
        urlTransform={urlTransform}
        components={{
          // Intercept `algorithm` fenced blocks for styled pseudocode rendering.
          code({ className, children, ...rest }) {
            const lang = (className ?? "").replace(/^language-/, "");
            if (lang === "algorithm") {
              return <AlgorithmBlock code={String(children).replace(/\n$/, "")} />;
            }
            return (
              <code className={className} {...rest}>
                {children}
              </code>
            );
          },
          a({ href, children, ...rest }) {
            if (typeof href === "string" && href.startsWith(WIKILINK_SCHEME)) {
              const target = decodeURIComponent(href.slice(WIKILINK_SCHEME.length));
              return <Wikilink value={target} />;
            }
            if (typeof href === "string" && /^https?:\/\//.test(href)) {
              return (
                <a
                  href={href}
                  target="_blank"
                  rel="noreferrer"
                  className="text-blue-600 hover:underline"
                  {...rest}
                >
                  {children}
                </a>
              );
            }
            if (typeof href === "string" && href) {
              return (
                <button
                  type="button"
                  onClick={() =>
                    navigate(`/vault/file/${encodeURIComponent(decodeURIComponent(href))}`)
                  }
                  className="cursor-pointer border-0 bg-transparent p-0 font-[inherit] text-blue-600 hover:underline"
                >
                  {children}
                </button>
              );
            }
            return <span {...rest}>{children}</span>;
          },
        }}
      >
        {processed}
      </ReactMarkdown>
    </div>
  );
}
