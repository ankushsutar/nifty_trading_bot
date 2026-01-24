'use client';

import { motion } from "framer-motion";
import { ReactNode } from "react";

interface CardProps {
  children: ReactNode;
  className?: string;
  title?: string;
  icon?: ReactNode;
  glow?: boolean;
}

export default function Card({ children, className = "", title, icon, glow = false }: CardProps) {
  return (
    <motion.div
      initial={{ opacity: 0, y: 10 }}
      animate={{ opacity: 1, y: 0 }}
      transition={{ duration: 0.4 }}
      className={`glass-panel rounded-xl p-6 relative overflow-hidden ${className} ${glow ? "border-cyan-500/30 shadow-[0_0_20px_rgba(0,240,255,0.1)]" : "border-white/5"}`}
    >
      {/* Top Accent Line */}
      <div className="absolute top-0 left-0 w-full h-[1px] bg-gradient-to-r from-transparent via-cyan-500/50 to-transparent opacity-50"></div>
      
      {(title || icon) && (
        <div className="flex items-center gap-3 mb-6 border-b border-white/5 pb-3">
          {icon && <span className="text-cyan-400">{icon}</span>}
          {title && <h2 className="text-sm font-bold tracking-widest uppercase text-gray-400">{title}</h2>}
        </div>
      )}
      
      <div className="relative z-10">
        {children}
      </div>
      
      {/* Background radial gradient for subtle depth */}
      <div className="absolute -top-20 -right-20 w-40 h-40 bg-cyan-500/5 rounded-full blur-3xl pointer-events-none"></div>
    </motion.div>
  );
}
