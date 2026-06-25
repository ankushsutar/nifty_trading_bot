"use client";

import React, { useEffect, useState } from "react";
import {
  Activity,
  ArrowUpRight,
  ArrowDownRight,
  DollarSign,
} from "lucide-react";
import { API_URL } from "../config";

interface Trade {
  id: number;
  symbol: string;
  token: string;
  leg: string;
  side: string;
  qty: number;
  entry_price: number;
  exit_price?: number;
  pnl?: number;
  current_pnl?: number;
  status: string;
  exit_reason?: string;
  created_at: string;
}

interface DailySummaryData {
  daily_pnl: number;
  realized_pnl: number;
  unrealized_pnl: number;
  trades: Trade[];
  mode: string;
}

export default function DailySummary() {
  const [data, setData] = useState<DailySummaryData | null>(null);
  const [loading, setLoading] = useState(true);

  const fetchData = async () => {
    try {
      const res = await fetch(`${API_URL}/api/daily-summary`);
      if (res.ok) {
        const json = await res.json();
        setData(json);
      }
    } catch (error) {
      console.error("Failed to fetch daily summary", error);
    } finally {
      setLoading(false);
    }
  };

  useEffect(() => {
    fetchData();
    const interval = setInterval(fetchData, 5000); // Refresh every 5s
    return () => clearInterval(interval);
  }, []);

  if (loading && !data)
    return (
      <div className="text-gray-500 animate-pulse text-xs">Loading P&L...</div>
    );

  const pnlColor = (pnl: number) => {
    if (pnl > 0) return "text-emerald-400";
    if (pnl < 0) return "text-rose-400";
    return "text-gray-400";
  };

  return (
    <div className="h-[500px] bg-black/40 backdrop-blur-md border border-white/10 rounded-lg p-2 flex flex-col gap-2 font-mono">
      {/* Header Stats */}
      <div className="flex items-center justify-between border-b border-white/5 pb-4">
        <div className="flex items-center gap-2">
          <Activity className="size-5 text-cyan-500" />
          <div>
            <h2 className="text-sm font-bold text-gray-200 tracking-wider">
              DAILY PERFORMANCE
            </h2>
            <div
              className={`text-[9px] uppercase font-bold tracking-widest ${data?.mode === "LIVE" ? "text-red-500 animate-pulse" : "text-yellow-500"}`}
            >
              {data?.mode === "LIVE" ? "🔴 LIVE" : "⚡ SIMULATION"}
            </div>
          </div>
        </div>

        <div className={`text-xl font-black ${pnlColor(data?.daily_pnl || 0)}`}>
          <span className="text-[10px] text-gray-500 mr-2 font-normal">
            NET P&L
          </span>
          {data?.daily_pnl ? data.daily_pnl.toFixed(2) : "0.00"}
        </div>
      </div>

      <div className="grid grid-cols-2 gap-2 shrink-0">
        <div className="bg-white/5 rounded p-2 border border-white/5">
          <div className="text-[10px] text-gray-500 mb-0.5">REALIZED P&L</div>
          <div
            className={`text-lg font-bold ${pnlColor(data?.realized_pnl || 0)}`}
          >
            {data?.realized_pnl?.toFixed(2)}
          </div>
        </div>
        <div className="bg-white/5 rounded p-2 border border-white/5">
          <div className="text-[10px] text-gray-500 mb-0.5">UNREALIZED P&L</div>
          <div
            className={`text-lg font-bold ${pnlColor(data?.unrealized_pnl || 0)}`}
          >
            {data?.unrealized_pnl?.toFixed(2)}
          </div>
        </div>
      </div>

      {/* Trades List */}
      <div className="flex-1 overflow-auto min-h-0 bg-black/20 rounded border border-white/5 no-scrollbar">
        <table className="w-full text-left text-[10px] text-gray-400">
          <thead className="text-gray-500 sticky top-0 bg-[#050505] z-10">
            <tr className="border-b border-white/10">
              <th className="py-2">TIME</th>
              <th className="py-2">SYMBOL</th>
              <th className="py-2">SIDE</th>
              <th className="py-2 text-right">QTY</th>
              <th className="py-2 text-right">ENTRY</th>
              <th className="py-2 text-right">EXIT</th>
              <th className="py-2 text-right">P&L</th>
              <th className="py-2">STATUS</th>
            </tr>
          </thead>
          <tbody className="divide-y divide-white/5">
            {data?.trades.map((trade) => {
              const pnl =
                trade.status === "CLOSED" ? trade.pnl : trade.current_pnl;
              const isWin = (pnl || 0) > 0;
              return (
                <tr
                  key={trade.id}
                  className="hover:bg-white/5 transition-colors"
                >
                  <td className="py-2 opacity-60">
                    {new Date(trade.created_at + "Z").toLocaleTimeString([], {
                      hour: "2-digit",
                      minute: "2-digit",
                    })}
                  </td>
                  <td className="py-2 font-bold text-white">{trade.symbol}</td>
                  <td className="py-2">
                    <span
                      className={`px-1.5 py-0.5 rounded ${trade.leg === "CE" ? "bg-green-500/20 text-green-400" : "bg-red-500/20 text-red-400"}`}
                    >
                      {trade.leg}
                    </span>
                  </td>
                  <td className="py-2 text-right">{trade.qty}</td>
                  <td className="py-2 text-right">
                    {trade.entry_price.toFixed(1)}
                  </td>
                  <td className="py-2 text-right text-gray-500">
                    {trade.exit_price ? trade.exit_price.toFixed(1) : "-"}
                  </td>
                  <td
                    className={`py-2 text-right font-bold ${pnlColor(pnl || 0)}`}
                  >
                    {pnl ? pnl.toFixed(1) : "-"}
                  </td>
                  <td className="py-2">
                    <span
                      className={`text-[10px] uppercase tracking-wider ${trade.status === "OPEN" ? "text-yellow-400 animate-pulse" : "text-gray-500"}`}
                    >
                      {trade.status}
                    </span>
                  </td>
                </tr>
              );
            })}
            {(!data?.trades || data.trades.length === 0) && (
              <tr>
                <td
                  colSpan={8}
                  className="py-8 text-center text-gray-600 italic"
                >
                  No trades recorded today.
                </td>
              </tr>
            )}
          </tbody>
        </table>
      </div>
    </div>
  );
}
