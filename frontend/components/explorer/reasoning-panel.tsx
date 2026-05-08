"use client";

import { useState } from "react";
import type { SpatialReasoningResult, ReasoningStep } from "@/lib/types/explorer";

interface ReasoningPanelProps {
  result: SpatialReasoningResult;
}

function getConfidenceInfo(confidence: number) {
  if (confidence >= 0.8) {
    return {
      label: `置信度 高 (${(confidence * 100).toFixed(0)}%)`,
      badgeClass: "bg-green-500/20 text-green-400",
    };
  }
  if (confidence >= 0.5) {
    return {
      label: `置信度 中 (${(confidence * 100).toFixed(0)}%)`,
      badgeClass: "bg-yellow-500/20 text-yellow-400",
    };
  }
  return {
    label: `置信度 低 (${(confidence * 100).toFixed(0)}%)`,
    badgeClass: "bg-red-500/20 text-red-400",
  };
}

function truncateFact(fact: string, maxLength: number = 40): string {
  if (fact.length <= maxLength) return fact;
  return fact.slice(0, maxLength) + "…";
}

function ReasoningStepCard({
  step,
  isExpanded,
  onToggle,
}: {
  step: ReasoningStep;
  isExpanded: boolean;
  onToggle: () => void;
}) {
  return (
    <div className="rounded-lg border border-white/10 bg-white/5">
      <button
        onClick={onToggle}
        className="flex w-full items-center justify-between p-3 text-left"
      >
        <div className="flex items-center gap-2">
          <span className="rounded-full bg-blue-500/20 px-2 py-0.5 text-xs text-blue-400">
            {step.step}
          </span>
          <span className="text-sm text-white/80">
            {isExpanded ? step.fact : truncateFact(step.fact)}
          </span>
        </div>
        <span className="text-xs text-white/50">
          {isExpanded ? "收起" : "展开"}
        </span>
      </button>
      {isExpanded && (
        <div className="border-t border-white/10 px-3 pb-3 pt-2">
          <p className="text-sm text-white/80">{step.fact}</p>
          <p className="mt-1 text-xs text-white/50">来源：{step.source}</p>
        </div>
      )}
    </div>
  );
}

export function ReasoningPanel({ result }: ReasoningPanelProps) {
  const [expandedSteps, setExpandedSteps] = useState<Set<number>>(
    () => new Set(result.reasoning_chain.length > 0 ? [result.reasoning_chain[0].step] : [])
  );

  const confidence = getConfidenceInfo(result.confidence);

  function toggleStep(stepNumber: number) {
    setExpandedSteps((prev) => {
      const next = new Set(prev);
      if (next.has(stepNumber)) {
        next.delete(stepNumber);
      } else {
        next.add(stepNumber);
      }
      return next;
    });
  }

  return (
    <div className="rounded-xl border border-white/10 bg-black/40 p-4 backdrop-blur-sm">
      {/* Header */}
      <div className="flex items-center justify-between">
        <h2 className="text-sm font-semibold text-white/90">空间推演分析</h2>
        <span
          className={`rounded-full px-2.5 py-0.5 text-xs font-medium ${confidence.badgeClass}`}
        >
          {confidence.label}
        </span>
      </div>

      {/* Conclusion */}
      <div className="mt-4">
        <h3 className="text-xs font-medium uppercase tracking-wider text-white/40">
          结论
        </h3>
        <p className="mt-1 text-sm text-white/80">{result.conclusion}</p>
      </div>

      {/* Reasoning Chain */}
      <div className="mt-4">
        <h3 className="text-xs font-medium uppercase tracking-wider text-white/40">
          推理依据
        </h3>
        <div className="mt-2 space-y-2">
          {result.reasoning_chain.map((step) => (
            <ReasoningStepCard
              key={step.step}
              step={step}
              isExpanded={expandedSteps.has(step.step)}
              onToggle={() => toggleStep(step.step)}
            />
          ))}
        </div>
      </div>

      {/* Uncertainty */}
      <div className="mt-4 rounded-lg border border-yellow-500/20 bg-yellow-500/5 p-3">
        <h3 className="text-xs font-medium uppercase tracking-wider text-yellow-400/80">
          不确定性
        </h3>
        <p className="mt-1 text-xs font-medium text-yellow-400/80">
          {result.uncertainty}
        </p>
      </div>

      {/* Recommendations */}
      <div className="mt-4">
        <h3 className="text-xs font-medium uppercase tracking-wider text-white/40">
          建议
        </h3>
        <ul className="mt-1 list-inside list-disc space-y-1">
          {result.recommendations.map((rec, index) => (
            <li key={index} className="text-sm text-white/80">
              {rec}
            </li>
          ))}
        </ul>
      </div>
    </div>
  );
}
