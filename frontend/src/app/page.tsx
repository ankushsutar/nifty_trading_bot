import Terminal from '@/components/Terminal';
import ControlPanel from '@/components/ControlPanel';

export default function Home() {
  return (
    <main className="min-h-screen bg-black text-gray-100 p-8 font-sans selection:bg-green-500/30">
      
      {/* Header */}
      <header className="mb-8 flex flex-col md:flex-row justify-between items-start md:items-end border-b border-gray-800 pb-4 gap-4">
        <div>
          <h1 className="text-4xl font-black tracking-tighter text-white">
            NIFTY <span className="text-blue-500">COMMAND</span> CENTER
          </h1>
          <p className="text-gray-500 mt-1 uppercase tracking-widest text-xs">
            Algorithmic Trading System v2.1
          </p>
        </div>
        
        {/* Connection Status Indicator */}
        <div className="flex items-center gap-2 text-xs font-mono text-gray-500">
           <span className="w-2 h-2 bg-green-500 rounded-full animate-pulse"></span>
           SERVER CONNECTED
        </div>
      </header>

      {/* Grid Layout */}
      <div className="grid grid-cols-1 lg:grid-cols-3 gap-8">
        
        {/* Left Column: Controls & Stats */}
        <div className="lg:col-span-1 space-y-6">
          <ControlPanel />
          
          {/* Instructions Card for New Users */}
          <div className="bg-gray-900 border border-gray-800 p-6 rounded-xl shadow-lg">
            <h3 className="text-white font-bold mb-3 flex items-center gap-2">
                <span className="bg-blue-500/20 text-blue-400 w-6 h-6 rounded-full flex items-center justify-center text-xs">?</span>
                Quick Guide
            </h3>
            <ul className="text-sm text-gray-400 space-y-2 list-disc list-inside">
                <li>Select <span className="text-yellow-400">Dry Run</span> to test without real money.</li>
                <li>Click <span className="text-green-400">START BOT</span> to begin scanning.</li>
                <li>Watch the <span className="text-gray-300">Terminal</span> for live signals.</li>
                <li>Bot auto-stops at 3:15 PM.</li>
            </ul>
          </div>
        </div>

        {/* Right Column: Terminal */}
        <div className="lg:col-span-2">
          <Terminal />
        </div>

      </div>
    </main>
  );
}
