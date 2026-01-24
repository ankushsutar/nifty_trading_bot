'use client';

import React, { useState, useEffect } from 'react';
import { Power, Shield, Zap, AlertTriangle, Radio } from 'lucide-react';
import { motion } from 'framer-motion';
import Card from './ui/Card';

export default function MissionControl() {
  const [isRunning, setIsRunning] = useState(false);
  const [loading, setLoading] = useState(false);
  const [isDryRun, setIsDryRun] = useState(true);
  const [strategy, setStrategy] = useState("AUTO"); // AUTO or MANUAL

  // Sync state on load
  useEffect(() => {
    fetch('http://localhost:8000/api/status')
      .then(res => res.json())
      .then(data => {
        if (data.status === 'RUNNING') {
           setIsRunning(true);
           // Assume lifecycle manager handles strategy
        }
      })
      .catch(err => console.error("Failed to fetch status:", err));
  }, []);

  const handleStart = async () => {
    setLoading(true);
    try {
      const res = await fetch('http://localhost:8000/api/start', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ strategy: strategy, dry_run: isDryRun })
      });
      const data = await res.json();
      if (data.status === 'success') {
        setIsRunning(true);
      } else {
        alert('Error: ' + data.message);
      }
    } catch (e) {
      alert('API Error: ' + e);
    }
    setLoading(false);
  };

  const handleStop = async () => {
    if (!confirm("⚠️ Confirm Emergency Shutdown? \nThis will kill the trading process immediately.")) return;
    setLoading(true);
    try {
      await fetch('http://localhost:8000/api/stop', { method: 'POST' });
      // Poll a few times to confirm stop
      setTimeout(() => setIsRunning(false), 2000); 
    } catch (e) {
      alert('API Error: ' + e);
    }
    setLoading(false);
  };

  return (
    <Card title="Mission Control" icon={<Radio size={18} />} glow={isRunning}>
      
      {/* 1. Safety Switch (Dry Run vs Live) */}
      <div className="mb-8">
        <label className="text-xs text-gray-500 uppercase tracking-widest mb-3 block">Safety Protocol</label>
        <div className="bg-black/40 p-1 rounded-lg border border-white/10 flex relative">
           {/* Slider Background */}
           <motion.div 
             className={`absolute top-1 bottom-1 w-[48%] rounded-md z-0 ${isDryRun ? "bg-yellow-500/20" : "bg-red-500/20 left-[50%]"}`}
             layout
             transition={{ type: "spring", stiffness: 500, damping: 30 }}
           />

           <button 
             onClick={() => setIsDryRun(true)}
             className={`flex-1 relative z-10 py-3 flex items-center justify-center gap-2 text-sm font-bold transition-all ${isDryRun ? "text-yellow-400" : "text-gray-600"}`}
           >
             <Shield size={16} /> SIMULATION
           </button>
           
           <button 
             onClick={() => setIsDryRun(false)}
             className={`flex-1 relative z-10 py-3 flex items-center justify-center gap-2 text-sm font-bold transition-all ${!isDryRun ? "text-red-500 text-glow" : "text-gray-600"}`}
           >
             <Zap size={16} /> LIVE EXECUTION
           </button>
        </div>
      </div>

      {/* 2. Strategy Selector */}
      <div className="mb-8">
        <label className="text-xs text-gray-500 uppercase tracking-widest mb-3 block">Strategy Logic</label>
        <select 
           value={strategy} 
           onChange={(e) => setStrategy(e.target.value)}
           className="w-full bg-black/40 border border-white/10 text-cyan-400 font-mono text-sm p-3 rounded focus:outline-none focus:border-cyan-500/50"
           disabled={isRunning}
        >
            <option value="AUTO">🤖 AUTO-PILOT (Lifecycle Manager)</option>
            <option value="MOMENTUM">⚡ MOMENTUM (Trend Follow)</option>
            <option value="STRADDLE">📉 STRADDLE (Delta Neutral)</option>
            <option value="OHL">🎯 OHL SCALP (Morning)</option>
        </select>
      </div>

      {/* 3. ARM / DISARM Buttons */}
      <div className="grid grid-cols-1 gap-4">
        {!isRunning ? (
            <motion.button
              whileHover={{ scale: 1.02 }}
              whileTap={{ scale: 0.95 }}
              onClick={handleStart}
              disabled={loading}
              className="group relative w-full h-16 bg-cyan-900/20 border border-cyan-500/30 overflow-hidden rounded-lg flex items-center justify-center gap-3 text-cyan-400 font-black tracking-widest text-lg transition-all hover:bg-cyan-500/10 hover:border-cyan-400"
            >
               <div className="absolute inset-x-0 h-[2px] bg-cyan-400 top-0 opacity-0 group-hover:opacity-100 transition-opacity" />
               <Power className="group-hover:drop-shadow-[0_0_8px_rgba(0,240,255,0.8)]" />
               INITIALIZE SYSTEM
            </motion.button>
        ) : (
            <motion.button
              whileHover={{ scale: 1.02 }}
              whileTap={{ scale: 0.95 }}
              onClick={handleStop}
              className="group relative w-full h-16 bg-red-900/20 border border-red-500/30 overflow-hidden rounded-lg flex items-center justify-center gap-3 text-red-500 font-black tracking-widest text-lg transition-all hover:bg-red-500/10 hover:border-red-400 hover:text-red-400"
            >
               <div className="absolute inset-x-0 h-[2px] bg-red-500 top-0 opacity-0 group-hover:opacity-100 transition-opacity" />
               <motion.div animate={{ opacity: [1, 0.5, 1] }} transition={{ repeat: Infinity, duration: 1 }}>
                 <AlertTriangle />
               </motion.div>
               EMERGENCY CUTOFF
            </motion.button>
        )}
      </div>

      {/* Status Footer */}
      <div className="mt-6 flex justify-between items-center text-[10px] text-gray-500 font-mono uppercase">
         <span>Latency: 24ms</span>
         <span className={isRunning ? "text-green-500" : "text-gray-600"}>
            {isRunning ? "● SYSTEM ACTIVE" : "○ SYSTEM IDLE"}
         </span>
      </div>

    </Card>
  );
}
