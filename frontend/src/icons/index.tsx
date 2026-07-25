import {
  Activity,
  ArrowUpDown,
  BookOpen,
  Bot,
  Box,
  Boxes,
  Braces,
  Cable,
  CandlestickChart,
  Code,
  Columns3,
  Combine,
  Database,
  DownloadCloud,
  FilePen,
  FilePlus2,
  FileSearch,
  FileText,
  Filter,
  Flag,
  FolderInput,
  FolderTree,
  GitBranch,
  Globe,
  KeyRound,
  LogIn,
  LogOut,
  Merge,
  Network,
  PenLine,
  Percent,
  Regex,
  Repeat,
  Repeat2,
  RotateCw,
  Rows3,
  Scissors,
  Search,
  ShieldCheck,
  Shuffle,
  SlidersHorizontal,
  Sparkles,
  Split,
  Table2,
  Target,
  UploadCloud,
  Wrench,
  type LucideIcon,
} from 'lucide-react';

// Curated lucide registry the backend catalog draws NodeSpec.icon names from
// (Langflow's ForwardedIconComponent pattern — every node has its own glyph).
export const LUCIDE_ICONS: Record<string, LucideIcon> = {
  Activity, ArrowUpDown, BookOpen, Bot, Box, Boxes, Braces, Cable, CandlestickChart, Code,
  Columns3, Combine, Database, DownloadCloud, FilePen, FilePlus2, FileSearch, FileText, Filter, Flag,
  FolderInput, FolderTree, GitBranch, Globe, KeyRound, LogIn, LogOut, Merge, Network, PenLine,
  Percent, Regex, Repeat, Repeat2, RotateCw, Rows3, Scissors, Search, ShieldCheck, Shuffle,
  SlidersHorizontal, Sparkles, Split, Table2, Target, UploadCloud, Wrench,
};

// Category → lucide icon name, used for the palette section headers.
export const CATEGORY_ICON_NAME: Record<string, string> = {
  io: 'Cable', structural: 'Split', agent: 'Sparkles', data: 'Database',
  process: 'SlidersHorizontal', evaluation: 'Target', files: 'FileText',
  knowledge: 'BookOpen', tool: 'Wrench', workflow: 'Network',
};

/** Resolve a node's icon by lucide name, falling back to its category glyph. */
export function NodeIcon({
  name, category, size = 16, className, strokeWidth = 2,
}: { name?: string | null; category?: string; size?: number; className?: string; strokeWidth?: number }) {
  const Icon =
    (name && LUCIDE_ICONS[name]) ||
    (category && LUCIDE_ICONS[CATEGORY_ICON_NAME[category] ?? '']) ||
    Boxes;
  return <Icon size={size} strokeWidth={strokeWidth} className={className} />;
}

/** A palette section-header glyph (looked up by category name). */
export function CategoryGlyph({ category, size = 16, className }: { category: string; size?: number; className?: string }) {
  return <NodeIcon name={CATEGORY_ICON_NAME[category]} category={category} size={size} className={className} />;
}
