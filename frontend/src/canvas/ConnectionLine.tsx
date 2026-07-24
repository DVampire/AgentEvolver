import type { ConnectionLineComponentProps } from '@xyflow/react';

/** In-progress connection line, ported from Langflow's
 * ConnectionLineComponent: an animated cubic path ending in a ringed dot. */
export default function ConnectionLine({ fromX, fromY, toX, toY, connectionLineStyle = {} }: ConnectionLineComponentProps) {
  return (
    <g>
      <path
        fill="none"
        strokeWidth={2}
        className="canvas-connection-line"
        style={connectionLineStyle}
        d={`M${fromX},${fromY} C ${fromX} ${toY} ${fromX} ${toY} ${toX},${toY}`}
      />
      <circle cx={toX} cy={toY} r={5} strokeWidth={1.5} className="canvas-connection-dot" />
    </g>
  );
}
