"use client";

import React, { useState, useEffect } from "react";
import { Power, Shield, Zap, AlertTriangle, Radio } from "lucide-react";
import { motion } from "framer-motion";
import Card from "./ui/Card";

export default function MissionControl() {
  const [isRunning, setIsRunning] = useState(false);
  const [loading, setLoading] = useState(false);
  const [isDryRun, setIsDryRun] = useState(true);
  const [strategy, setStrategy] = useState("AUTO");

  useEffect(() => {
    fetch("http://localhost:8000/api/status")
      .then((res) => res.json())
      .then((data) => {
        if (data.status === "RUNNING") setIsRunning(true);
      })
      .catch(console.error);
  }, []);

  const handleStart = async () => {
    setLoading(true);
    try {
      const res = await fetch("http://localhost:8000/api/start", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ strategy, dry_run: isDryRun }),
      });
      const data = await res.json();
      if (data.status === "success") setIsRunning(true);
      else alert(data.message);
    } catch (e) {
      alert(e);
    }
    setLoading(false);
  };

  const handleStop = async () => {
    if (
      !confirm(
        "⚠️ Confirm Emergency Shutdown?\nThis will terminate live execution immediately.",
      )
    )
      return;
    setLoading(true);
    try {
      await fetch("http://localhost:8000/api/stop", { method: "POST" });
      setTimeout(() => setIsRunning(false), 1500);
    } catch (e) {
      alert(e);
    }
    setLoading(false);
  };

  return (
    <Card
      title="Mission Control"
      icon={<Radio size={16} />}
      glow={isRunning}
    >
      {/* SAFETY MODE */}
      <section className="mb-5">
        <label className="block text-[10px] text-gray-500 tracking-widest uppercase mb-2">
          Execution Mode
        </label>

        <div className="relative flex rounded-lg bg-black/50 border border-white/10 p-1">
          <motion.div
            layout
            transition={{ type: "spring", stiffness: 400, damping: 30 }}
            className={`absolute inset-y-1 w-1/2 rounded-md ${
              isDryRun
                ? "bg-yellow-500/15 left-1"
                : "bg-red-500/20 left-[50%]"
            }`}
          />

          <button
            onClick={() => setIsDryRun(true)}
            className={`relative z-10 flex-1 py-2 text-xs font-semibold tracking-wide flex items-center justify-center gap-2 ${
              isDryRun ? "text-yellow-400" : "text-gray-600"
            }`}
          >
            <Shield size={14} /> SIMULATION
          </button>

          <button
            onClick={() => setIsDryRun(false)}
            className={`relative z-10 flex-1 py-2 text-xs font-semibold tracking-wide flex items-center justify-center gap-2 ${
              !isDryRun
                ? "text-red-500 drop-shadow-[0_0_6px_rgba(239,68,68,0.6)]"
                : "text-gray-600"
            }`}
          >
            <Zap size={14} /> LIVE
          </button>
        </div>
      </section>

      {/* STRATEGY */}
      <section className="mb-5">
        <label className="block text-[10px] text-gray-500 tracking-widest uppercase mb-2">
          Strategy Engine
        </label>

        <select
          disabled={isRunning}
          value={strategy}
          onChange={(e) => setStrategy(e.target.value)}
          className="w-full rounded-md bg-black/60 border border-white/10 px-3 py-2 text-xs font-mono text-cyan-400 focus:outline-none focus:border-cyan-500/50 disabled:opacity-40"
        >
          <option value="AUTO">🤖 AUTO-PILOT</option>
          <option value="MOMENTUM">⚡ MOMENTUM</option>
          <option value="STRADDLE">📉 STRADDLE</option>
          <option value="OHL">🎯 OHL SCALP</option>
        </select>
      </section>

      {/* ACTION */}
      <section>
        {!isRunning ? (
          <motion.button
            whileHover={{ scale: 1.02 }}
            whileTap={{ scale: 0.96 }}
            disabled={loading}
            onClick={handleStart}
            className="w-full h-11 rounded-md border border-cyan-500/30 bg-cyan-900/20 text-cyan-400 font-semibold tracking-widest text-xs flex items-center justify-center gap-2 hover:bg-cyan-500/10 hover:border-cyan-400 disabled:opacity-40"
          >
            <Power size={14} />
            ARM SYSTEM
          </motion.button>
        ) : (
          <motion.button
            whileHover={{ scale: 1.02 }}
            whileTap={{ scale: 0.96 }}
            onClick={handleStop}
            className="w-full h-11 rounded-md border border-red-500/30 bg-red-900/20 text-red-500 font-semibold tracking-widest text-xs flex items-center justify-center gap-2 hover:bg-red-500/10 hover:border-red-400"
          >
            <motion.span
              animate={{ opacity: [1, 0.4, 1] }}
              transition={{ repeat: Infinity, duration: 1 }}
            >
              <AlertTriangle size={14} />
            </motion.span>
            EMERGENCY STOP
          </motion.button>
        )}
      </section>

      {/* FOOTER */}
      <footer className="mt-6 flex items-center justify-between text-[10px] font-mono uppercase">
        <span className="text-gray-600">Latency · 24ms</span>
        <span
          className={`flex items-center gap-1 ${
            isRunning ? "text-green-500" : "text-gray-600"
          }`}
        >
          <span className="text-xs">●</span>
          {isRunning ? "System Active" : "System Idle"}
        </span>
      </footer>
    </Card>
  );
}
