"use client"
import { useState, useRef, useEffect } from "react"
import { Send, Paperclip, Bot, User, Loader2, Upload, X } from "lucide-react"
import { streamChat } from "@/lib/api/chat"

interface Message {
  id: string
  role: "user" | "assistant"
  content: string
  timestamp: Date
  isThinking?: boolean
}

interface Attachment {
  id: string
  name: string
  size: number
  type: string
}

// Callback types for parent component communication
interface ChatPanelProps {
  onAnalysisRequest?: (message: string, attachments?: Attachment[]) => void
  incomingMessage?: string
  incomingResponse?: string
  onToolResult?: (toolName: string, result: any) => void
}

export function ChatPanel({ onAnalysisRequest, incomingMessage, incomingResponse, onToolResult }: ChatPanelProps) {
  const [messages, setMessages] = useState<Message[]>([
    {
      id: "1",
      role: "assistant",
      content: "你好！我是 WebGIS AI 助手，可以帮你进行空间数据分析和制图。请描述你想进行的分析，例如「分析北京市人口分布」或「找出周边5公里内的公园」",
      timestamp: new Date(),
    },
  ])
  const [input, setInput] = useState("")
  const [isLoading, setIsLoading] = useState(false)
  const [sessionId, setSessionId] = useState<string>()
  const [attachments, setAttachments] = useState<Attachment[]>([])
  const [showAttachments, setShowAttachments] = useState(false)
  const messageEndRef = useRef<HTMLDivElement>(null)
  const inputRef = useRef<HTMLInputElement>(null)

  // Handle incoming messages from parent
  useEffect(() => {
    if (incomingMessage) {
      handleSend(incomingMessage, incomingResponse)
    }
  }, [incomingMessage, incomingResponse])

  const scrollToBottom = () => {
    setTimeout(() => {
      messageEndRef.current?.scrollIntoView({ behavior: "smooth" })
    }, 100)
  }

  const handleFileSelect = (e: React.ChangeEvent<HTMLInputElement>) => {
    const files = Array.from(e.target.files || [])
    const newAttachments: Attachment[] = files.map(file => ({
      id: Date.now().toString() + Math.random(),
      name: file.name,
      size: file.size,
      type: file.type,
    }))
    setAttachments(prev => [...prev, ...newAttachments])
    setShowAttachments(true)
  }

  const removeAttachment = (id: string) => {
    setAttachments(prev => prev.filter(a => a.id !== id))
  }

  const handleSend = async (msg?: string, customResponse?: string) => {
    const messageText = msg || input.trim()
    if (!messageText || isLoading) return

    const userMessage: Message = {
      id: Date.now().toString(),
      role: "user",
      content: messageText,
      timestamp: new Date(),
      ...(msg && { isThinking: !!customResponse }),
    }

    setMessages(prev => [...prev, userMessage])
    if (!msg) {
      setInput("")
      setAttachments([])
    }
    
    if (customResponse) {
      // Pre-set response (for demo/testing)
      const assistantMessage: Message = {
        id: (Date.now() + 1).toString(),
        role: "assistant",
        content: customResponse,
        timestamp: new Date(),
      }
      setMessages(prev => [...prev, assistantMessage])
      onAnalysisRequest?.(messageText, attachments)
      scrollToBottom()
      return
    }

    setIsLoading(true)

    // Add thinking indicator
    const thinkingMessage: Message = {
      id: (Date.now() + 1).toString(),
      role: "assistant",
      content: "",
      timestamp: new Date(),
      isThinking: true,
    }
    setMessages(prev => [...prev, thinkingMessage])
    scrollToBottom()

    try {
      let assistantContent = ""

      for await (const event of streamChat(messageText, sessionId)) {
        const { event: eventType, data } = event

        if (eventType === "session" && data?.session_id) {
          setSessionId(data.session_id)
        } else if (eventType === "tool_call") {
          const toolName = typeof data === "object" ? data.name || data.tool : String(data)
          assistantContent += `\n🔧 正在调用工具: ${toolName}\n`
          setMessages(prev => prev.map(msg =>
            msg.id === thinkingMessage.id
              ? { ...msg, content: assistantContent, isThinking: false }
              : msg
          ))
          scrollToBottom()
        } else if (eventType === "tool_result") {
          // Notify parent for GeoJSON rendering
          if (onToolResult && typeof data === "object" && data?.name) {
            onToolResult(data.name, data.result || data)
          }
          const result = typeof data === "object" ? JSON.stringify(data, null, 2) : String(data)
          assistantContent += `📋 工具结果: ${result}\n`
          setMessages(prev => prev.map(msg =>
            msg.id === thinkingMessage.id
              ? { ...msg, content: assistantContent, isThinking: false }
              : msg
          ))
          scrollToBottom()
        } else if (eventType === "message" || eventType === "content" || eventType === "token") {
          const chunk = typeof data === "object" ? (data.content || data.text || data.message || "") : String(data)
          assistantContent += chunk
          setMessages(prev => prev.map(msg =>
            msg.id === thinkingMessage.id
              ? { ...msg, content: assistantContent, isThinking: false }
              : msg
          ))
          scrollToBottom()
        } else if (eventType === "done" || eventType === "end") {
          break
        }
      }

      // Ensure we un-set thinking state
      setMessages(prev => prev.map(msg =>
        msg.id === thinkingMessage.id
          ? { ...msg, isThinking: false }
          : msg
      ))

      // Notify parent component
      onAnalysisRequest?.(messageText, attachments)

    } catch (error) {
      setMessages(prev => prev.map(msg =>
        msg.id === thinkingMessage.id
          ? { ...msg, content: "抱歉，请求失败了，请重试", isThinking: false }
          : msg
      ))
    } finally {
      setIsLoading(false)
      inputRef.current?.focus()
    }
  }

  const handleKeyPress = (e: React.KeyboardEvent) => {
    if (e.key === "Enter" && !e.shiftKey) {
      e.preventDefault()
      handleSend()
    }
  }

  return (
    <div className="flex h-full flex-col">
      {/* Header */}
      <div className="flex items-center gap-2 border-b border-border p-4">
        <Bot className="h-5 w-5 text-primary" />
        <h1 className="font-semibold">AI 对话</h1>
      </div>

      {/* Messages */}
      <div className="flex-1 overflow-y-auto p-4 space-y-4">
        {messages.map((message) => (
          <div
            key={message.id}
            className={`flex gap-3 ${
              message.role === "user" ? "flex-row-reverse" : ""
            }`}
          >
            <div
              className={`flex h-8 w-8 shrink-0 items-center justify-center rounded-full ${
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
                  : "bg-muted"
              }`}
            >
              {message.isThinking ? (
                <div className="flex items-center gap-2">
                  <Loader2 className="h-4 w-4 animate-spin" />
                  <span className="text-xs">思考中...</span>
                </div>
              ) : (
                <div className="whitespace-pre-wrap">{message.content}</div>
              )}
            </div>
          </div>
        ))}
        <div ref={messageEndRef} />
      </div>

      {/* Attachments Preview */}
      {attachments.length > 0 && (
        <div className="px-4 py-2 border-t border-border flex flex-wrap gap-2">
          {attachments.map((att) => (
            <div key={att.id} className="flex items-center gap-1 bg-muted rounded px-2 py-1 text-xs">
              <Upload className="h-3 w-3" />
              <span className="max-w-24 truncate">{att.name}</span>
              <button onClick={() => removeAttachment(att.id)} className="hover:text-destructive">
                <X className="h-3 w-3" />
              </button>
            </div>
          ))}
        </div>
      )}

      {/* Input */}
      <div className="border-t border-border p-4">
        <div className="flex gap-2">
          <label className="flex h-10 w-10 cursor-pointer items-center justify-center rounded-lg border border-border hover:bg-muted transition-colors">
            <input type="file" multiple className="hidden" onChange={handleFileSelect} />
            <Paperclip className="h-4 w-4" />
          </label>
          <input
            ref={inputRef}
            type="text"
            value={input}
            onChange={(e) => setInput(e.target.value)}
            onKeyPress={handleKeyPress}
            placeholder="输入分析指令..."
            disabled={isLoading}
            className="flex-1 rounded-lg border border-border px-3 text-sm focus:outline-none focus:ring-2 focus:ring-primary disabled:opacity-50"
          />
          <button
            onClick={() => handleSend()}
            disabled={!input.trim() || isLoading}
            className="flex h-10 w-10 shrink-0 items-center justify-center rounded-lg bg-primary text-primary-foreground hover:bg-primary/90 disabled:opacity-50 disabled:cursor-not-allowed transition-colors"
          >
            {isLoading ? (
              <Loader2 className="h-4 w-4 animate-spin" />
            ) : (
              <Send className="h-4 w-4" />
            )}
          </button>
        </div>
      </div>
    </div>
  )
}
