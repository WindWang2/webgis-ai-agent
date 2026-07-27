import { describe, it, expect } from "vitest";
import { render, screen, fireEvent } from "@testing-library/react";
import { ReasoningPanel } from "./reasoning-panel";
import type { SpatialReasoningResult } from "@/lib/types/explorer";

function makeResult(overrides: Partial<SpatialReasoningResult> = {}): SpatialReasoningResult {
  return {
    type: "spatial_reasoning",
    conclusion: "测试结论",
    reasoning_chain: [
      { step: 1, fact: "第一步", source: "src-1" },
      { step: 2, fact: "第二步", source: "src-2" },
      { step: 3, fact: "第三步", source: "src-3" },
    ],
    confidence: 0.9,
    uncertainty: "低",
    recommendations: ["建议一"],
    ...overrides,
  };
}

// 通过步骤的「事实文本」定位该步骤的展开按钮。每个步骤按钮的可访问名
// 包含其 fact 文本（折叠态显示截断文本，展开态显示完整文本），因此用
// getByRole('button', { name: /fact/ }) 能稳定命中单个元素。
function toggleForFact(fact: string): HTMLElement {
  return screen.getByRole("button", { name: new RegExp(fact) });
}

describe("ReasoningPanel expansion state", () => {
  // 审计 findings.md State Management：展开状态应在 result 变化（新对象）时重置为默认，
  // 但同一对象重新渲染时必须保留用户的展开/折叠操作。该用例锁定此前 useEffect 修复引入的回归。
  it("resets expansion to default when a new result object arrives", () => {
    const first = makeResult();
    const { rerender } = render(<ReasoningPanel result={first} />);

    // 默认：第一步展开，其余折叠。
    expect(toggleForFact("第一步")).toHaveAttribute("aria-expanded", "true");
    expect(toggleForFact("第三步")).toHaveAttribute("aria-expanded", "false");

    // 用户展开第三步。
    fireEvent.click(toggleForFact("第三步"));
    expect(toggleForFact("第三步")).toHaveAttribute("aria-expanded", "true");

    // 传入「相同内容、新对象身份」的结果 → 应重置回默认（第三步重新折叠）。
    const next = makeResult();
    rerender(<ReasoningPanel result={next} />);

    expect(toggleForFact("第一步")).toHaveAttribute("aria-expanded", "true");
    expect(toggleForFact("第三步")).toHaveAttribute("aria-expanded", "false");
  });

  it("preserves user toggles when re-rendered with the same result object", () => {
    const result = makeResult();
    const { rerender } = render(<ReasoningPanel result={result} />);

    // 用户展开第三步。
    fireEvent.click(toggleForFact("第三步"));
    expect(toggleForFact("第三步")).toHaveAttribute("aria-expanded", "true");

    // 用「同一对象引用」重新渲染 → 用户折叠状态必须保留（此前错误 effect 会重置）。
    rerender(<ReasoningPanel result={result} />);
    expect(toggleForFact("第三步")).toHaveAttribute("aria-expanded", "true");
  });
});
