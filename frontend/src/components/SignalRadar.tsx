'use client';

import React, { useEffect, useState } from 'react';
import { TrendingUp, Activity, DollarSign, Crosshair } from 'lucide-react';
import Card from './ui/Card';

interface MarketData {
    nifty: number;
    vix: number;
    pnl: number;
    sentiment?: { score: number };
}

export default function SignalRadar() {
  const [data, setData] = useState<MarketData>({ nifty: 0, vix: 0, pnl: 0 });
  const [loading, setLoading] = useState(true);

  // Poll for Data
  useEffect(() => {
    const fetchData = async () => {
        try {
            // Fetch Market Data
            const res = await fetch('http://localhost:8000/api/market-data');
            const json = await res.json();
            
            // Fetch Sentiment
            const resSent = await fetch('http://localhost:8000/api/sentiment');
            const jsonSent = await resSent.json();

            setData({
                nifty: json.nifty || 0,
                vix: json.vix || 0,
                pnl: json.pnl || 0,
                sentiment: jsonSent
            });
            setLoading(false);
        } catch (e) {
            console.error(e);
        }
    };

    fetchData(); // Initial
    const interval = setInterval(fetchData, 2000); // Poll every 2s
    return () => clearInterval(interval);
  }, []);

  const sentimentScore = data.sentiment?.score || 0;

  const metrics = [
    { label: "Daily P&L", value: `₹${data.pnl.toFixed(2)}`, change: "---", icon: <DollarSign size={16}/>, color: data.pnl >= 0 ? "text-green-400" : "text-red-400" },
    { label: "India VIX", value: data.vix.toFixed(2), change: "", icon: <Activity size={16}/>, color: "text-red-400" },
    { label: "Nifty 50", value: data.nifty.toLocaleString('en-IN'), change: "", icon: <TrendingUp size={16}/>, color: "text-cyan-400" },
    { label: "Sentiment", value: `${(sentimentScore * 100).toFixed(0)}%`, change: sentimentScore > 0 ? "BULLISH" : "BEARISH", icon: <Activity size={16}/>, color: sentimentScore > 0 ? "text-green-400" : "text-red-400" },
  ];

  return (
    <Card title="Market Telemetry" icon={<Crosshair size={18} />}>
        {loading ? (
             <div className="h-24 flex items-center justify-center text-xs text-gray-600 animate-pulse">
                 ESTABLISHING SATELLITE LINK...
             </div>
        ) : (
            <div className="grid grid-cols-2 gap-4">
                {metrics.map((m, i) => (
                    <div key={i} className="bg-black/40 border border-white/5 p-4 rounded-lg flex flex-col items-center justify-center relative overflow-hidden group">
                        <div className={`absolute inset-0 opacity-0 group-hover:opacity-10 bg-gradient-to-t from-${m.color.split('-')[1]}-500 to-transparent transition-opacity`} />
                        
                        <span className="text-xs text-gray-500 uppercase tracking-widest mb-1">{m.label}</span>
                        <span className={`text-xl font-black ${m.color} text-glow mb-1`}>{m.value}</span>
                        {m.change && <span className="text-[10px] text-gray-600 font-mono">{m.change}</span>}
                    </div>
                ))}
            </div>
        )}
        
        {/* Visualizer Placeholder */}
        <div className="mt-6 h-32 bg-black/40 border border-white/5 rounded-lg flex items-center justify-center relative overflow-hidden">
             {/* Fake Scanning Line */}
             <div className="absolute top-0 bottom-0 w-[2px] bg-cyan-500/50 blur-[2px] animate-[scan_2s_linear_infinite]" style={{ left: '50%' }}></div>
             <div className="text-xs text-gray-700 font-mono">SIGNAL_VISUALIZER (WIP)</div>
        </div>
    </Card>
  );
}
