"use client";

import React from "react";
import { useDroppable } from "@dnd-kit/core";

interface DroppableSlotProps {
  id: string;
  children: React.ReactNode;
}

export default function DroppableSlot({ id, children }: DroppableSlotProps) {
  const { isOver, setNodeRef } = useDroppable({
    id: id,
  });

  return (
    <div
      ref={setNodeRef}
      className={`h-full p-1 relative transition-colors duration-200 ${
        isOver ? "bg-cyan-500/10 ring-1 ring-cyan-500" : ""
      }`}
    >
      {/* Over-indicator overlay */}
      {isOver && (
        <div className="absolute inset-0 bg-cyan-500/5 backdrop-blur-[1px] flex items-center justify-center z-10">
          <div className="px-3 py-1 bg-cyan-500 text-black text-[10px] font-bold rounded uppercase tracking-tighter">
            Drop Here to Swap
          </div>
        </div>
      )}
      {children}
    </div>
  );
}
