import Terminal from '@/components/Terminal';
import MissionControl from '@/components/ControlPanel';
import SignalRadar from '@/components/SignalRadar';
import { Cpu } from 'lucide-react';

export default function Home() {
  return (
    <main className="min-h-screen p-6 font-sans selection:bg-cyan-500/30">
      
      {/* Top Bar */}
      <header className="mb-8 flex justify-between items-end border-b border-white/5 pb-4">
        <div>
          <h1 className="text-4xl font-black tracking-tighter text-white flex items-center gap-3">
             <Cpu className="text-cyan-500" />
             NIFTY <span className="text-cyan-500 text-glow">COMMAND</span> CENTER
          </h1>
          <p className="text-gray-600 mt-1 uppercase tracking-[0.2em] text-xs font-mono ml-11">
            Algorithmic Trading System v3.0
          </p>
        </div>
        
        <div className="flex items-center gap-2 text-[10px] font-mono text-gray-500 uppercase tracking-widest">
           <span className="w-1.5 h-1.5 bg-green-500 rounded-full animate-pulse shadow-[0_0_10px_#00ff00]"></span>
           SERVER_UPLINK_ESTABLISHED
        </div>
      </header>

      {/* Main Grid */}
      <div className="grid grid-cols-1 lg:grid-cols-12 gap-6">
        
        {/* Left Col: Mission Control (3 cols) */}
        <div className="lg:col-span-4 space-y-6">
          <MissionControl />
          <SignalRadar />
        </div>

        {/* Right Col: Terminal (9 cols) */}
        <div className="lg:col-span-8 h-full min-h-[500px]">
          <Terminal />
        </div>

      </div>
    </main>
  );
}
