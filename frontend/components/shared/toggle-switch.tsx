'use client';

import React from 'react';

interface ToggleSwitchProps {
  checked: boolean;
  onChange: () => void;
  accentColor?: string;
}

export default function ToggleSwitch({
  checked,
  onChange,
  accentColor = '#16a34a',
}: ToggleSwitchProps) {
  return (
    <button
      type="button"
      role="switch"
      aria-checked={checked}
      onClick={onChange}
      className="relative inline-flex flex-shrink-0 cursor-pointer border-0 p-0 focus:outline-none"
      style={{ width: 34, height: 19 }}
    >
      {/* Track */}
      <span
        className="block rounded-full transition-colors duration-200"
        style={{
          width: 34,
          height: 19,
          backgroundColor: checked ? accentColor : 'rgba(15,23,42,0.15)',
        }}
      />
      {/* Knob */}
      <span
        className="absolute top-[2px] rounded-full bg-white transition-all duration-200"
        style={{
          width: 15,
          height: 15,
          left: checked ? 16 : 2,
          boxShadow: '0 1px 3px rgba(0,0,0,0.15), 0 1px 2px rgba(0,0,0,0.1)',
        }}
      />
    </button>
  );
}
