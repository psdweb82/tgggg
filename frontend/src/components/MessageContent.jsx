import React, { useMemo, useState } from "react";
import ReactMarkdown from "react-markdown";
import remarkGfm from "remark-gfm";
import { Prism as SyntaxHighlighter } from "react-syntax-highlighter";
import { oneDark } from "react-syntax-highlighter/dist/esm/styles/prism";
import { Check, Copy } from "lucide-react";
import GeneratedImage from "./GeneratedImage";

function CodeBlock({ inline, className, children }) {
  const [copied, setCopied] = useState(false);
  const match = /language-(\w+)/.exec(className || "");
  const code = String(children).replace(/\n$/, "");

  if (inline || !match) return <code className={className}>{children}</code>;

  const copy = () => {
    navigator.clipboard.writeText(code);
    setCopied(true);
    setTimeout(() => setCopied(false), 1500);
  };

  return (
    <div data-testid="code-block" className="my-3 rounded-xl overflow-hidden border border-white/10 bg-[#050505]">
      <div className="flex items-center justify-between px-4 py-2 border-b border-white/10 bg-white/[0.02]">
        <span className="text-[11px] uppercase tracking-wider text-muted-foreground font-mono">{match[1]}</span>
        <button onClick={copy} className="inline-flex items-center gap-1.5 text-[11px] text-muted-foreground hover:text-white transition-colors">
          {copied ? <><Check className="h-3 w-3" /> Скопировано</> : <><Copy className="h-3 w-3" /> Копировать</>}
        </button>
      </div>
      <SyntaxHighlighter
        language={match[1]}
        style={oneDark}
        customStyle={{ margin: 0, background: "transparent", padding: "1rem", fontSize: "0.83rem" }}
        codeTagProps={{ style: { fontFamily: "'JetBrains Mono', monospace" } }}
      >
        {code}
      </SyntaxHighlighter>
    </div>
  );
}

/**
 * Renders assistant markdown with a Grok-like smooth streaming effect.
 * When `streaming` is true, the newest tail (`streamingLen` chars) fades in.
 */
export default function MessageContent({ content, images = [], streaming = false, streamingLen = 0 }) {
  // Split into "stable" (already shown) and "tail" (currently being revealed).
  const { stable, tail } = useMemo(() => {
    if (!streaming || !streamingLen || !content) return { stable: content || "", tail: "" };
    const cut = Math.max(0, content.length - streamingLen);
    return { stable: content.slice(0, cut), tail: content.slice(cut) };
  }, [content, streaming, streamingLen]);

  return (
    <div className="md-body">
      {stable && (
        <ReactMarkdown remarkPlugins={[remarkGfm]} components={{ code: CodeBlock }}>
          {stable}
        </ReactMarkdown>
      )}
      {tail && (
        <span className="stream-tail" key={stable.length}>
          {tail}
        </span>
      )}
      {images && images.length > 0 && (
        <div className="mt-3 flex flex-col gap-3">
          {images.map((img, i) => (
            <GeneratedImage key={i} src={img.data} mime={img.mime} />
          ))}
        </div>
      )}
    </div>
  );
}
