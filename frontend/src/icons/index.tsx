import {
  Cable,
  GitBranch,
  Network,
  Sparkles,
  Split,
  Wrench,
  type LucideIcon,
} from 'lucide-react';

import { categoryColor } from '../utils/styleUtils';

// Category → lucide icon registry (langflow's categoryIcons pattern; they map
// sidebar categories to lucide icon names in utils/styleUtils.ts).
export const CATEGORY_ICON_MAP: Record<string, LucideIcon> = {
  io: Cable,
  structural: Split,
  tool: Wrench,
  agent: Sparkles,
  workflow: Network,
};

export function CategoryIcon({ category, size = 13 }: { category: string; size?: number }) {
  const Icon = CATEGORY_ICON_MAP[category] ?? GitBranch;
  return (
    <span className={`lf-node-icon cat-${category}`} style={{ color: categoryColor(category) }}>
      <Icon size={size} strokeWidth={2.2} />
    </span>
  );
}
