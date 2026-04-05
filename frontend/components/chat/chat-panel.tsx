"use client"

import { useState, useRef, useEffect } from "react"
import { Send, Paperclip, Bot, User, Trash2, Mic } from "lucide-react"
import ReactMarkdown from 'react-markdown'
import remarkGfm from 'remark-gfm'

interface Message {
  id: string
  role: "user" | "assistant" | "system"
  content: string
  timestamp: Date
  type?: "text" | "loading" | "error"
}

const quickTemplates = [
  { name: "缓冲区分析", prompt: "请帮我做缓冲区分析" },
  { name: "路径规划", prompt: "请帮我规划最优路径" },
  { name: "热力图生成", prompt: "请帮我生成热力图" },
  { name: "空间查询", prompt: "请帮我进行空间查询" },
  { name: "数据统计", prompt: "请帮我做空间数据统计" },
]

export function ChatPanel() {
  const [messages, setMessages] = useState<Message[]>([
    {
      id: "1",
      role: "assistant",
      content: "你好！我是 WebGIS AI 助手，可以帮助你进行空间数据分析和制图。请告诉我你想分析什么？",
      timestamp: new Date(),
    },
  ])
  const [input, setInput] = useState("")
  const [isLoading, setIsLoading] = useState(false)
  const messagesEndRef = useRef<HTMLDivElement>(null)
  const fileInputRef = useRef<HTMLInputElement>(null)

  // Auto scroll to bottom when messages change
  useEffect(() => {
    messagesEndRef.current?.scrollIntoView({ behavior: "smooth" })
  }, [messages])

  const handleSend = async () => {
    if (!input.trim() || isLoading) return

    const userMessage: Message = {
      id: Date.now().toString(),
      role: "user",
      content: input,
      timestamp: new Date(),
    }

    setMessages((prev) => [...prev, userMessage])
    setInput("")
    setIsLoading(true)

    // Add loading message
    const loadingMessage: Message = {
      id: (Date.now() + 1).toString(),
      role: "assistant",
      content: "正在思考中...",
      timestamp: new Date(),
      type: "loading",
    }
    setMessages((prev) => [...prev, loadingMessage])

    try {
      const response = await fetch("/api/ai/query", {
        method: "POST",
        headers: {
          "Content-Type": "application/json",
        },
        body: JSON.stringify({
          query: input,
          history: messages.map(m => ({ role: m.role, content: m.content })),
        }),
      })

      if (!response.ok) throw new Error("API request failed")

      const data = await response.json()

      // Replace loading message with actual response
      setMessages((prev) => 
        prev.map(m => 
          m.id === loadingMessage.id 
            ? {
                ...m,
                content: data.content,
                type: "text",
              }
            : m
        )
      )
    } catch (error) {
      // Replace loading message with error
      setMessages((prev) => 
        prev.map(m => 
          m.id === loadingMessage.id 
            ? {
                ...m,
                content: "抱歉，请求失败，请稍后重试。",
                type: "error",
              }
            : m
        )
      )
      console.error("Query error:", error)
    } finally {
      setIsLoading(false)
    }
  }

  const handleKeyPress = (e: React.KeyboardEvent) => {
    if (e.key === "Enter" && !e.shiftKey) {
      e.preventDefault()
      handleSend()
    }
  }

  const handleClearChat = () => {
    setMessages([
      {
        id: Date.now().toString(),
        role: "assistant",
        content: "你好！我是 WebGIS AI 助手，可以帮助你进行空间数据分析和制图。请告诉我你想分析什么？",
        timestamp: new Date(),
      },
    ])
  }

  const handleTemplateClick = (prompt: string) => {
    setInput(prompt)
  }

  const handleFileUpload = (e: React.ChangeEvent<HTMLInputElement>) => {
    const files = e.target.files
    if (!files || files.length === 0) return

    // TODO: Implement file upload logic
    const file = files[0]
    const allowedTypes = ['.shp', '.geojson', '.tiff', '.tif', '.json']
    const fileExt = file.name.substring(file.name.lastIndexOf('.')).toLowerCase()
    
    if (allowedTypes.includes(fileExt)) {
      const userMessage: Message = {
        id: Date.now().toString(),
        role: "user",
        content: `已上传文件：${file.name}`,
        timestamp: new Date(),
      }
      setMessages((prev) => [...prev, userMessage])
    } else {
      alert('不支持的文件格式，请上传shp/geojson/tiff格式的文件')
    }
    
    // Reset file input
    e.target.value = ''
  }

  return (
    <div className="flex h-full flex-col">
      {/* Header */}
      <div className="flex items-center justify-between border-b border-border p-4">
        <div className="flex items-center gap-2">
          <Bot className="h-5 w-5 text-primary" />
          <h1 className="font-semibold">AI 对话</h1>
        </div>
        <button
          onClick={handleClearChat}
          className="flex h-8 w-8 items-center justify-center rounded-lg hover:bg-muted transition-colors"
          title="清空对话"
        >
          <Trash2 className="h-4 w-4 text-muted-foreground" />
        </button>
      </div>

      {/* Quick Templates */}
      <div className="flex gap-2 overflow-x-auto p-3 border-b border-border no-scrollbar">
        {quickTemplates.map((template, index) => (
          <button
            key={index}
            onClick={() => handleTemplateClick(template.prompt)}
            className="flex-shrink-0 px-3 py-1.5 text-xs rounded-full bg-muted hover:bg-muted/80 transition-colors"
          >
            {template.name}
          </button>
        ))}
      </div>

      {/* Messages */}
      <div className="flex-1 overflow-y-auto p-4 space-y-4" role="list">
        {messages.map((message) => (
          <div
            key={message.id}
            role="article"
            className={`flex gap-3 ${
              message.role === "user" ? "flex-row-reverse" : ""
            }`}
          >
            <div
              className={`flex h-8 w-8 items-center justify-center rounded-full flex-shrink-0 ${
                message.role === "user"
                  ? "bg-primary text-primary-foreground"
                  : "bg-muted"
              }`}
            >
              {message.role === "user" ? (
                <User className="h-4 w-4" />
              ) : (
                <Bot className="h-4 w-4" />
              )}
            </div>
            <div
              className={`max-w-[85%] rounded-lg p-3 text-sm ${
                message.role === "user"
                  ? "bg-primary text-primary-foreground"
                  : message.type === "error"
                  ? "bg-red-50 text-red-800 border border-red-200"
                  : "bg-muted"
              }`}
            >
              {message.role === "assistant" && message.type !== "loading" ? (
                <ReactMarkdown 
                  remarkPlugins={[remarkGfm]}
                  className="prose prose-sm max-w-none"
                  components={{
                    a: ({node, ...props}) => (
                      <a {...props} target="_blank" rel="noopener noreferrer" className="text-primary hover:underline" />
                    ),
                    code: ({node, inline, ...props}) => (
                      inline ? 
                        <code {...props} className="bg-gray-200 px-1 py-0.5 rounded text-xs" /> :
                        <pre className="bg-gray-900 text-white p-2 rounded overflow-x-auto text-xs">
                          <code {...props} />
                        </pre>
                    )
                  }}
                >
                  {message.content}
                </ReactMarkdown>
              ) : (
                message.content
              )}
            </div>
          </div>
        ))}
        <div ref={messagesEndRef} />
      </div>

      {/* Input */}
      <div className="border-t border-border p-4">
        <div className="flex gap-2 mb-2">
          <input
            type="file"
            ref={fileInputRef}
            onChange={handleFileUpload}
            accept=".shp,.geojson,.tiff,.tif,.json"
            className="hidden"
          />
          <button
            onClick={() => fileInputRef.current?.click()}
            className="flex h-10 w-10 items-center justify-center rounded-lg border border-border hover:bg-muted transition-colors"
            title="上传GIS文件"
          >
            <Paperclip className="h-4 w-4" />
          </button>
          <button
            className="flex h-10 w-10 items-center justify-center rounded-lg border border-border hover:bg-muted transition-colors"
            title="语音输入"
          >
            <Mic className="h-4 w-4" />
          </button>
          <input
            type="text"
            value={input}
            onChange={(e) => setInput(e.target.value)}
            onKeyPress={handleKeyPress}
            placeholder="输入分析指令..."
            className="flex-1 rounded-lg border border-border px-3 text-sm focus:outline-none focus:ring-2 focus:ring-primary"
          />
          <button
            onClick={handleSend}
            disabled={!input.trim() || isLoading}
            className="flex h-10 w-10 items-center justify-center rounded-lg bg-primary text-primary-foreground hover:bg-primary/90 disabled:opacity-50 disabled:cursor-not-allowed transition-colors"
            title="发送消息"
          >
            <Send className="h-4 w-4" />
          </button>
        </div>
        <p className="text-xs text-muted-foreground text-center">
          支持 shp/geojson/tiff 格式文件上传，语音输入即将上线
        </p>
      </div>
    </div>
  )
}
