"use client"
import { useState, useRef, useEffect, useCallback } from "react"
import { Send, Paperclip, Bot, User, Loader2, Upload, X, Check, AlertCircle, History, ArrowLeft, Trash2 } from "lucide-react"
import { streamChat, SSEEventType, getSessionList, getSessionDetail, deleteSession } from "@/lib/api/chat"
import { ChatSession } from "@/lib/types/chat"
import { useTask } from "@/lib/contexts/task-context"
import type { ToolResult } from "@/lib/types"
import { TaskProgress } from "@/components/chat/task-progress"
import { UploadZone } from "@/components/upload/upload-zone"
import { UploadProgress } from "@/components/upload/upload-progress"
import type { UploadResponse } from "@/lib/api/upload"
import ReactMarkdown from "react-markdown"
import remarkGfm from "remark-gfm"
import { ChartRenderer, ChartData, adaptChartData } from "@/components/chat/chart-renderer"
import { MapActionRenderer } from "@/components/chat/map-action-renderer"

interface Message {
  id: string
  role: "user" | "assistant"
  content: string
  timestamp: Date
  isThinking?: boolean
  charts?: ChartData[]
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
  onToolResult?: (toolName: string, result: ToolResult, sessionId?: string) => void
  onUploadSuccess?: (result: UploadResponse) => void
}

