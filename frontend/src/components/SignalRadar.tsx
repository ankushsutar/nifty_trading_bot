"use client";

import React, { useEffect, useState, useRef } from "react";
import {
  TrendingUp,
  Activity,
  DollarSign,
  Crosshair,
  Radar,
} from "lucide-react";
import Card from "./ui/Card";

interface Analysis {
  ema9: number;
  ema21: number;
  rsi: number;
  adx: number;
  atr: number;
  regime: string;
  htf_trend: string;
}

interface MarketData {
  nifty: number;
  vix: number;
  pnl: number;
  sentiment?: { score: number };
  analysis?: Analysis;
}

export default function SignalRadar() {
  const [data, setData] = useState<MarketData>({ nifty: 0, vix: 0, pnl: 0 });
  const [loading, setLoading] = useState(true);

  // Poll for Data
  useEffect(() => {
    const fetchData = async () => {
      try {
        const res = await fetch("http://localhost:8000/api/market-data");
        const json = await res.json();

        const resSent = await fetch("http://localhost:8000/api/sentiment");
        const jsonSent = await resSent.json();

        setData({
          nifty: json.nifty || 0,
          vix: json.vix || 0,
          pnl: json.pnl || 0,
          sentiment: jsonSent,
          analysis: json.analysis,
        });
        setLoading(false);
      } catch (e) {
        console.error(e);
      }
    };

    fetchData();
    const interval = setInterval(fetchData, 2000);
    return () => clearInterval(interval);
  }, []);

  const sentimentScore = data.sentiment?.score || 0;
  const analysis = data.analysis;

  // Helper to determine regime color
  const getRegimeColor = (regime?: string) => {
    if (regime === "TRENDING") return "text-green-400";
    if (regime === "VOLATILE") return "text-red-400";
    return "text-yellow-400";
  };

  const metrics = [
    {
      label: "Daily P&L",
      value: `₹${data.pnl.toFixed(2)}`,
      change: "---",
      icon: <DollarSign size={16} />,
      color: data.pnl >= 0 ? "text-green-400" : "text-red-400",
    },
    {
      label: "India VIX",
      value: data.vix.toFixed(2),
      change: "",
      icon: <Activity size={16} />,
      color: "text-red-400",
    },
    {
      label: "Nifty 50",
      value: data.nifty.toLocaleString("en-IN"),
      change: "",
      icon: <TrendingUp size={16} />,
      color: "text-cyan-400",
    },
    {
      label: "Sentiment",
      value: `${(sentimentScore * 100).toFixed(0)}%`,
      change: sentimentScore > 0 ? "BULLISH" : "BEARISH",
      icon: <Activity size={16} />,
      color: sentimentScore > 0 ? "text-green-400" : "text-red-400",
    },
  ];

  return (
    <Card title="Market Telemetry" icon={<Crosshair size={18} />}>
      {loading ? (
        <div className="h-[200px] flex items-center justify-center text-xs text-gray-600 animate-pulse">
          ESTABLISHING SATELLITE LINK...
        </div>
      ) : (
        <div className="flex flex-col gap-3">
          {/* Top Metrics Grid */}
          <div className="grid grid-cols-2 gap-2">
            {metrics.map((m, i) => (
              <div
                key={i}
                className="bg-black/40 border border-white/5 p-2 rounded flex flex-col items-center justify-center relative overflow-hidden group h-16"
              >
                <div
                  className={`absolute inset-0 opacity-0 group-hover:opacity-10 bg-gradient-to-t from-${m.color.split("-")[1]}-500 to-transparent transition-opacity`}
                />

                <span className="text-[9px] text-gray-500 uppercase tracking-widest mb-0.5">
                  {m.label}
                </span>
                <span
                  className={`text-lg font-bold ${m.color} text-glow leading-none`}
                >
                  {m.value}
                </span>
                {m.change && (
                  <span className="text-[8px] text-gray-600 font-mono mt-0.5">
                    {m.change}
                  </span>
                )}
              </div>
            ))}
          </div>

          {/* Tactical Display (Analysis) */}
          <div className="h-[140px] bg-black/60 border border-white/10 rounded relative overflow-hidden flex items-center justify-between px-4">
            {/* Background Grid & Scanline */}
            <div
              className="absolute inset-0 z-0 opacity-20 pointer-events-none"
              style={{
                backgroundImage:
                  "radial-gradient(circle, #333 1px, transparent 1px)",
                backgroundSize: "20px 20px",
              }}
            ></div>
            <div
              className="absolute top-0 bottom-0 w-[2px] bg-cyan-500/30 blur-[4px] animate-[scan_3s_linear_infinite] z-0"
              style={{ left: "50%" }}
            ></div>

            {/* Left: RSI Gauge */}
            <div className="relative z-10 flex flex-col items-center">
              <div className="w-20 h-10 overflow-hidden relative">
                {/* Gauge Background */}
                <div className="w-20 h-20 rounded-full border-[3px] border-white/10 border-b-0 absolute top-0 left-0"></div>
                {/* Needle Logic (Simple Rotation) */}
                <div
                  className="w-20 h-20 rounded-full border-[3px] border-transparent border-t-cyan-500 absolute top-0 left-0 transition-all duration-1000 ease-out"
                  style={{
                    transform: `rotate(${(analysis?.rsi || 50) * 1.8 - 90}deg)`,
                  }} // Map 0-100 to -90 to 90? No, 0-100 to 0-180 (starts at left)
                  // Correction: 0 should be -90deg (flat left), 50 is 0deg (up), 100 is 90deg (flat right)
                ></div>
              </div>
              <span className="text-[10px] text-gray-500 mt-1 font-mono">
                RSI
              </span>
              <span
                className={`text-xl font-bold font-mono ${analysis?.rsi && analysis.rsi > 70 ? "text-red-400" : analysis?.rsi && analysis.rsi < 30 ? "text-green-400" : "text-cyan-400"}`}
              >
                {analysis?.rsi?.toFixed(0) || "--"}
              </span>
            </div>

            {/* Center: Regime */}
            <div className="relative z-10 flex flex-col items-center">
              <span className="text-[9px] text-gray-500 uppercase tracking-[0.2em] mb-1">
                MARKET REGIME
              </span>
              <div
                className={`text-lg font-black tracking-widest ${getRegimeColor(analysis?.regime)} text-glow animate-pulse`}
              >
                {analysis?.regime || "ANALYZING"}
              </div>
              <div className="mt-2 flex gap-1">
                <span className="text-[8px] px-1 py-0.5 rounded bg-white/5 border border-white/5 text-gray-400">
                  ATR: {analysis?.atr?.toFixed(1) || "-"}
                </span>
                <span className="text-[8px] px-1 py-0.5 rounded bg-white/5 border border-white/5 text-gray-400">
                  HTF: {analysis?.htf_trend || "-"}
                </span>
              </div>
            </div>

            {/* Right: ADX Bar */}
            <div className="relative z-10 flex flex-col items-center w-16">
              <div className="h-12 w-3 bg-white/10 rounded-full relative overflow-hidden">
                <div
                  className="absolute bottom-0 w-full bg-gradient-to-t from-cyan-900 to-cyan-400 transition-all duration-1000"
                  style={{ height: `${Math.min(100, analysis?.adx || 0)}%` }}
                ></div>
              </div>
              <span className="text-[10px] text-gray-500 mt-2 font-mono">
                ADX
              </span>
              <span className="text-sm font-bold text-cyan-400 font-mono">
                {analysis?.adx?.toFixed(0) || "--"}
              </span>
            </div>
          </div>
        </div>
      )}
    </Card>
  );
}
