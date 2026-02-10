import Terminal from "@/components/Terminal";
import MissionControl from "@/components/ControlPanel";
import SignalRadar from "@/components/SignalRadar";
import NewsFeed from "@/components/NewsFeed";
import DailySummary from "@/components/DailySummary";
import { Cpu } from "lucide-react";

export default function Home() {
  return (
    <main className="min-h-screen p-2 font-sans selection:bg-cyan-500/30 bg-[#050505] text-[#ededed] flex flex-col">
      {/* Top Bar */}
      <header className="mb-4 flex justify-between items-end border-b border-white/5 pb-2">
        <div>
          <h1 className="text-2xl font-black tracking-tighter text-white flex items-center gap-2">
            <Cpu className="text-cyan-500 size-6" />
            NIFTY <span className="text-cyan-500 text-glow">COMMAND</span>{" "}
            CENTER
          </h1>
          <p className="text-gray-600 mt-0.5 uppercase tracking-[0.2em] text-[10px] font-mono ml-8">
            Algorithmic Trading System v3.0
          </p>
        </div>

        <div className="flex items-center gap-2 text-[10px] font-mono text-gray-500 uppercase tracking-widest">
          <span className="w-1.5 h-1.5 bg-green-500 rounded-full animate-pulse shadow-[0_0_10px_#00ff00]"></span>
          SERVER_UPLINK_ESTABLISHED
        </div>
      </header>

      {/* Main Grid */}
      <div className="grid grid-cols-1 lg:grid-cols-12 gap-2">
        {/* Left Col: Mission Control (3 cols) */}
        <div className="lg:col-span-3 space-y-2 flex flex-col">
          <MissionControl />
          <SignalRadar />
          <NewsFeed />
        </div>

        {/* Right Col: Terminal & P&L (9 cols) */}
        <div className="lg:col-span-9 flex flex-col gap-2">
          <DailySummary />
          <div className="h-[500px] shrink-0">
            <Terminal />
          </div>
        </div>
      </div>
    </main>
  );
}
