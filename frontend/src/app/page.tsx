"use client";

import Terminal from "@/components/Terminal";
import MissionControl from "@/components/ControlPanel";
import SignalRadar from "@/components/SignalRadar";
import NewsFeed from "@/components/NewsFeed";
import DailySummary from "@/components/DailySummary";
import { Cpu } from "lucide-react";
import { useState, useEffect } from "react";
import {
  Panel,
  Group as PanelGroup,
  Separator as PanelResizeHandle,
  Layout,
} from "react-resizable-panels";
import {
  DndContext,
  DragEndEvent,
  PointerSensor,
  useSensor,
  useSensors,
} from "@dnd-kit/core";
import DraggableWidget from "@/components/DraggableWidget";
import DroppableSlot from "@/components/DroppableSlot";

// Widget IDs
const WIDGETS = {
  MISSION_CONTROL: "MISSION_CONTROL",
  SIGNAL_RADAR: "SIGNAL_RADAR",
  DAILY_SUMMARY: "DAILY_SUMMARY",
  TERMINAL: "TERMINAL",
  NEWS_FEED: "NEWS_FEED",
};

// Default mapping: slotId -> widgetId
const DEFAULT_WIDGET_POSITIONS = {
  "slot-mission-control": WIDGETS.MISSION_CONTROL,
  "slot-signal-radar": WIDGETS.SIGNAL_RADAR,
  "slot-daily-summary": WIDGETS.DAILY_SUMMARY,
  "slot-terminal": WIDGETS.TERMINAL,
  "slot-news-feed": WIDGETS.NEWS_FEED,
};

