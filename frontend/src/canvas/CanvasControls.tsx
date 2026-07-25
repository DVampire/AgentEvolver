import { Panel, useReactFlow, useStore } from '@xyflow/react';
import { ChevronUp, Maximize, Minus, Plus } from 'lucide-react';
import type { LucideIcon } from 'lucide-react';

import ShadTooltip from '../components/common/shadTooltipComponent';
import {
  DropdownMenu, DropdownMenuContent, DropdownMenuItem, DropdownMenuShortcut, DropdownMenuTrigger,
} from '../components/ui/dropdown-menu';

// Bottom-center controls pill copied from Langflow's CanvasControls: a
// horizontal rounded-lg bg-background bar (replacing React Flow's stock vertical
// Controls) with zoom out / a zoom-% dropdown / zoom in / fit view. Button
// styling mirrors Langflow's CanvasControlButton hover treatment.
function ControlButton({ icon: Icon, label, onClick, disabled }: { icon: LucideIcon; label: string; onClick: () => void; disabled?: boolean }) {
  return (
    <ShadTooltip content={label}>
      <button
        type="button"
        aria-label={label}
        disabled={disabled}
        onClick={onClick}
        className="group flex h-8 w-8 items-center justify-center rounded-md hover:bg-muted disabled:cursor-not-allowed disabled:opacity-50"
      >
        <Icon className="h-[18px] w-[18px] text-muted-foreground transition-colors group-hover:text-foreground" />
      </button>
    </ShadTooltip>
  );
}

export function CanvasControls() {
  const { zoomIn, zoomOut, fitView, zoomTo } = useReactFlow();
  const zoom = useStore((state) => state.transform[2]);
  return (
    <Panel
      position="bottom-center"
      data-testid="main_canvas_controls"
      className="react-flow__controls !m-4 flex !flex-row items-center gap-1 !overflow-visible rounded-lg border border-border bg-background p-1 text-primary shadow-md [&>button]:border-0"
    >
      <ControlButton icon={Minus} label="Zoom out" onClick={() => zoomOut()} />
      <DropdownMenu>
        <DropdownMenuTrigger asChild>
          <button
            type="button"
            title="Zoom"
            className="group flex h-8 items-center justify-center gap-1 rounded-md px-0.5 hover:bg-muted"
          >
            <span className="w-11 pl-1.5 text-left text-sm tabular-nums text-muted-foreground group-hover:text-foreground">
              {Math.round(zoom * 100)}%
            </span>
            <ChevronUp className="h-5 w-5 text-muted-foreground group-hover:text-foreground" />
          </button>
        </DropdownMenuTrigger>
        <DropdownMenuContent side="top" align="center" className="w-44">
          <DropdownMenuItem onClick={() => zoomIn()}>Zoom in<DropdownMenuShortcut>+</DropdownMenuShortcut></DropdownMenuItem>
          <DropdownMenuItem onClick={() => zoomOut()}>Zoom out<DropdownMenuShortcut>-</DropdownMenuShortcut></DropdownMenuItem>
          <DropdownMenuItem onClick={() => zoomTo(1)}>Zoom to 100%<DropdownMenuShortcut>0</DropdownMenuShortcut></DropdownMenuItem>
          <DropdownMenuItem onClick={() => fitView()}>Fit view<DropdownMenuShortcut>1</DropdownMenuShortcut></DropdownMenuItem>
        </DropdownMenuContent>
      </DropdownMenu>
      <ControlButton icon={Plus} label="Zoom in" onClick={() => zoomIn()} />
      <ControlButton icon={Maximize} label="Fit view" onClick={() => fitView()} />
    </Panel>
  );
}
