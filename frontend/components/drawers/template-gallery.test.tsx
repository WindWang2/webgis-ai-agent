import { describe, it, expect, vi, beforeEach } from "vitest";
import { render, screen, fireEvent, waitFor } from "@testing-library/react";
import React from "react";
import { TemplateGallery } from "./template-gallery";
import { useHudStore } from "@/lib/store/useHudStore";

// The gallery emits commands via useMapAction(selector).dispatchAction — mock the
// context so tests can assert the twin-seam dispatch without a MapActionProvider.
// The component calls useMapAction with a selector, so the mock must honor it.
const { mockDispatchAction } = vi.hoisted(() => ({ mockDispatchAction: vi.fn() }));
vi.mock("@/lib/contexts/map-action-context", () => ({
  useMapAction: (selector?: any) => {
    const store = { dispatchAction: mockDispatchAction };
    return selector ? selector(store) : store;
  },
}));

describe("TemplateGallery Component", () => {
  const defaultProps = {
    open: true,
    onClose: vi.fn(),
    onApplyTemplate: vi.fn(),
  };

  beforeEach(() => {
    vi.clearAllMocks();
    mockDispatchAction.mockReset();
  });

  it("renders 4 kind tabs (底图模板, 符号化, 专题图, 版式布局) and search input", () => {
    render(<TemplateGallery {...defaultProps} />);

    expect(screen.getByRole("button", { name: /底图模板/i })).toBeDefined();
    expect(screen.getByRole("button", { name: /符号化/i })).toBeDefined();
    expect(screen.getByRole("button", { name: /专题图/i })).toBeDefined();
    expect(screen.getByRole("button", { name: /版式布局/i })).toBeDefined();
    expect(screen.getByPlaceholderText(/搜索模板/i)).toBeDefined();
  });

  it("renders source filter tabs (全部, 内置, 我的)", () => {
    render(<TemplateGallery {...defaultProps} />);

    expect(screen.getByText("全部")).toBeDefined();
    expect(screen.getAllByText("内置").length).toBeGreaterThan(0);
    expect(screen.getAllByText("我的").length).toBeGreaterThan(0);
  });

  it("basemap apply dispatches BASE_LAYER_CHANGE command (twin seam)", async () => {
    render(<TemplateGallery {...defaultProps} />);

    // Click on basemap tab
    const basemapTab = screen.getByRole("button", { name: /底图模板/i });
    fireEvent.click(basemapTab);

    // Look for apply button
    const applyButtons = screen.getAllByText(/套用/i);
    expect(applyButtons.length).toBeGreaterThan(0);

    fireEvent.click(applyButtons[0]);

    expect(defaultProps.onApplyTemplate).toHaveBeenCalled();
    // Gallery must emit the same command backend apply_template emits for basemap
    expect(mockDispatchAction).toHaveBeenCalledWith(
      expect.objectContaining({ command: "BASE_LAYER_CHANGE" })
    );
  });

  it("symbology apply without selected layer shows prompt", async () => {
    // Ensure no layer selected
    useHudStore.setState({ selectedLayerId: null, layers: [] });

    render(<TemplateGallery {...defaultProps} />);

    // Click symbology tab
    fireEvent.click(screen.getByRole("button", { name: /符号化/i }));

    const applyButtons = screen.getAllByText(/套用/i);
    expect(applyButtons.length).toBeGreaterThan(0);
    fireEvent.click(applyButtons[0]);

    // Prompt MUST appear (previously this was a no-op `if` guard that passed vacuously)
    await waitFor(() => {
      expect(screen.getByText(/请先选择图层/i)).toBeDefined();
    });
    // No command should have been dispatched (apply aborted)
    expect(mockDispatchAction).not.toHaveBeenCalled();
  });

  it("save-as template button opens save modal", async () => {
    render(<TemplateGallery {...defaultProps} />);

    const saveAsBtn = screen.getByText(/另存为模板/i);
    fireEvent.click(saveAsBtn);

    await waitFor(() => {
      expect(screen.getByText(/保存为新模板/i)).toBeDefined();
    });
  });
});
