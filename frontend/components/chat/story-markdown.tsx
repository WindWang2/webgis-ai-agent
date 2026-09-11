"use client"

import ReactMarkdown from "react-markdown"
import remarkGfm from "remark-gfm"
import { safeUrlTransform } from "./mini-md"
import { CitationAnchor, CitationArea, isCitationHref } from "./citation"

// Lazy-loadable markdown renderer for the story page (bundle-slimming):
// keeps react-markdown + remark-gfm out of the /story route's first load.
//
// V9 citation 扩展点（ADR-0145，契约 §8 ≤60 行）：`[n] 来源：…` 定义块剥离 +
// 已知编号 `[n]` 渲染为角标。无定义块的存量文本逐字节不变；非 citation
// 链接回落到与默认一致的 <a>（safeUrlTransform 仍在顶层生效，消毒不回退）。
export default function StoryMarkdown({ text }: { text: string }) {
  return (
    <CitationArea
      text={text}
      renderMarkdown={(body) => (
        <ReactMarkdown
          remarkPlugins={[remarkGfm]}
          urlTransform={safeUrlTransform}
          components={{
              a: ({ href, children }) => {
                if (isCitationHref(href)) return <CitationAnchor href={href ?? ""} />
                const safeHref = safeUrlTransform(href ?? "", "href", {
                  type: "element",
                  tagName: "a",
                  properties: {},
                  children: [],
                })
                return <a href={safeHref ?? undefined}>{children}</a>
              },
          }}
        >
          {body}
        </ReactMarkdown>
      )}
    />
  )
}
