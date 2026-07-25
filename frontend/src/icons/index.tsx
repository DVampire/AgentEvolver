import {
  Cable,
  Database,
  FileText,
  GitBranch,
  Network,
  Sparkles,
  Split,
  SlidersHorizontal,
  Target,
  BookOpen,
  Wrench,
  type LucideIcon,
} from 'lucide-react';

import { categoryColor } from '../utils/styleUtils';

// Category → lucide icon registry (langflow's categoryIcons pattern; they map
// sidebar categories to lucide icon names in utils/styleUtils.ts).
export const CATEGORY_ICON_MAP: Record<string, LucideIcon> = {
  io: Cable,
  structural: Split,
  agent: Sparkles,
  data: Database,
  process: SlidersHorizontal,
  evaluation: Target,
  files: FileText,
  knowledge: BookOpen,
  tool: Wrench,
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
