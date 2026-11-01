"use client";

import type { Element, ElementContent, Root } from "hast";
import { useTranslations } from "next-intl";
import { useMemo, type ReactNode } from "react";
import ReactMarkdown, { type Components } from "react-markdown";
import remarkGfm from "remark-gfm";

// Model output is untrusted, because a dataset cell can steer it. Only these elements render, raw
// HTML is dropped, and images are out: loading a planted image address would send data to it.
const ALLOWED = [
  "p", "strong", "em", "del", "ul", "ol", "li", "h1", "h2", "h3", "h4", "h5", "h6", "blockquote", "hr", "br", "a",
  "code", "pre", "table", "thead", "tbody", "tr", "th", "td", "bdi",
];
const SAFE_LINK = /^(https:|mailto:)/i;
// Dates and ranges stay one piece: split at the hyphens, they would reverse in right-to-left text.
const NUMBER = /[-+]?[\d٠-٩]+(?:[,.٬٫:/-][\d٠-٩]+)*%?/g;

function isolated(value: string): ElementContent[] {
  const out: ElementContent[] = [];
  let last = 0;
  for (const match of value.matchAll(NUMBER)) {
    if (match.index > last) out.push({ type: "text", value: value.slice(last, match.index) });
    out.push({ type: "element", tagName: "bdi", properties: {}, children: [{ type: "text", value: match[0] }] });
    last = match.index + match[0].length;
  }
  if (last < value.length) out.push({ type: "text", value: value.slice(last) });
  return out;
}

function isolateIn(node: Root | Element): void {
  const children = node.children as ElementContent[];
  node.children = children.flatMap((child) => {
    if (child.type === "text") return isolated(child.value);
    if (child.type === "element" && child.tagName !== "code" && child.tagName !== "pre") isolateIn(child);
    return [child];
  });
}

/** Keep each number in order inside right-to-left text, signs and percent marks included. */
function rehypeIsolateNumbers() {
  return (tree: Root) => isolateIn(tree);
}

const HEADING = "font-semibold text-foreground";

function headingAs(Tag: "h3" | "h4" | "h5", size: string): (props: { children?: ReactNode }) => ReactNode {
  // An answer sits under the page's own headings, so its levels start lower.
  return function Heading({ children }) {
    return (
      <Tag dir="auto" className={`${HEADING} ${size} text-start`}>
        {children}
      </Tag>
    );
  };
}

/** Render an answer's markdown as React elements, styled like the rest of the report. */
export function Markdown({ text }: { text: string }) {
  const t = useTranslations("chat");
  const components = useMemo<Components>(
    () => ({
      p: ({ children }) => (
        <p dir="auto" className="text-start">
          {children}
        </p>
      ),
      h1: headingAs("h3", "text-lg"),
      h2: headingAs("h3", "text-lg"),
      h3: headingAs("h4", "text-base"),
      h4: headingAs("h5", "text-base"),
      h5: headingAs("h5", "text-sm"),
      h6: headingAs("h5", "text-sm"),
      ul: ({ children }) => (
        <ul dir="auto" className="list-disc space-y-1 ps-5 text-start">
          {children}
        </ul>
      ),
      ol: ({ children }) => (
        <ol dir="auto" className="list-decimal space-y-1 ps-5 text-start">
          {children}
        </ol>
      ),
      li: ({ children }) => <li dir="auto">{children}</li>,
      blockquote: ({ children }) => (
        <blockquote className="border-s-2 border-border ps-3 text-muted-foreground">{children}</blockquote>
      ),
      a: ({ href, children }) =>
        href ? (
          <a
            href={href}
            target="_blank"
            rel="noopener noreferrer nofollow"
            className="text-primary underline underline-offset-4"
          >
            {children}
          </a>
        ) : (
          <span>{children}</span>
        ),
      code: ({ children }) => (
        <code dir="ltr" className="rounded-xs bg-muted px-1 py-0.5 text-[0.9em]">
          {children}
        </code>
      ),
      pre: ({ children }) => (
        <pre
          dir="ltr"
          tabIndex={0}
          className="overflow-x-auto rounded-md border bg-muted p-3 text-xs leading-relaxed [&>code]:bg-transparent [&>code]:p-0"
        >
          {children}
        </pre>
      ),
      table: ({ children }) => (
        <div tabIndex={0} role="region" aria-label={t("table")} className="max-w-full overflow-x-auto rounded-md border">
          <table className="w-full text-sm">{children}</table>
        </div>
      ),
      thead: ({ children }) => <thead className="bg-muted">{children}</thead>,
      tbody: ({ children }) => <tbody className="divide-y">{children}</tbody>,
      th: ({ children }) => (
        <th dir="auto" scope="col" className="px-3 py-2 text-start font-medium whitespace-nowrap">
          {children}
        </th>
      ),
      td: ({ children }) => (
        <td dir="auto" className="px-3 py-1.5 text-start tabular-nums">
          {children}
        </td>
      ),
    }),
    [t],
  );
  return (
    <div className="min-w-0 space-y-3 break-words [overflow-wrap:anywhere]">
      <ReactMarkdown
        remarkPlugins={[remarkGfm]}
        rehypePlugins={[rehypeIsolateNumbers]}
        allowedElements={ALLOWED}
        unwrapDisallowed
        skipHtml
        urlTransform={(url) => (SAFE_LINK.test(url) ? url : "")}
        components={components}
      >
        {text}
      </ReactMarkdown>
    </div>
  );
}
