import React from "react";
import { render, screen, fireEvent } from "@testing-library/react";
import { describe, it, expect, vi } from "vitest";
import { TelemetryStatusCard, TelemetryDigest } from "./telemetry-status-card";

describe("TelemetryStatusCard", () => {
  const sampleDigest: TelemetryDigest = {
    success: true,
    tool_metrics: {
      webgis_layer_upsert: {
        count: 5,
        total_ms: 150,
        max_ms: 50,
        hit_count: 0,
        error_count: 0,
      },
    },
    spatial_cache: {
      hits: 10,
      misses: 2,
      size: 5,
      maxsize: 128,
    },
    harness_enabled: true,
    harness_metrics: {
      rates: {
        ToolChoiceAccuracy: 95.0,
        MapSpecValidity: 100.0,
      },
      counts: {
        ToolCallsCount: 42,
        ExceptionsCount: 3,
      },
    },
  };

  it("renders empty state when no digest provided", () => {
    render(<TelemetryStatusCard digest={null} />);
    expect(screen.getByText("生产端性能与评估遥测")).toBeDefined();
    expect(screen.getByText("暂无遥测数据，请点击刷新加载。")).toBeDefined();
  });

  it("renders metrics, spatial cache hit ratio, and harness rates with %", () => {
    render(<TelemetryStatusCard digest={sampleDigest} />);
    expect(screen.getByText("生产端性能与评估遥测")).toBeDefined();
    expect(screen.getByText("83%")).toBeDefined(); // 10 hits / 12 total = 83%
    expect(screen.getByText("webgis_layer_upsert")).toBeDefined();
    expect(screen.getByText("30 ms")).toBeDefined(); // 150ms / 5 = 30ms
    expect(screen.getByText("ToolChoiceAccuracy")).toBeDefined();
    expect(screen.getByText("95%")).toBeDefined();
  });

  it("renders raw counts WITHOUT a % suffix (U5 regression)", () => {
    // Previously ToolCallsCount (42) and ExceptionsCount (3) were rendered
    // as "42%" / "3%" because the card applied % to every harness_metrics
    // value. Counts must now render as plain numbers.
    render(<TelemetryStatusCard digest={sampleDigest} />);
    expect(screen.getByText("42")).toBeDefined();
    expect(screen.getByText("3")).toBeDefined();
    // The counts must NOT carry a % - "42%" must not exist in the document.
    expect(screen.queryByText("42%")).toBeNull();
    expect(screen.queryByText("3%")).toBeNull();
  });

  it("triggers onRefresh callback when refresh button clicked", () => {
    const handleRefresh = vi.fn();
    render(<TelemetryStatusCard digest={sampleDigest} onRefresh={handleRefresh} />);
    const refreshButton = screen.getByText("刷新");
    fireEvent.click(refreshButton);
    expect(handleRefresh).toHaveBeenCalledTimes(1);
  });
});