export default function Home() {
  const [mainLayout, setMainLayout] = useState<Layout | undefined>(undefined);
  const [sidebarLayout, setSidebarLayout] = useState<Layout | undefined>(
    undefined,
  );
  const [contentLayout, setContentLayout] = useState<Layout | undefined>(
    undefined,
  );
  const [terminalNewsLayout, setTerminalNewsLayout] = useState<
    Layout | undefined
  >(undefined);
  const [widgetPositions, setWidgetPositions] = useState<
    Record<string, string>
  >(DEFAULT_WIDGET_POSITIONS);
  const [hydrated, setHydrated] = useState(false);

  const sensors = useSensors(
    useSensor(PointerSensor, {
      activationConstraint: {
        distance: 5, // 5px movement required to start drag
      },
    }),
  );

  // Load layouts on mount
  useEffect(() => {
    const savedMain = localStorage.getItem("nifty-layout-main");
    const savedSidebar = localStorage.getItem("nifty-layout-sidebar");
    const savedContent = localStorage.getItem("nifty-layout-content");
    const savedTerminalNews = localStorage.getItem(
      "nifty-layout-terminal-news",
    );
    const savedPositions = localStorage.getItem("nifty-widget-positions");

    if (savedMain) {
      try {
        setMainLayout(JSON.parse(savedMain));
      } catch (e) {
        console.error("Failed to parse main layout", e);
      }
    }
    if (savedSidebar) {
      try {
        setSidebarLayout(JSON.parse(savedSidebar));
      } catch (e) {
        console.error("Failed to parse sidebar layout", e);
      }
    }
    if (savedContent) {
      try {
        setContentLayout(JSON.parse(savedContent));
      } catch (e) {
        console.error("Failed to parse content layout", e);
      }
    }
    if (savedTerminalNews) {
      try {
        setTerminalNewsLayout(JSON.parse(savedTerminalNews));
      } catch (e) {
        console.error("Failed to parse terminal-news layout", e);
      }
    }
    if (savedPositions) {
      try {
        setWidgetPositions(JSON.parse(savedPositions));
      } catch (e) {
        console.error("Failed to parse widget positions", e);
      }
    }

    setHydrated(true);
  }, []);

  const onLayoutChanged = (layout: Layout) => {
    localStorage.setItem("nifty-layout-main", JSON.stringify(layout));
  };

  const onSidebarLayoutChanged = (layout: Layout) => {
    localStorage.setItem("nifty-layout-sidebar", JSON.stringify(layout));
  };

  const onMainLayoutChanged = (layout: Layout) => {
    localStorage.setItem("nifty-layout-content", JSON.stringify(layout));
  };

  const onTerminalNewsLayoutChanged = (layout: Layout) => {
    localStorage.setItem("nifty-layout-terminal-news", JSON.stringify(layout));
  };

  const handleDragEnd = (event: DragEndEvent) => {
    const { active, over } = event;
    if (!over) return;

    const activeWidgetId = active.id as string;
    const overSlotId = over.id as string;

    // Find the slot that currently holds the active widget
    const activeSlotId = Object.keys(widgetPositions).find(
      (key) => widgetPositions[key] === activeWidgetId,
    );

    if (activeSlotId && activeSlotId !== overSlotId) {
      const newPositions = { ...widgetPositions };
      // Swap widgets between slots
      const widgetInOverSlot = widgetPositions[overSlotId];
      newPositions[overSlotId] = activeWidgetId;
      newPositions[activeSlotId] = widgetInOverSlot;

      setWidgetPositions(newPositions);
      localStorage.setItem(
        "nifty-widget-positions",
        JSON.stringify(newPositions),
      );
    }
  };

  const renderWidget = (widgetId: string) => {
    switch (widgetId) {
      case WIDGETS.MISSION_CONTROL:
        return (
          <DraggableWidget id={WIDGETS.MISSION_CONTROL} title="Mission Control">
            <MissionControl />
          </DraggableWidget>
        );
      case WIDGETS.SIGNAL_RADAR:
        return (
          <DraggableWidget id={WIDGETS.SIGNAL_RADAR} title="Signal Radar">
            <SignalRadar />
          </DraggableWidget>
        );
      case WIDGETS.DAILY_SUMMARY:
        return (
          <DraggableWidget id={WIDGETS.DAILY_SUMMARY} title="Daily Summary">
            <DailySummary />
          </DraggableWidget>
        );
      case WIDGETS.TERMINAL:
        return (
          <DraggableWidget id={WIDGETS.TERMINAL} title="Terminal">
            <Terminal />
          </DraggableWidget>
        );
      case WIDGETS.NEWS_FEED:
        return (
          <DraggableWidget id={WIDGETS.NEWS_FEED} title="News & Sentiment">
            <NewsFeed />
          </DraggableWidget>
        );
      default:
        return null;
    }
  };

  if (!hydrated) return null; // Prevent hydration mismatch

  return (
    <DndContext sensors={sensors} onDragEnd={handleDragEnd}>
      <main className="h-screen p-2 font-sans selection:bg-cyan-500/30 bg-[#050505] text-[#ededed] flex flex-col overflow-hidden">
        {/* Top Bar */}
        <header className="shrink-0 mb-4 flex justify-between items-end border-b border-white/5 pb-2">
          <div>
            <h1 className="text-2xl font-black tracking-tighter text-white flex items-center gap-2">
              <Cpu className="text-cyan-500 size-6" />
              NIFTY <span className="text-cyan-500 text-glow">
                COMMAND
              </span>{" "}
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

        {/* Main Resizable Layout */}
        <div className="flex-1 min-h-0">
          <PanelGroup
            id="main-group"
            orientation="horizontal"
            onLayoutChanged={onLayoutChanged}
            defaultLayout={mainLayout}
          >
            {/* Left Panel: Sidebar Widgets */}
            <Panel id="sidebar-panel" defaultSize={25} minSize={20}>
              <PanelGroup
                id="sidebar-vertical-group"
                orientation="vertical"
                onLayoutChanged={onSidebarLayoutChanged}
                defaultLayout={sidebarLayout}
              >
                <Panel id="sidebar-slot-1" defaultSize={40} minSize={15}>
                  <DroppableSlot id="slot-mission-control">
                    <div className="h-full overflow-y-auto pr-1 no-scrollbar">
                      {renderWidget(widgetPositions["slot-mission-control"])}
                    </div>
                  </DroppableSlot>
                </Panel>
                <PanelResizeHandle />
                <Panel id="sidebar-slot-2" defaultSize={60} minSize={15}>
                  <DroppableSlot id="slot-signal-radar">
                    <div className="h-full overflow-y-auto pr-1 no-scrollbar">
                      {renderWidget(widgetPositions["slot-signal-radar"])}
                    </div>
                  </DroppableSlot>
                </Panel>
              </PanelGroup>
            </Panel>

            <PanelResizeHandle />

            {/* Right Panel: Main Content */}
            <Panel id="content-panel" defaultSize={75} minSize={30}>
              <PanelGroup
                id="content-vertical-group"
                orientation="vertical"
                onLayoutChanged={onMainLayoutChanged}
                defaultLayout={contentLayout}
              >
                <Panel id="content-slot-1" defaultSize={30} minSize={10}>
                  <DroppableSlot id="slot-daily-summary">
                    <div className="h-full overflow-y-auto no-scrollbar">
                      {renderWidget(widgetPositions["slot-daily-summary"])}
                    </div>
                  </DroppableSlot>
                </Panel>
                <PanelResizeHandle />
                <Panel id="terminal-news-section" defaultSize={70} minSize={20}>
                  <PanelGroup
                    id="terminal-news-group"
                    orientation="horizontal"
                    onLayoutChanged={onTerminalNewsLayoutChanged}
                    defaultLayout={terminalNewsLayout}
                  >
                    <Panel id="content-slot-2" defaultSize={70} minSize={30}>
                      <DroppableSlot id="slot-terminal">
                        <div className="h-full">
                          {renderWidget(widgetPositions["slot-terminal"])}
                        </div>
                      </DroppableSlot>
                    </Panel>
                    <PanelResizeHandle />
                    <Panel id="content-slot-3" defaultSize={30} minSize={15}>
                      <DroppableSlot id="slot-news-feed">
                        <div className="h-full overflow-y-auto no-scrollbar">
                          {renderWidget(widgetPositions["slot-news-feed"])}
                        </div>
                      </DroppableSlot>
                    </Panel>
                  </PanelGroup>
                </Panel>
              </PanelGroup>
            </Panel>
          </PanelGroup>
        </div>
      </main>
    </DndContext>
  );
}
