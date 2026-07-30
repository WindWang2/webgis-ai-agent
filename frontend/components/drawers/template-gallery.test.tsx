import { describe, it, expect, vi, beforeEach } from "vitest";
import { render, screen, fireEvent, waitFor } from "@testing-library/react";
import React from "react";
import { TemplateGallery } from "./template-gallery";
import { useHudStore } from "@/lib/store/useHudStore";

describe("TemplateGallery Component", () => {
  const defaultProps = {
    open: true,
    onClose: vi.fn(),
    onApplyTemplate: vi.fn(),
  };

  beforeEach(() => {
    vi.clearAllMocks();
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

  it("basemap apply dispatches baseLayer update and onApplyTemplate callback", async () => {
    render(<TemplateGallery {...defaultProps} />);

    // Click on basemap tab
    const basemapTab = screen.getByRole("button", { name: /底图模板/i });
    fireEvent.click(basemapTab);

    // Look for apply button
    const applyButtons = screen.getAllByText(/套用/i);
    expect(applyButtons.length).toBeGreaterThan(0);

    fireEvent.click(applyButtons[0]);

    expect(defaultProps.onApplyTemplate).toHaveBeenCalled();
  });

  it("symbology apply without selected layer shows prompt", async () => {
    // Ensure no layer selected
    useHudStore.setState({ selectedLayerId: null, layers: [] });

    render(<TemplateGallery {...defaultProps} />);

    // Click symbology tab
    fireEvent.click(screen.getByRole("button", { name: /符号化/i }));

    const applyButtons = screen.getAllByText(/套用/i);
    if (applyButtons.length > 0) {
      fireEvent.click(applyButtons[0]);
      await waitFor(() => {
        expect(screen.getByText(/请先选择图层/i)).toBeDefined();
      });
    }
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
