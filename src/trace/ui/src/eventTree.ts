/**
 * Build a hierarchical tree from a flat list of TraceEvents.
 *
 * Hierarchy rules:
 *   agent_start/end          → root
 *     step_start/end         → child of agent
 *       llm_start/end        → child of step
 *       tool_call/result     → child of step (paired by action_index)
 *       skill_call/result    → child of step
 *       plan_text            → child of step
 *     action_start/end       → child of step (fallback)
 *   error                    → child of nearest parent
 */

import { TraceEvent, TreeNode } from './types'

let _nodeId = 0
function makeNode(event: TraceEvent, depth: number): TreeNode {
  return {
    id: `node-${_nodeId++}`,
    event,
    children: [],
    depth,
    isExpanded: depth < 2,  // auto-expand top 2 levels
  }
}

export function buildTree(events: TraceEvent[]): TreeNode[] {
  const roots: TreeNode[] = []

  // Stack: [node, ...] — innermost context is last
  const stack: TreeNode[] = []

  const peek = () => stack[stack.length - 1] ?? null

  const push = (node: TreeNode) => {
    const parent = peek()
    if (parent) {
      parent.children.push(node)
    } else {
      roots.push(node)
    }
    stack.push(node)
  }

  const popUntil = (predicate: (n: TreeNode) => boolean) => {
    while (stack.length > 0 && !predicate(peek()!)) {
      stack.pop()
    }
  }

  const addLeaf = (event: TraceEvent) => {
    const parent = peek()
    const node = makeNode(event, parent ? parent.depth + 1 : 0)
    if (parent) {
      parent.children.push(node)
    } else {
      roots.push(node)
    }
  }

  for (const ev of events) {
    const t = ev.event_type

    if (t === 'agent_start') {
      const node = makeNode(ev, 0)
      roots.push(node)
      stack.length = 0
      stack.push(node)
      continue
    }

    if (t === 'agent_end') {
      popUntil(n => n.event.event_type === 'agent_start')
      if (peek()) {
        // Attach end event as a leaf inside agent node, then pop
        const agentNode = peek()!
        agentNode.children.push(makeNode(ev, agentNode.depth + 1))
        stack.pop()
      } else {
        roots.push(makeNode(ev, 0))
      }
      continue
    }

    if (t === 'step_start') {
      // Ensure we're inside an agent node
      popUntil(n => n.event.event_type === 'agent_start')
      push(makeNode(ev, peek() ? peek()!.depth + 1 : 0))
      continue
    }

    if (t === 'step_end') {
      popUntil(n => n.event.event_type === 'step_start')
      if (peek()) {
        const stepNode = peek()!
        stepNode.children.push(makeNode(ev, stepNode.depth + 1))
        stack.pop()
      } else {
        addLeaf(ev)
      }
      continue
    }

    if (t === 'llm_start') {
      popUntil(n => n.event.event_type === 'step_start')
      push(makeNode(ev, peek() ? peek()!.depth + 1 : 0))
      continue
    }

    if (t === 'llm_end') {
      popUntil(n => n.event.event_type === 'llm_start')
      if (peek()) {
        const llmNode = peek()!
        llmNode.children.push(makeNode(ev, llmNode.depth + 1))
        stack.pop()
      } else {
        addLeaf(ev)
      }
      continue
    }

    if (t === 'tool_call' || t === 'skill_call' || t === 'plan_text' || t === 'action_start') {
      popUntil(n => n.event.event_type === 'step_start' || n.event.event_type === 'llm_end')
      push(makeNode(ev, peek() ? peek()!.depth + 1 : 0))
      continue
    }

    if (t === 'tool_result' || t === 'skill_result' || t === 'action_end') {
      popUntil(n =>
        n.event.event_type === 'tool_call' ||
        n.event.event_type === 'skill_call' ||
        n.event.event_type === 'action_start'
      )
      if (peek()) {
        const actionNode = peek()!
        actionNode.children.push(makeNode(ev, actionNode.depth + 1))
        stack.pop()
      } else {
        addLeaf(ev)
      }
      continue
    }

    // Everything else: add as leaf to current context
    addLeaf(ev)
  }

  return roots
}
