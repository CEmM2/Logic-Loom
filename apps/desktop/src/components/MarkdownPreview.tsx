import ReactMarkdown, { defaultUrlTransform } from "react-markdown";
import remarkGfm from "remark-gfm";
import { useNavigate } from "react-router-dom";

import { Wikilink } from "./Wikilink";

const WIKILINK_SCHEME = "wikilink:";

// react-markdown v9's defaultUrlTransform strips any href whose scheme
// isn't on its safe-list (http/https/mailto/tel/...). Our preprocessor
// emits hrefs like "wikilink:%40foo", which would otherwise be sanitized
// to an empty string before the `a` component override sees them. Pass
// wikilink: through verbatim and delegate everything else to the default.
function urlTransform(url: string): string {
  if (url.startsWith(WIKILINK_SCHEME)) return url;
  return defaultUrlTransform(url);
}

// Remark's CommonMark parser splits [[target]] across multiple MDAST nodes
// (literal "[" + linkReference + literal "]"), so the plugin never sees "[[".
// Pre-process the raw string instead: turn [[target]] into a real markdown
// link with a sentinel scheme that the `a` component override picks up.
function preprocessWikilinks(text: string): string {
  return text.replace(/\[\[([^\[\]\n]+)\]\]/g, (_, inner) => {
    const target = inner.trim();
    return `[${target}](${WIKILINK_SCHEME}${encodeURIComponent(target)})`;
  });
}

interface Props {
  content: string;
}

export function MarkdownPreview({ content }: Props) {
  const navigate = useNavigate();
  const processed = preprocessWikilinks(content);

  return (
    <div className="prose prose-sm max-w-none break-words">
      <ReactMarkdown
        remarkPlugins={[remarkGfm]}
        urlTransform={urlTransform}
        components={{
          a: ({ href, children, ...rest }) => {
            if (typeof href === "string" && href.startsWith(WIKILINK_SCHEME)) {
              const target = decodeURIComponent(href.slice(WIKILINK_SCHEME.length));
              return <Wikilink value={target} />;
            }
            // External links → open in system browser
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
            // Relative vault links → navigate in-app.
            //
            // Target the canonical /zsum/vault/file/ path directly
            // instead of the legacy /vault/file/ form. The latter matches
            // the `/vault/*` redirect in App.tsx, which drops the file
            // segment and lands the user on a bare /zsum/vault with an
            // empty right panel. Same UX as the "View" affordance on a
            // paper card now.
            if (typeof href === "string" && href) {
              const vaultPath = decodeURIComponent(href);
              return (
                <button
                  type="button"
                  onClick={() =>
                    navigate(`/zsum/vault/file/${encodeURIComponent(vaultPath)}`)
                  }
                  className="text-blue-600 hover:underline cursor-pointer bg-transparent border-0 p-0 font-inherit"
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
