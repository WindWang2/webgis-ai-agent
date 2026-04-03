'use client';
import React, { useState, memo } from 'react';
import { ArrowUpDown } from 'lucide-react';
import type { SortOption, SortField } from '@/lib/types/layer';
import clsx from 'clsx';
import { twMerge } from 'tailwind-merge';
import { cn } from '@/lib/utils';

interface SortControlsProps {
  onSort: (sort: SortOption) => void;
  defaultField?: SortField;
  defaultOrder?: 'asc' | 'desc';
}
const SORT_FIELDS: { value: SortField; label: string }[] = [
  { value: 'name', label: 'Name' },
  { value: 'created_at', label: 'Created' },
  { value: 'updated_at', label: 'Updated' },
];
export const SortControls = memo(function SortControls({
  onSort,
  defaultField = 'name',
  defaultOrder = 'asc',
}: SortControlsProps) {
  const [sort, setSort] = useState<SortOption>({
    field: defaultField,
    order: defaultOrder,
  });
  const handleSort = (updates: Partial<SortOption>) => {
    const newSort = { ...sort, ...updates };
    setSort(newSort);
    onSort(newSort);
  };
  return (
    <div className="flex items-center gap-3">
      <ArrowUpDown size={16} className="text-gray-500" />
      <select
        value={sort.field}
        onChange={(e) => handleSort({ field: e.target.value as SortField })}
        className="px-3 py-1.5 text-sm border rounded-md"
      >
        {SORT_FIELDS.map((field) => (
          <option key={field.value} value={field.value}>
            {field.label}
          </option>
        ))}
      </select>
      <label className="flex items-center gap-1.5 cursor-pointer">
        <input
          type="radio"
          name="sort-order"
          checked={sort.order === 'asc'}
          onChange={() => handleSort({ order: 'asc' })}
          className="accent-blue-600"
        />
        <span className="text-sm">Asc</span>
      </label>
      <label className="flex items-center gap-1.5 cursor-pointer">
        <input
          type="radio"
          name="sort-order"
          checked={sort.order === 'desc'}
          onChange={() => handleSort({ order: 'desc' })}
          className="accent-blue-600"
        />
        <span className="text-sm">Desc</span>
      </label>
    </div>
  );
});