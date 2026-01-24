'use client';

import React, { useEffect, useState, useRef } from 'react';
import { motion } from 'framer-motion';
import { Terminal as TerminalIcon, Minimize2, Maximize2 } from 'lucide-react';
import Card from './ui/Card';

interface LogMessage {
  timestamp: string;
  level: string;
  message: string;
}

export default function Terminal() {
  const [logs, setLogs] = useState<LogMessage[]>([]);
  const scrollRef = useRef<HTMLDivElement>(null);

  useEffect(() => {
    const protocol = window.location.protocol === 'https:' ? 'wss:' : 'ws:';
    const host = window.location.hostname;
    // Assume backend is on port 8000 of the same host
    const wsUrl = `${protocol}//${host}:8000/ws/logs`;

    console.log(`[Terminal] Connecting to ${wsUrl}...`);
    const socket = new WebSocket(wsUrl);

    socket.onopen = () => {
      addLog('SYSTEM', `Uplink Established to ${host}:8000...`);
    };

    socket.onmessage = (event) => {
      try {
        const data = JSON.parse(event.data);
        if (typeof data === 'object') {
            setLogs((prev) => [...prev, data].slice(-100)); // Keep last 100 logs
        }
      } catch (err) {
        // Ignore parse errors from heartbeat
      }
    };

    socket.onerror = (error) => {
        // Silent error
    };

    socket.onclose = () => {
      addLog('SYSTEM', 'Uplink Lost. Reconnecting...');
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

  return (
    <Card className="h-full flex flex-col p-0 overflow-hidden min-h-[500px]" glow={true}>
      
      {/* CRT Scanline Effect Overlay */}
      <div className="crt-scanline pointer-events-none"></div>

      {/* Terminal Header */}
      <div className="flex justify-between items-center px-4 py-3 bg-white/5 border-b border-white/5">
        <div className="flex items-center gap-2 text-cyan-400">
           <TerminalIcon size={14} />
           <span className="text-xs font-mono tracking-widest uppercase">Live_Feed_V2.log</span>
        </div>
        <div className="flex gap-2">
           <span className="w-2 h-2 rounded-full bg-red-500/50"></span>
           <span className="w-2 h-2 rounded-full bg-yellow-500/50"></span>
           <span className="w-2 h-2 rounded-full bg-green-500/50"></span>
        </div>
      </div>
      
      {/* Logs Area */}
      <div ref={scrollRef} className="flex-1 overflow-y-auto p-4 font-mono text-xs space-y-1 relative">
        {logs.length === 0 && (
            <div className="absolute inset-0 flex items-center justify-center text-gray-700 uppercase tracking-widest animate-pulse">
                Waiting for signal transmission...
            </div>
        )}
        
        {logs.map((log, i) => (
          <div key={i} className="flex gap-3 hover:bg-white/5 p-0.5 rounded transition-colors group">
            <span className="text-gray-600 shrink-0 select-none group-hover:text-gray-400">
                {log.timestamp}
            </span>
            
            <span className={`font-bold shrink-0 w-16 ${
                log.level === 'INFO' ? 'text-green-400' :
                log.level === 'WARNING' ? 'text-yellow-400' :
                log.level === 'ERROR' ? 'text-red-500' :
                'text-cyan-400'
            }`}>
              {log.level}
            </span>
            
            <span className={`break-all ${
                log.message.includes("PROFIT") ? "text-green-300 text-glow" : 
                log.message.includes("LOSS") ? "text-red-300" : 
                "text-gray-300"
            }`}>
                {log.message}
            </span>
          </div>
        ))}
        
        {/* Blinking Cursor at bottom */}
        <div className="h-4 w-2 bg-green-400 animate-pulse mt-2"></div>
      </div>
    </Card>
  );
}