export function ChatPanel({ onAnalysisRequest, incomingMessage, incomingResponse, onToolResult, onUploadSuccess }: ChatPanelProps) {
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
  const [currentStep, setCurrentStep] = useState<SSEEventType | 'error' | null>(null)
  const messageEndRef = useRef<HTMLDivElement>(null)
  const inputRef = useRef<HTMLInputElement>(null)

  // History panel state
  type ViewMode = 'chat' | 'history' | 'history-detail'
  const [viewMode, setViewMode] = useState<ViewMode>('chat')
  const [sessions, setSessions] = useState<ChatSession[]>([])
  const [historyDetail, setHistoryDetail] = useState<ChatSession | null>(null)
  const [sessionsLoading, setSessionsLoading] = useState(false)
  const [deleteConfirm, setDeleteConfirm] = useState<string | null>(null)

  // Upload state
  const [uploadedFiles, setUploadedFiles] = useState<UploadResponse[]>([])
  const [showUploadZone, setShowUploadZone] = useState(false)

  const handleUploadSuccess = useCallback((result: UploadResponse) => {
    setUploadedFiles(prev => [result, ...prev])
    onUploadSuccess?.(result)
  }, [onUploadSuccess])

  const handleRemoveUpload = useCallback((id: number) => {
    setUploadedFiles(prev => prev.filter(u => u.id !== id))
  }, [])

  // Task context
  const {
    currentTask,
    handleTaskStart,
    handleStepStart,
    handleStepResult,
    handleStepError,
    handleTaskComplete,
    handleTaskError,
    handleTaskCancelled,
    clearTask,
  } = useTask()

  // Reasoning step order and labels
  const stepOrder: SSEEventType[] = ['thinking', 'planning', 'acting', 'observing', 'done']
  const stepLabels: Partial<Record<SSEEventType | 'error', { label: string; icon: React.ReactNode }>> = {
    thinking: { label: '思考中', icon: <Loader2 className="h-3 w-3 animate-spin" /> },
    planning: { label: '规划方案', icon: <Loader2 className="h-3 w-3 animate-spin" /> },
    acting: { label: '执行操作', icon: <Loader2 className="h-3 w-3 animate-spin" /> },
    observing: { label: '分析结果', icon: <Loader2 className="h-3 w-3 animate-spin" /> },
    done: { label: '完成', icon: <Check className="h-3 w-3 text-green-500" /> },
    tool_error: { label: '执行出错', icon: <AlertCircle className="h-3 w-3 text-red-500" /> },
    error: { label: '执行出错', icon: <AlertCircle className="h-3 w-3 text-red-500" /> },
    message: { label: '回复消息', icon: <Check className="h-3 w-3 text-green-500" /> }
  }

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
      setCurrentStep('thinking')

      for await (const event of streamChat(messageText, sessionId)) {
        const { event: eventType, data } = event

        // 步骤状态更新
        if (stepOrder.includes(eventType as SSEEventType) || eventType === 'tool_error') {
          setCurrentStep(eventType)
        }

        if (eventType === "session" && data?.session_id) {
          setSessionId(data.session_id)
        } 
        
        // 核心任务流处理 (由 TaskProgress 统一渲染)
        else if (eventType === "task_start" && data?.task_id) {
          handleTaskStart(data.task_id)
        } else if (eventType === "step_start" && data?.task_id) {
          handleStepStart(data.task_id, data.step_id, data.step_index, data.tool)
        } else if (eventType === "step_result" && data?.task_id) {
          handleStepResult(data.task_id, data.step_id, data.tool, data.result, data.has_geojson)
          // 如果结果中包含引用 ID (geojson_ref)，则可能数据已被脱敏，需要前端后续拉取
          if (data.has_geojson && onToolResult) {
            const toolResult = { ...data.result, geojson_ref: data.geojson_ref }
            const effectiveSessionId = data.session_id || sessionId
            onToolResult(data.tool, toolResult, effectiveSessionId)
          }
        } else if (eventType === "step_error" && data?.task_id) {
          handleStepError(data.task_id, data.step_id, data.error)
        } else if (eventType === "task_complete" && data?.task_id) {
          handleTaskComplete(data.task_id, data.step_count, data.summary)
          // 如果 AI 还没说够，可以用任务总结补位
          if (assistantContent.length < 10 && data.summary) {
            assistantContent = data.summary
          }
        } 
        
        // 传统工具事件 (主要用于提取图表等富媒体内容，不再追加文本日志)
        else if (eventType === "tool_result") {
          const toolResult = typeof data === "object" ? (data.result || data) : data
          // 检测图表数据
          if (toolResult?.chart) {
            const validatedChart = adaptChartData(toolResult.chart)
            if (validatedChart) {
              setMessages(prev => prev.map(msg =>
                msg.id === thinkingMessage.id
                  ? { ...msg, charts: [...(msg.charts || []), validatedChart], isThinking: false }
                  : msg
              ))
            }
          }
        } 
        
        // 内容流更新
        else if (eventType === "message" || eventType === "content" || eventType === "token") {
          const chunk = typeof data === "object" ? (data.content || data.text || data.message || "") : String(data)
          assistantContent += chunk
          setMessages(prev => prev.map(msg =>
            msg.id === thinkingMessage.id
              ? { ...msg, content: assistantContent, isThinking: false }
              : msg
          ))
          scrollToBottom()
        } else if (eventType === "done" || eventType === "end") {
          setCurrentStep('done')
          break
        } else if (eventType === "task_error" || eventType === "tool_error") {
          const errorMsg = typeof data === "object" ? (data.message || data.error || "未知错误") : String(data)
          // 错误信息还是需要显示在气泡中的
          if (!assistantContent.includes(errorMsg)) {
            assistantContent += `\n\n> ⚠️ **观测异常**: ${errorMsg}\n`
          }
          setMessages(prev => prev.map(msg =>
            msg.id === thinkingMessage.id
              ? { ...msg, content: assistantContent, isThinking: false }
              : msg
          ))
          scrollToBottom()
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
      setCurrentStep('error')
      setMessages(prev => prev.map(msg =>
        msg.id === thinkingMessage.id
          ? { ...msg, content: "抱歉，请求失败了，请重试", isThinking: false }
          : msg
      ))
    } finally {
      // Keep done/error state visible for 1 second before clearing
      setTimeout(() => {
        setCurrentStep(null)
        clearTask()
      }, 1000)
      setIsLoading(false)
      inputRef.current?.focus()
    }
  }

  const loadSessions = async () => {
    setSessionsLoading(true)
    try {
      const data = await getSessionList()
      setSessions(data.sessions || [])
    } catch (e) {
      console.error("Failed to load sessions", e)
    } finally {
      setSessionsLoading(false)
    }
  }

  const openHistory = () => {
    setViewMode('history')
    loadSessions()
  }

  const openHistoryDetail = async (sessionId: string) => {
    try {
      const detail = await getSessionDetail(sessionId)
      setHistoryDetail(detail)
      setViewMode('history-detail')
    } catch (e) {
      console.error("Failed to load session detail", e)
    }
  }

  const handleDeleteSession = async (sessionId: string) => {
    try {
      await deleteSession(sessionId)
      setSessions(prev => prev.filter(s => s.id !== sessionId))
      if (historyDetail?.id === sessionId) {
        setViewMode('history')
        setHistoryDetail(null)
      }
    } catch (e) {
      console.error("Failed to delete session", e)
    } finally {
      setDeleteConfirm(null)
    }
  }

  const handleKeyPress = (e: React.KeyboardEvent) => {
    if (e.key === "Enter" && !e.shiftKey) {
      e.preventDefault()
      handleSend()
    }
  }

  return (
    <div className="flex h-full flex-col bg-card border-r border-border">
      {/* Header - 探险家徽章风格 */}
      <div className="flex items-center gap-3 border-b border-border p-4 bg-background-secondary/50">
        <div className="relative">
          <Bot className="h-5 w-5 text-primary" />
          <div className="absolute -inset-1 bg-primary/20 rounded-full blur-sm" />
        </div>
        <div className="flex-1">
          <h1 className="font-semibold text-foreground text-lg tracking-wide">探索者日志</h1>
          <p className="text-xs text-muted-foreground">AI 地理分析助手</p>
        </div>
        <button
          onClick={viewMode === 'chat' ? openHistory : () => setViewMode('chat')}
          className="flex h-8 w-8 items-center justify-center rounded-lg hover:bg-card transition-colors"
          title={viewMode === 'chat' ? "历史会话" : "返回对话"}
        >
          {viewMode === 'chat'
            ? <History className="h-4 w-4 text-muted-foreground" />
            : <ArrowLeft className="h-4 w-4 text-muted-foreground" />
          }
        </button>
      </div>

      {/* History List View */}
      {viewMode === 'history' && (
        <div className="flex-1 overflow-y-auto">
          <div className="p-3">
            <p className="text-xs text-muted-foreground px-2 mb-2">最近 {sessions.length} 条会话</p>
            {sessionsLoading && (
              <div className="flex justify-center py-8">
                <Loader2 className="h-5 w-5 animate-spin text-muted-foreground" />
              </div>
            )}
            {!sessionsLoading && sessions.length === 0 && (
              <p className="text-sm text-muted-foreground text-center py-8">暂无历史会话</p>
            )}
            <ul className="space-y-1">
              {sessions.map(s => (
                <li key={s.id} className="group flex items-center gap-2 px-3 py-2.5 rounded-lg hover:bg-card transition-colors cursor-pointer"
                    onClick={() => openHistoryDetail(s.id)}>
                  <div className="flex-1 min-w-0">
                    <p className="truncate text-sm font-medium text-foreground">{s.title || '新对话'}</p>
                    <p className="text-xs text-muted-foreground">
                      {new Date(s.updatedAt).toLocaleDateString('zh-CN')}
                    </p>
                  </div>
                  {deleteConfirm === s.id ? (
                    <div className="flex gap-1" onClick={e => e.stopPropagation()}>
                      <button onClick={() => handleDeleteSession(s.id)}
                        className="text-xs px-2 py-1 bg-red-500 text-white rounded">确认</button>
                      <button onClick={() => setDeleteConfirm(null)}
                        className="text-xs px-2 py-1 bg-muted rounded">取消</button>
                    </div>
                  ) : (
                    <button
                      onClick={e => { e.stopPropagation(); setDeleteConfirm(s.id) }}
                      className="opacity-0 group-hover:opacity-100 p-1 hover:bg-red-100 hover:text-red-600 rounded transition-opacity"
                    >
                      <Trash2 className="h-3.5 w-3.5" />
                    </button>
                  )}
                </li>
              ))}
            </ul>
          </div>
        </div>
      )}

      {/* History Detail View (read-only) */}
      {viewMode === 'history-detail' && historyDetail && (
        <div className="flex flex-col flex-1 overflow-hidden">
          <div className="px-4 py-2 bg-amber-50 border-b border-amber-200 text-xs text-amber-700 flex items-center justify-between">
            <span>只读模式 — {historyDetail.title}</span>
            <button onClick={() => { setViewMode('history'); setHistoryDetail(null) }}
              className="underline hover:no-underline">返回列表</button>
          </div>
          <div className="flex-1 overflow-y-auto p-4 space-y-4">
            {historyDetail.messages.map((msg, i) => (
              <div key={i} className={`flex gap-3 ${msg.role === 'user' ? 'flex-row-reverse' : 'flex-row'}`}>
                <div className={`flex h-7 w-7 shrink-0 items-center justify-center rounded-full
                  ${msg.role === 'user' ? 'bg-primary text-primary-foreground' : 'bg-muted'}`}>
                  {msg.role === 'user' ? <User className="h-3.5 w-3.5" /> : <Bot className="h-3.5 w-3.5" />}
                </div>
                <div className={`max-w-[85%] rounded-xl px-3 py-2 text-sm
                  ${msg.role === 'user' ? 'bg-primary text-primary-foreground' : 'bg-card border border-border'}`}>
                  <ReactMarkdown remarkPlugins={[remarkGfm]}>{msg.content}</ReactMarkdown>
                </div>
              </div>
            ))}
          </div>
        </div>
      )}

      {/* Active chat: task progress, messages, attachments, input */}
      {viewMode === 'chat' && (
        <>
          {/* Task Progress Card (inline, replaces old reasoning bar) - 探索日志风格 */}
          {currentTask && (
            <div className="border-b border-border px-4 py-3 bg-background-secondary/30">
              <TaskProgress task={currentTask} />
            </div>
          )}

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
                  className={`flex h-9 w-9 shrink-0 items-center justify-center rounded-full border-2 ${
                    message.role === "user"
                      ? "bg-primary border-primary text-primary-foreground"
                      : "bg-card border-border"
                  }`}
                >
                  {message.role === "user" ? (
                    <User className="h-4 w-4" />
                  ) : (
                    <Bot className="h-4 w-4" />
                  )}
                </div>
                <div
                  className={`max-w-[90%] rounded-2xl p-4 text-sm border-2 shadow-xl transition-all hover:shadow-2xl ${
                    message.role === "user"
                      ? "bg-primary border-primary/20 text-primary-foreground shadow-primary/10"
                      : "bg-card/80 border-border/40 backdrop-blur-sm shadow-black/20"
                  }`}
                >
                  {message.isThinking ? (
                    <div className="flex items-center gap-3 py-1">
                      <div className="relative">
                        <Loader2 className="h-4 w-4 animate-spin text-primary" />
                        <div className="absolute inset-0 bg-primary/30 rounded-full blur animate-pulse" />
                      </div>
                      <span className="text-xs font-bold tracking-widest uppercase text-muted-foreground">解构星图中...</span>
                    </div>
                  ) : (
                    <div className="prose prose-sm max-w-none dark:prose-invert 
                                    prose-p:leading-relaxed prose-p:my-2 
                                    prose-headings:text-primary prose-headings:font-bold prose-headings:tracking-tight
                                    prose-li:marker:text-primary/60
                                    prose-code:bg-muted/50 prose-code:px-1.5 prose-code:py-0.5 prose-code:rounded prose-code:before:content-none prose-code:after:content-none
                                    break-words">
                      <ReactMarkdown
                        remarkPlugins={[remarkGfm]}
                        components={{
                          img: ({ src, alt }) => {
                            if (!src || src.includes('/api/map/view') || src.includes('localhost')) return null
                            return (
                              <div className="my-3 rounded-lg overflow-hidden border border-border shadow-soft">
                                <img src={src} alt={alt || ''} className="max-w-full" />
                              </div>
                            )
                          },
                          // 优化标题
                          h1: ({children}) => <h1 className="text-xl border-b border-primary/20 pb-1 mb-4">{children}</h1>,
                          h2: ({children}) => <h2 className="text-lg border-l-4 border-primary/40 pl-3 my-4">{children}</h2>,
                        }}
                      >
                        {message.content}
                      </ReactMarkdown>
                      {message.role === 'assistant' && (
                        <div className="mt-4 pt-4 border-t border-border/20">
                           <MapActionRenderer content={message.content} />
                        </div>
                      )}
                      {message.charts?.map((chart, idx) => (
                        <div key={`chart-${message.id}-${idx}`} className="mt-4 p-4 bg-background-secondary/50 rounded-xl border border-border/30 shadow-inner">
                          <ChartRenderer chart={chart} />
                        </div>
                      ))}
                    </div>
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

          {/* Input - 复古信笺风格 */}
          <div className="border-t border-border p-4 bg-background-secondary/30">
            {/* Uploaded files list */}
            {uploadedFiles.length > 0 && (
              <div className="mb-2">
                <UploadProgress uploads={uploadedFiles} onRemove={handleRemoveUpload} />
              </div>
            )}
            {/* Upload zone (toggle) */}
            {showUploadZone && (
              <div className="mb-2">
                <UploadZone
                  sessionId={sessionId}
                  onUploadSuccess={handleUploadSuccess}
                  compact={false}
                />
              </div>
            )}
            <div className="flex gap-3">
              <button
                onClick={() => setShowUploadZone(prev => !prev)}
                className="flex h-11 w-11 shrink-0 items-center justify-center rounded-lg border border-border hover:bg-card hover:border-primary/50 transition-all group"
                title="上传 GIS 数据"
              >
                <Upload className="h-4 w-4 text-muted-foreground group-hover:text-primary transition-colors" />
              </button>
              <input
                ref={inputRef}
                type="text"
                value={input}
                onChange={(e) => setInput(e.target.value)}
                onKeyPress={handleKeyPress}
                placeholder="描述您想进行的地理分析..."
                disabled={isLoading}
                className="flex-1 rounded-lg border border-border bg-card px-4 text-sm text-foreground placeholder:text-muted-foreground focus:outline-none focus:ring-2 focus:ring-primary focus:border-primary/50 transition-all"
              />
              <button
                onClick={() => handleSend()}
                disabled={!input.trim() || isLoading}
                className="flex h-11 w-11 shrink-0 items-center justify-center rounded-lg bg-primary text-primary-foreground hover:bg-primary-dark disabled:opacity-50 disabled:cursor-not-allowed transition-all border border-primary/30 hover:border-primary"
              >
                {isLoading ? (
                  <Loader2 className="h-4 w-4 animate-spin" />
                ) : (
                  <Send className="h-4 w-4" />
                )}
              </button>
            </div>
          </div>
        </>
      )}
    </div>
  )
}
