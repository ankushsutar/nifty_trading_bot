'use client';
import React, { useEffect, useState } from 'react';
import { TrendingUp, TrendingDown, DollarSign, Activity } from 'lucide-react';
import { motion } from 'framer-motion';

interface TradeDetails {
    symbol: string;
    leg: string;
    qty: number;
    entry_price: number;
    sl_price: number;
    token: string;
}

export default function TradePanel() {
  const [active, setActive] = useState(false);
  const [trade, setTrade] = useState<TradeDetails | null>(null);

  useEffect(() => {
    const interval = setInterval(() => {
        fetch('http://localhost:8000/api/trade')
            .then(res => res.json())
            .then(data => {
                if (data.active) {
                    setActive(true);
                    setTrade(data.details);
                } else {
                    setActive(false);
                    setTrade(null);
                }
            })
            .catch(e => console.error("Poll error", e));
    }, 2000); // 2s polling

    return () => clearInterval(interval);
  }, []);

  if (!active || !trade) {
      return (
        <div className="bg-gray-900 border border-gray-800 p-6 rounded-xl shadow-lg flex flex-col items-center justify-center opacity-50 h-[300px]">
            <Activity size={48} className="text-gray-700 mb-2"/>
            <span className="text-gray-500 font-mono text-sm tracking-widest">NO ACTIVE TRADES</span>
            <span className="text-xs text-gray-700 mt-1">Scanning market...</span>
        </div>
      );
  }

  const isCE = trade.leg === 'CE';

  return (
    <motion.div 
        initial={{ opacity: 0, scale: 0.95 }}
        animate={{ opacity: 1, scale: 1 }}
        className="bg-gray-900 border border-blue-500/30 p-6 rounded-xl shadow-lg relative overflow-hidden"
    >
        <div className="absolute top-0 right-0 p-2 opacity-10">
            {isCE ? <TrendingUp size={120} /> : <TrendingDown size={120} />}
        </div>

        <h2 className="text-sm font-bold text-gray-400 uppercase tracking-widest mb-4 flex items-center gap-2">
            <span className="w-2 h-2 rounded-full bg-blue-500 animate-pulse"></span>
            Live Position
        </h2>

        <div className="mb-6">
            <div className="text-2xl font-black text-white truncate">{trade.symbol}</div>
            <div className={`text-sm font-bold px-2 py-0.5 rounded inline-block mt-1 ${isCE ? 'bg-green-500/20 text-green-400' : 'bg-red-500/20 text-red-500'}`}>
                {trade.leg} (CALL/PUT)
            </div>
        </div>

        <div className="grid grid-cols-2 gap-4 mb-6">
             <div className="bg-gray-800/50 p-3 rounded border border-gray-700">
                 <span className="text-xs text-gray-500 block">Entry Price</span>
                 <span className="text-xl font-mono text-white">₹{trade.entry_price.toFixed(2)}</span>
             </div>
             <div className="bg-gray-800/50 p-3 rounded border border-gray-700">
                 <span className="text-xs text-gray-500 block">Quantity</span>
                 <span className="text-xl font-mono text-white">{trade.qty}</span>
             </div>
        </div>

        {/* P/L Placeholder - Since we don't have realtime LTP yet */}
        <div className="border-t border-gray-800 pt-4">
             <div className="flex justify-between items-end">
                 <span className="text-gray-400 text-sm">Invested Value</span>
                 <span className="text-white font-mono">₹{(trade.entry_price * trade.qty).toFixed(2)}</span>
             </div>
        </div>
        
    </motion.div>
  );
}
