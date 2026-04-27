import { describe, it, expect, vi, beforeEach, afterEach } from "vitest"
import { useToastStore } from "./toast"

describe("toast store", () => {
  beforeEach(() => {
    useToastStore.setState({ toasts: [] })
    vi.useFakeTimers()
  })

  afterEach(() => {
    vi.useRealTimers()
  })

  it("adds a toast", () => {
    useToastStore.getState().addToast("hello world")
    const { toasts } = useToastStore.getState()
    expect(toasts).toHaveLength(1)
    expect(toasts[0].message).toBe("hello world")
    expect(toasts[0].type).toBe("info")
    expect(toasts[0].id).toMatch(/^toast-\d+$/)
  })

  it("removes a toast by id", () => {
    useToastStore.getState().addToast("to remove")
    const { id } = useToastStore.getState().toasts[0]
    useToastStore.getState().removeToast(id)
    expect(useToastStore.getState().toasts).toHaveLength(0)
  })

  it("deduplicates same message within 2s", () => {
    useToastStore.getState().addToast("dup me")
    useToastStore.getState().addToast("dup me")
    expect(useToastStore.getState().toasts).toHaveLength(1)
  })

  it("allows different messages", () => {
    useToastStore.getState().addToast("first")
    useToastStore.getState().addToast("second")
    expect(useToastStore.getState().toasts).toHaveLength(2)
    expect(useToastStore.getState().toasts.map((t) => t.message)).toEqual([
      "first",
      "second",
    ])
  })

  it("auto-removes after duration", () => {
    useToastStore.getState().addToast("auto dismiss", "info", 3000)
    expect(useToastStore.getState().toasts).toHaveLength(1)

    vi.advanceTimersByTime(2999)
    expect(useToastStore.getState().toasts).toHaveLength(1)

    vi.advanceTimersByTime(1)
    expect(useToastStore.getState().toasts).toHaveLength(0)
  })
})
