import { TrendingUp, Activity, DollarSign, Crosshair } from 'lucide-react';
import Card from './ui/Card';

export default function SignalRadar() {
  // Mock Data for aesthetics - in proper implementation this would be props or fetched
  const metrics = [
    { label: "Daily P&L", value: "₹0.00", change: "+0%", icon: <DollarSign size={16}/>, color: "text-gray-400" },
    { label: "India VIX", value: "12.45", change: "-1.2%", icon: <Activity size={16}/>, color: "text-red-400" },
    { label: "Nifty 50", value: "22,450", change: "+0.45%", icon: <TrendingUp size={16}/>, color: "text-green-400" },
  ];

  return (
    <Card title="Market Telemetry" icon={<Crosshair size={18} />}>
        <div className="grid grid-cols-3 gap-4">
            {metrics.map((m, i) => (
                <div key={i} className="bg-black/40 border border-white/5 p-4 rounded-lg flex flex-col items-center justify-center relative overflow-hidden group">
                    <div className={`absolute inset-0 opacity-0 group-hover:opacity-10 bg-gradient-to-t from-${m.color.split('-')[1]}-500 to-transparent transition-opacity`} />
                    
                    <span className="text-xs text-gray-500 uppercase tracking-widest mb-1">{m.label}</span>
                    <span className={`text-xl font-black ${m.color} text-glow mb-1`}>{m.value}</span>
                    <span className="text-[10px] text-gray-600 font-mono">{m.change}</span>
                </div>
            ))}
        </div>
        
        {/* Visualizer Placeholder */}
        <div className="mt-6 h-32 bg-black/40 border border-white/5 rounded-lg flex items-center justify-center relative overflow-hidden">
             {/* Fake Scanning Line */}
             <div className="absolute top-0 bottom-0 w-[2px] bg-cyan-500/50 blur-[2px] animate-[scan_2s_linear_infinite]" style={{ left: '50%' }}></div>
             <div className="text-xs text-gray-700 font-mono">SIGNAL_VISUALIZER_OFFLINE</div>
        </div>
    </Card>
  );
}
