'use client';

import React, { useEffect, useState, useRef } from 'react';
import { motion } from 'framer-motion';

interface LogMessage {
  timestamp: string;
  level: string;
  message: string;
}

export default function Terminal() {
  const [logs, setLogs] = useState<LogMessage[]>([]);
  const scrollRef = useRef<HTMLDivElement>(null);

  useEffect(() => {
    // Dynamically connect to the same host as the frontend
    const protocol = window.location.protocol === 'https:' ? 'wss:' : 'ws:';
    const host = window.location.hostname;
    // Assume backend is on port 8000 of the same host
    const wsUrl = `${protocol}//${host}:8000/ws/logs`;

    console.log(`[Terminal] Connecting to ${wsUrl}...`);
    const socket = new WebSocket(wsUrl);

    socket.onopen = () => {
      console.log('Connected to Log Stream');
      addLog('SYSTEM', `Connected to Log Stream (${host}:8000)...`);
    };

    socket.onmessage = (event) => {
      try {
        const data = JSON.parse(event.data);
        setLogs((prev) => [...prev, data]);
      } catch (err) {
        console.error('Log Parse Error', err);
      }
    };

    socket.onerror = (error) => {
      console.error('WebSocket Error', error);
      addLog('ERROR', 'Connection Failed. Is Backend Running?');
    };

    socket.onclose = () => {
      addLog('SYSTEM', 'Disconnected from Stream.');
    };

    return () => {
      socket.close();
    };
  }, []);

  // Auto-scroll
  useEffect(() => {
    if (scrollRef.current) {
      scrollRef.current.scrollTop = scrollRef.current.scrollHeight;
    }
  }, [logs]);

  const addLog = (level: string, message: string) => {
    const ts = new Date().toLocaleTimeString();
    setLogs((prev) => [...prev, { timestamp: ts, level, message }]);
  };

  const getLevelColor = (level: string) => {
    switch (level?.toUpperCase()) {
      case 'INFO': return 'text-green-400';
      case 'WARNING': return 'text-yellow-400';
      case 'ERROR': return 'text-red-500';
      case 'SYSTEM': return 'text-cyan-400';
      default: return 'text-gray-300';
    }
  };

  return (
    <div className="bg-black/90 border border-gray-800 rounded-lg p-4 h-[500px] flex flex-col font-mono text-sm shadow-2xl backdrop-blur-md">
      <div className="flex justify-between items-center mb-2 border-b border-gray-800 pb-2">
        <span className="text-gray-400">⚡ LIVE_TERMINAL_FEED</span>
        <div className="flex gap-2">
           <span className="w-3 h-3 rounded-full bg-red-500/20"></span>
           <span className="w-3 h-3 rounded-full bg-yellow-500/20"></span>
           <span className="w-3 h-3 rounded-full bg-green-500"></span>
        </div>
      </div>
      
      <div ref={scrollRef} className="flex-1 overflow-y-auto space-y-1 scrollbar-hide">
        {logs.length === 0 && <span className="text-gray-600">Waiting for signals...</span>}
        {logs.map((log, i) => (
          <motion.div 
            initial={{ opacity: 0, x: -10 }}
            animate={{ opacity: 1, x: 0 }}
            key={i} 
            className="flex gap-3 hover:bg-white/5 p-0.5 rounded"
          >
            <span className="text-gray-500 shrink-0">[{log.timestamp}]</span>
            <span className={`font-bold shrink-0 w-20 ${getLevelColor(log.level)}`}>
              {log.level}
            </span>
            <span className="text-gray-300 break-all">{log.message}</span>
          </motion.div>
        ))}
      </div>
    </div>
  );
}
