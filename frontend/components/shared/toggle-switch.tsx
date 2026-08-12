'use client';

import React from 'react';

interface ToggleSwitchProps {
  checked: boolean;
  onChange: () => void;
  /**
   * 可访问名称。a11y 修复：此前 role="switch" 有 aria-checked 但完全没有名字，
   * 于是 LLM / RAG 设置以及技能中心里每一个开关对读屏都是「无名开关」。
   */
  label: string;
}

export default function ToggleSwitch({ checked, onChange, label }: ToggleSwitchProps) {
  return (
    <button
      type="button"
      role="switch"
      aria-checked={checked}
      aria-label={label}
      title={label}
      onClick={onChange}
      // 只保留 relative 定位；焦点环由 globals.css 的 *:focus-visible 统一提供，
      // 不再手写 focus:outline-none（那会把键盘可见性交给一条兜底规则去救）。
      className="relative inline-flex h-[18px] w-[32px] shrink-0 cursor-pointer border-0 p-0"
    >
      <span
        aria-hidden
        className={`block h-[18px] w-[32px] rounded-pill transition-colors duration-200 ${
          checked ? 'bg-status-accent-vivid' : 'bg-edge-strong'
        }`}
      />
      <span
        aria-hidden
        className={`absolute top-[2px] h-[14px] w-[14px] rounded-pill bg-surface-raised shadow-raised transition-[left] duration-200 ${
          checked ? 'left-[16px]' : 'left-[2px]'
        }`}
      />
    </button>
  );
}
