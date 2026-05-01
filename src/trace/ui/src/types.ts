export type TraceEventType =
  | 'agent_start' | 'agent_end'
  | 'step_start'  | 'step_end'
  | 'llm_start'   | 'llm_end'
  | 'action_start'| 'action_end'
  | 'tool_call'   | 'tool_result'
  | 'skill_call'  | 'skill_result'
  | 'plan_text'
  | 'error'       | 'custom'

export interface TraceEvent {
  id: string
  event_type: TraceEventType
  session_id?: string
  task_id?: string
  agent_name?: string
  label: string
  step_number?: number
  action_index?: number
  action_type?: string
  action_name?: string
  input?: Record<string, unknown>
  output?: unknown
  error?: string
  duration_ms?: number
  metadata?: Record<string, unknown>
  timestamp: string
  _replay?: boolean
}

export interface SessionMeta {
  session_id: string
  file: string
  event_count: number
  first_event_at: string
  last_event_at: string
  agent_names: string[]
  task_ids: string[]
}

// A node in the tree UI
export interface TreeNode {
  id: string
  event: TraceEvent
  children: TreeNode[]
  depth: number
  isExpanded: boolean
}
