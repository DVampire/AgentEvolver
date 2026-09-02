"""Hook event names — the one thing every layer needs and nothing should pay for.

A leaf module on purpose. This enum used to live in ``hook/types.py`` beside the
``HookContext`` and ``Hook`` models, which import the message and session packages;
anything wanting to name an event therefore had to import those too, and both the tool
execution pipeline and the sandbox worktree hit an import cycle and settled for raw
strings instead. Raw strings do not fail when they drift — a typo becomes an event
nobody raises, which is indistinguishable from a hook that never fires.

Nothing is imported here beyond the standard library, so every layer can name an event.
"""

from __future__ import annotations

from enum import Enum


class HookEvent(str, Enum):
    """Lifecycle events that middleware can intercept."""

    # Grouped by the scope they fire in, outermost first. The nesting is the whole
    # relationship to the runtime's process phases: a session holds processes, a process
    # runs steps, a step runs actions. The kernel owns the outermost bracket and never
    # interrupts a step, because its only safe points are between steps.
    #
    # Every member below has exactly one firing site, named in its comment. An event with
    # no site is worse than a missing one: a handler registers for it, never runs, and
    # reads as a policy that is in force.

    # -- Session: the whole task tree. Raised by Kernel._announce for a root process.
    SESSION_START = "session_start"
    SESSION_END = "session_end"
    USER_PROMPT_SUBMIT = "user_prompt_submit"

    # -- Process: one agent's run. Raised by Kernel._announce for a spawned child, and
    #    by the process itself when the kernel holds or releases it.
    SUBAGENT_START = "subagent_start"
    SUBAGENT_STOP = "subagent_stop"
    ON_SUSPEND = "on_suspend"
    ON_RESUME = "on_resume"
    #: Every process reports its own outcome, root or child alike.
    TASK_COMPLETED = "task_completed"

    # -- Agent run: the loop's own brackets, inside RUNNING.
    ON_START = "on_start"
    #: The agent decided it was done. Not the stop *signal* — that lands in the
    #: runtime's `on_land` phase, which fires on every graceful ending including this
    #: one. Two causes, two names.
    ON_STOP = "on_stop"
    #: A sub-agent is blocked and waiting on its parent. Raised by `escalate_tool`.
    ON_ESCALATE = "on_escalate"
    #: Not a lifecycle event. `hook_manager` requires an `event` key even when a hook
    #: is called by name as a service — the `compact` hook, asked to write a summary —
    #: and this is the honest marker for that. Nothing fans out on it.
    DIRECT_CALL = "direct_call"

    # -- Step: one think+act. Raised by the loop.
    PRE_STEP = "pre_step"
    POST_STEP = "post_step"
    #: History is folded into a checkpoint. Raised both by the agent's own fold and by
    #: the memory tier's, since either can be why a token count dropped.
    PRE_COMPACT = "pre_compact"
    POST_COMPACT = "post_compact"

    # -- Action: one capability call, whichever of the seven kinds it is. Raised by the
    #    loop's executor, around routing; the kind is in `action.type`. `plan_mode_hook`
    #    and `repeat_tool_reminder_hook` gate here, so a call denied at this level never
    #    reaches an implementation at all.
    PRE_ACTION = "pre_action"
    POST_ACTION = "post_action"

    # -- Invocation: the resolved call actually running, one level below an action.
    #    Raised by the shared execution pipeline, which `tool`, `environment` and
    #    `connector` all funnel through; `capability_type` in the payload says which.
    #    The level is load-bearing and separate from the action: PRE_ACTION sees a call
    #    the model wants to make, PRE_INVOKE sees one that is about to run with its
    #    arguments canonicalised and its guards passed. Both are gates, either may BLOCK.
    #
    #    These were PRE_TOOL_USE / POST_TOOL_USE / POST_TOOL_USE_FAILURE, which named one
    #    capability kind for a stage three kinds reach: an environment action raised
    #    `pre_tool_use` for something that is not a tool.
    PRE_INVOKE = "pre_invoke"
    #: The call needs approval and the policy layer is about to ask for it.
    PERMISSION_REQUEST = "permission_request"
    POST_INVOKE = "post_invoke"
    INVOKE_FAILED = "invoke_failed"

    # -- Sandbox: raised by IsolatedWorktree, outside any step.
    WORKTREE_CREATE = "worktree_create"
    WORKTREE_REMOVE = "worktree_remove"


__all__ = ["HookEvent"]
