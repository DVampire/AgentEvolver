import { memo } from 'react';
import { BaseEdge, getBezierPath, type EdgeProps } from '@xyflow/react';

/** Default bezier edge, ported (simplified) from langflow's
 * CustomEdges/DefaultEdge: BaseEdge over getBezierPath with selection styling
 * (their context-menu/i18n layers are omitted). */
export const DefaultEdge = memo(function DefaultEdge({
  sourceX, sourceY, targetX, targetY, sourcePosition, targetPosition, selected, markerEnd, style,
}: EdgeProps) {
  const [path] = getBezierPath({ sourceX, sourceY, targetX, targetY, sourcePosition, targetPosition });
  return (
    <BaseEdge
      path={path}
      markerEnd={markerEnd}
      className={`lf-edge${selected ? ' selected' : ''}`}
      style={style}
    />
  );
});

export const EDGE_TYPES = { default: DefaultEdge };
