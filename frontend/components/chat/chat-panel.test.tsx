import { render, screen, fireEvent, waitFor } from '@testing-library/react'
import { ChatPanel } from './chat-panel'
import '@testing-library/jest-dom'

// Mock fetch API
global.fetch = jest.fn()

describe('ChatPanel Component', () => {
  beforeEach(() => {
    jest.clearAllMocks()
  })

  test('renders initial welcome message', () => {
    render(<ChatPanel />)
    expect(screen.getByText('你好！我是 WebGIS AI 助手，可以帮助你进行空间数据分析和制图。请告诉我你想分析什么？')).toBeInTheDocument()
  })

  test('sends message when clicking send button', async () => {
    render(<ChatPanel />)
    const input = screen.getByPlaceholderText('输入分析指令...')
    const sendButton = screen.getByTitle('发送消息')

    fireEvent.change(input, { target: { value: '查询北京市的人口分布' } })
    fireEvent.click(sendButton)

    expect(screen.getByText('查询北京市的人口分布')).toBeInTheDocument()
    expect(input).toHaveValue('')
  })

  test('sends message when pressing Enter key', async () => {
    render(<ChatPanel />)
    const input = screen.getByPlaceholderText('输入分析指令...')

    fireEvent.change(input, { target: { value: '查询上海市的房价分布' } })
    fireEvent.keyPress(input, { key: 'Enter', shiftKey: false })

    expect(screen.getByText('查询上海市的房价分布')).toBeInTheDocument()
    expect(input).toHaveValue('')
  })

  test('does not send empty message', async () => {
    render(<ChatPanel />)
    const input = screen.getByPlaceholderText('输入分析指令...')
    const sendButton = screen.getByTitle('发送消息')

    fireEvent.change(input, { target: { value: '   ' } })
    expect(sendButton).toBeDisabled()

    fireEvent.keyPress(input, { key: 'Enter', shiftKey: false })
    const messages = screen.getAllByRole('article')
    expect(messages).toHaveLength(1) // Only initial welcome message
  })

  test('shows loading state when waiting for response', async () => {
    (global.fetch as jest.Mock).mockImplementationOnce(() =>
      Promise.resolve({
        ok: true,
        json: () => Promise.resolve({ content: '测试回复' }),
      })
    )

    render(<ChatPanel />)
    const input = screen.getByPlaceholderText('输入分析指令...')
    const sendButton = screen.getByTitle('发送消息')

    fireEvent.change(input, { target: { value: '测试查询' } })
    fireEvent.click(sendButton)

    expect(screen.getByText('正在思考中...')).toBeInTheDocument()

    await waitFor(() => {
      expect(screen.getByText('测试回复')).toBeInTheDocument()
    })
  })

  test('shows error message when API call fails', async () => {
    (global.fetch as jest.Mock).mockImplementationOnce(() =>
      Promise.reject(new Error('API Error'))
    )

    render(<ChatPanel />)
    const input = screen.getByPlaceholderText('输入分析指令...')
    const sendButton = screen.getByTitle('发送消息')

    fireEvent.change(input, { target: { value: '测试查询' } })
    fireEvent.click(sendButton)

    await waitFor(() => {
      expect(screen.getByText('抱歉，请求失败，请稍后重试。')).toBeInTheDocument()
    })
  })

  test('renders markdown content in assistant messages', async () => {
    (global.fetch as jest.Mock).mockImplementationOnce(() =>
      Promise.resolve({
        ok: true,
        json: () => Promise.resolve({ content: '**加粗文本**\n- 列表项1\n- 列表项2\n[链接](https://example.com)' }),
      })
    )

    render(<ChatPanel />)
    const input = screen.getByPlaceholderText('输入分析指令...')
    const sendButton = screen.getByTitle('发送消息')

    fireEvent.change(input, { target: { value: '测试markdown' } })
    fireEvent.click(sendButton)

    await waitFor(() => {
      expect(screen.getByText('加粗文本')).toBeInTheDocument()
      expect(screen.getByText('列表项1')).toBeInTheDocument()
      expect(screen.getByText('链接')).toBeInTheDocument()
    })
  })

  test('clears all messages when clicking clear button', async () => {
    render(<ChatPanel />)
    const input = screen.getByPlaceholderText('输入分析指令...')
    const sendButton = screen.getByTitle('发送消息')
    const clearButton = screen.getByTitle('清空对话')

    fireEvent.change(input, { target: { value: '测试消息' } })
    fireEvent.click(sendButton)

    await waitFor(() => {
      const messages = screen.getAllByRole('article')
      expect(messages).toHaveLength(3) // Welcome + user + assistant
    })

    fireEvent.click(clearButton)
    const messagesAfterClear = screen.getAllByRole('article')
    expect(messagesAfterClear).toHaveLength(1) // Only welcome message remains
  })

  test('sends quick template when clicking template button', async () => {
    render(<ChatPanel />)
    const bufferTemplate = screen.getByText('缓冲区分析')

    fireEvent.click(bufferTemplate)

    const input = screen.getByPlaceholderText('输入分析指令...')
    expect(input).toHaveValue('请帮我做缓冲区分析')
  })
})
