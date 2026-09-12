// Category accents share the dashboard palette in style/theme.css.

export const nodeColors: Record<string, string> = {
  inputs: 'var(--green)',
  outputs: 'var(--red)',
  data: 'var(--blue)',
  prompts: 'var(--blue)',
  models: 'var(--violet)',
  agents: 'var(--violet)',
  tools: 'var(--green)',
  chains: 'var(--amber)',
  memories: 'var(--amber)',
  str: 'var(--violet)',
  Message: 'var(--violet)',
  unknown: 'var(--text-mid)',
};

/** Palette categories mapped onto the shared semantic accents. */
export const categoryColors: Record<string, string> = {
  io: nodeColors.inputs,
  structural: nodeColors.data,
  tool: nodeColors.tools,
  agent: nodeColors.agents,
  workflow: nodeColors.prompts,
  data: nodeColors.data,
  process: nodeColors.chains,
  evaluation: nodeColors.outputs,
  files: nodeColors.memories,
  knowledge: nodeColors.models,
};

export function categoryColor(category: string): string {
  return categoryColors[category] ?? nodeColors.unknown;
}
