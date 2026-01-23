'use client';

import React, { useState, useEffect } from 'react';
import { Play, Square, Activity, AlertTriangle, Shield, CheckCircle } from 'lucide-react';
import { motion } from 'framer-motion';

export default function ControlPanel() {
  const [isRunning, setIsRunning] = useState(false);
  const [loading, setLoading] = useState(false);
  const [isDryRun, setIsDryRun] = useState(true);
  const [showConfirm, setShowConfirm] = useState(false); // Modal State

  // Sync state on load
  useEffect(() => {
    fetch('http://localhost:8000/api/status')
      .then(res => res.json())
      .then(data => {
        if (data.status === 'RUNNING') {
           setIsRunning(true);
        }
      })
      .catch(err => console.error("Failed to fetch status:", err));
  }, []);

  const startBot = async () => {
    setLoading(true);
    try {
      const res = await fetch('http://localhost:8000/api/start', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ strategy: 'MOMENTUM', dry_run: isDryRun })
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

  const handleStopClick = () => {
    setShowConfirm(true); // Open Modal
  };

  // Poll for status helper
  const pollStatus = async () => {
      try {
          const res = await fetch('http://localhost:8000/api/status');
          const data = await res.json();
          return data.status;
      } catch (e) {
          return null;
      }
  };

  const confirmStop = async () => {
    setShowConfirm(false); // Close Modal
    setLoading(true);
    
    try {
      // 1. Send Stop Signal
      await fetch('http://localhost:8000/api/stop', { method: 'POST' });
      
      // 2. Poll until actually stopped
      let attempts = 0;
      const maxAttempts = 30; // 30 seconds max wait
      
      const checkLoop = setInterval(async () => {
          attempts++;
          const status = await pollStatus();
          
          if (status === 'STOPPED') {
              clearInterval(checkLoop);
              setIsRunning(false);
              setLoading(false);
          } else if (attempts >= maxAttempts) {
              clearInterval(checkLoop);
              setLoading(false);
              alert("Backend is taking too long to stop. Please check logs.");
          }
      }, 1000); // Check every 1s

    } catch (e) {
      alert('API Error: ' + e);
      setLoading(false);
    }
  };

  return (
    <div className="bg-gray-900 border border-gray-800 p-6 rounded-xl shadow-lg relative overflow-hidden">
      
      {/* Confirmation Modal Overlay */}
      {showConfirm && (
        <div className="absolute inset-0 bg-black/80 backdrop-blur-sm z-50 flex flex-col items-center justify-center p-4 text-center">
            <motion.div 
               initial={{ scale: 0.9, opacity: 0 }} 
               animate={{ scale: 1, opacity: 1 }}
               className="bg-gray-800 border border-red-500/50 p-6 rounded-xl shadow-2xl max-w-xs w-full"
            >
                <div className="flex justify-center mb-4 text-red-500">
                    <AlertTriangle size={48} className="animate-pulse" />
                </div>
                <h3 className="text-xl font-bold text-white mb-2">Confirm Shutdown?</h3>
                <p className="text-gray-400 text-sm mb-6">
                    Stopping the bot will halt all monitoring. 
                    <br/><span className="text-red-400 font-bold">Open positions will NOT be closed automatically.</span>
                </p>
                <div className="flex gap-3">
                    <button 
                       onClick={() => setShowConfirm(false)}
                       className="flex-1 py-3 bg-gray-700 hover:bg-gray-600 rounded text-sm font-bold transition-colors"
                    >
                        Cancel
                    </button>
                    <button 
                       onClick={confirmStop}
                       className="flex-1 py-3 bg-red-600 hover:bg-red-700 text-white rounded text-sm font-bold shadow-lg shadow-red-600/20 transition-colors"
                    >
                        Yes, Stop
                    </button>
                </div>
            </motion.div>
        </div>
      )}

      <h2 className="text-xl font-bold text-white mb-6 flex items-center gap-2">
        <Activity className="text-blue-500" /> Control Deck
      </h2>

      {/* Mode Selection */}
      <div className="mb-6 bg-gray-800/50 p-4 rounded-lg border border-gray-700">
        <div className="flex justify-between items-center mb-2">
           <span className="text-gray-300 font-medium">Execution Mode</span>
           {isDryRun ? (
               <span className="flex items-center gap-1 text-yellow-400 text-xs font-bold px-2 py-1 bg-yellow-400/10 rounded border border-yellow-400/20">
                   <Shield size={12}/> SAFEMODE
               </span>
           ) : (
               <span className="flex items-center gap-1 text-red-500 text-xs font-bold px-2 py-1 bg-red-500/10 rounded border border-red-500/20 animate-pulse">
                   <AlertTriangle size={12}/> LIVE TRADING
               </span>
           )}
        </div>
        
        <div className="flex gap-2">
            <button
                onClick={() => setIsDryRun(true)}
                disabled={isRunning}
                className={`flex-1 py-2 text-sm font-medium rounded transition-colors ${
                    isDryRun 
                    ? 'bg-yellow-500 text-black shadow-lg' 
                    : 'bg-gray-700 text-gray-400 hover:bg-gray-600'
                }`}
            >
                Dry Run (Paper)
            </button>
             <button
                onClick={() => setIsDryRun(false)}
                disabled={isRunning}
                className={`flex-1 py-2 text-sm font-medium rounded transition-colors ${
                    !isDryRun 
                    ? 'bg-red-600 text-white shadow-lg' 
                    : 'bg-gray-700 text-gray-400 hover:bg-gray-600'
                }`}
            >
                Live Trade
            </button>
        </div>
        {!isDryRun && (
            <div className="mt-2 text-xs text-red-400 flex items-start gap-1">
                <AlertTriangle size={12} className="mt-0.5 shrink-0"/>
                Warning: Real money will be used. Ensure your Angel One account has funds.
            </div>
        )}
      </div>

      <div className="grid grid-cols-2 gap-4">
        {/* Run Button */}
        <motion.button
          whileTap={{ scale: 0.95 }}
          onClick={startBot}
          disabled={isRunning || loading}
          className={`flex flex-col items-center justify-center p-6 rounded-lg transition-all border ${
            isRunning 
              ? 'bg-gray-800 border-gray-700 text-gray-500 cursor-not-allowed' 
              : 'bg-green-600 text-white hover:bg-green-500 border-transparent shadow-green-500/20 shadow-lg'
          }`}
        >
          <Play size={32} className="mb-2 fill-current" />
          <span className="font-bold text-lg">START BOT</span>
        </motion.button>

        {/* Stop Button */}
        <motion.button
          whileTap={{ scale: 0.95 }}
          onClick={handleStopClick}
          disabled={!isRunning || loading}
          className={`flex flex-col items-center justify-center p-6 rounded-lg transition-all border ${
            !isRunning 
              ? 'bg-gray-800 border-gray-700 text-gray-500 cursor-not-allowed' 
              : 'bg-red-900/20 border-red-500 text-red-500 hover:bg-red-900/40'
          }`}
        >
          <Square size={32} className="mb-2 fill-current" />
          <span className="font-bold text-lg">STOP BOT</span>
        </motion.button>
      </div>

      {isRunning && (
        <div className="mt-6 flex items-center gap-3 bg-green-500/10 p-4 rounded-lg text-green-400 text-sm border border-green-500/20">
          <span className="relative flex h-3 w-3 shrink-0">
             <span className="animate-ping absolute inline-flex h-full w-full rounded-full bg-green-400 opacity-75"></span>
             <span className="relative inline-flex rounded-full h-3 w-3 bg-green-500"></span>
          </span>
          <div>
              <p className="font-bold">System Active</p>
              <p className="text-xs opacity-70">
                  Scanning Nifty 50 on 5-min timeframe...
              </p>
          </div>
        </div>
      )}
    </div>
  );
}
