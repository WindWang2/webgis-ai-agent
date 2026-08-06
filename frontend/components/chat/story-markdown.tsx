"use client"

import ReactMarkdown from "react-markdown"
import remarkGfm from "remark-gfm"

// Lazy-loadable markdown renderer for the story page (bundle-slimming):
// keeps react-markdown + remark-gfm out of the /story route's first load.
export default function StoryMarkdown({ text }: { text: string }) {
  return (
    <ReactMarkdown remarkPlugins={[remarkGfm]}>
      {text}
    </ReactMarkdown>
  )
}
