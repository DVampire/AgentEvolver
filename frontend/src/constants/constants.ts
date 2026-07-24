// UI constants (langflow keeps these in src/constants/constants.ts).

export const CONTAINER_W = 460;
export const CONTAINER_H = 320;

export const CATEGORY_LABELS: Record<string, string> = {
  io: 'Input & Output',
  structural: 'Flow Control',
  tool: 'Tools',
  agent: 'Agents',
  workflow: 'Workflows',
};
export const CATEGORY_ORDER = ['io', 'structural', 'tool', 'agent', 'workflow'];

export const REF_PATTERN = /\$\{([A-Za-z][A-Za-z0-9_-]*)/g;
export const DND_MIME = 'application/agentevolver-spec';

export const MAX_HISTORY_SIZE = 100;
export const ALERT_AUTO_DISMISS_MS = 5000;
export const RUN_POLL_INTERVAL_MS = 1000;
