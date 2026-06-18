"use client";

import React from "react";
import { useDraggable } from "@dnd-kit/core";
import { CSS } from "@dnd-kit/utilities";
import { GripVertical } from "lucide-react";

interface DraggableWidgetProps {
  id: string;
  children: React.ReactNode;
  title: string;
}

export default function DraggableWidget({
  id,
  children,
  title,
}: DraggableWidgetProps) {
  const { attributes, listeners, setNodeRef, transform, isDragging } =
    useDraggable({
      id: id,
    });

  const style = {
    transform: CSS.Translate.toString(transform),
    opacity: isDragging ? 0.3 : 1,
    zIndex: isDragging ? 100 : "auto",
  };

  return (
    <div
      ref={setNodeRef}
      style={style}
      className="h-full flex flex-col group/widget relative bg-[#0a0a0a] border border-white/5 rounded-lg overflow-hidden shadow-2xl shadow-black/50"
    >
      <div
        {...listeners}
        {...attributes}
        className="shrink-0 h-6 bg-white/5 border-b border-white/5 flex items-center px-2 cursor-grab active:cursor-grabbing hover:bg-white/10 transition-colors"
      >
        <GripVertical className="size-3 text-gray-500 mr-2" />
        <span className="text-[10px] font-mono uppercase tracking-widest text-gray-400">
          {title}
        </span>
      </div>
      <div className="flex-1 min-h-0 bg-black/20">{children}</div>
    </div>
  );
}
