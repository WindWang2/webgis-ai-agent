'use client';
import React from 'react';
import {
  DndContext,
  closestCenter,
  KeyboardSensor,
  PointerSensor,
  useSensor,
  useSensors,
  DragEndEvent,
} from '@dnd-kit/core';
import {
  arrayMove,
  SortableContext,
  sortableKeyboardCoordinates,
  verticalListSortingStrategy,
  useSortable,
} from '@dnd-kit/sortable';
import { CSS } from '@dnd-kit/utilities';
import { LayerCard } from '../layer-card';
import { Layer } from '@/lib/types/layer';

interface SortableLayerItemProps {
  layer: Layer;
  onToggle: (id: string) => void;
  onDelete: (id: string) => void;
  onUpdate: (id: string, updates: Partial<Layer>) => void;
}

function SortableLayerItem({ layer, onToggle, onDelete, onUpdate }: SortableLayerItemProps) {
  const {
    attributes,
    listeners,
    setNodeRef,
    transform,
    transition,
    isDragging,
  } = useSortable({ id: layer.id });

  const style = {
    transform: CSS.Transform.toString(transform),
    transition,
    zIndex: isDragging ? 100 : 1,
    boxShadow: isDragging ? '0 10px 15px -3px rgb(0 0 0 / 0.1), 0 4px 6px -4px rgb(0 0 0 / 0.1)' : 'none',
  };

  return (
    <div ref={setNodeRef} style={style} className={isDragging ? 'relative z-50 opacity-80' : ''}>
      <LayerCard
        layer={layer}
        onToggle={onToggle}
        onDelete={onDelete}
        onEdit={() => {}} // Legacy
        onUpdate={onUpdate}
        dragHandleProps={{ ...attributes, ...listeners }}
      />
    </div>
  );
}

interface DraggableLayerListProps {
  layers: Layer[];
  onReorder: (newLayers: Layer[]) => void;
  onToggle: (id: string) => void;
  onDelete: (id: string) => void;
  onUpdate: (id: string, updates: Partial<Layer>) => void;
}

export function DraggableLayerList({
  layers,
  onReorder,
  onToggle,
  onDelete,
  onUpdate,
}: DraggableLayerListProps) {
  const sensors = useSensors(
    useSensor(PointerSensor, {
      activationConstraint: {
        distance: 5, // Allow for click without drag
      },
    }),
    useSensor(KeyboardSensor, {
      coordinateGetter: sortableKeyboardCoordinates,
    })
  );

  const handleDragEnd = (event: DragEndEvent) => {
    const { active, over } = event;

    if (over && active.id !== over.id) {
      const oldIndex = layers.findIndex((l) => l.id === active.id);
      const newIndex = layers.findIndex((l) => l.id === over.id);
      onReorder(arrayMove(layers, oldIndex, newIndex));
    }
  };

  return (
    <DndContext
      sensors={sensors}
      collisionDetection={closestCenter}
      onDragEnd={handleDragEnd}
    >
      <SortableContext
        items={layers.map((l) => l.id)}
        strategy={verticalListSortingStrategy}
      >
        <div className="flex flex-col gap-3 p-1">
          {layers.map((layer) => (
            <SortableLayerItem
              key={layer.id}
              layer={layer}
              onToggle={onToggle}
              onDelete={onDelete}
              onUpdate={onUpdate}
            />
          ))}
          {layers.length === 0 && (
            <div className="text-center py-8 text-muted-foreground bg-muted/20 rounded-lg border border-dashed border-border/50">
              <p className="text-sm">暂无激活图层</p>
              <p className="text-[10px] mt-1">分析产生的结果将自动出现在此处</p>
            </div>
          )}
        </div>
      </SortableContext>
    </DndContext>
  );
}
