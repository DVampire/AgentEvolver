'''One-line description of what this agent does.'''
from typing import Any, Dict, Optional
from pydantic import ConfigDict, Field
from src.agent.types import Agent, AgentContext
from src.response.types import Response, ResponseType
from src.hook.server import hook_manager
from src.hook.types import HookEvent
from src.logger import logger
from src.registry import AGENT
from src.utils.name_utils import make_id


@AGENT.register_module(force=True)
class MyAgent(Agent):
    '''Docstring explaining what the agent does.'''

    model_config = ConfigDict(arbitrary_types_allowed=True, extra='allow')

    name: str = Field(default='my_agent')
    description: str = Field(default='Human-readable description of what the agent does.')
    metadata: Dict[str, Any] = Field(default_factory=dict)
    require_grad: bool = Field(default=True)

    def __init__(self, base_dir: str, name=None, description=None, metadata=None,
                 model_name=None, prompt_name=None, memory_name=None,
                 max_actions: int = 10, max_step: int = 30, review_steps: int = 5,
                 require_grad: bool = True, **kwargs):
        super().__init__(
            base_dir=base_dir, name=name, description=description, metadata=metadata,
            model_name=model_name, prompt_name=prompt_name or 'my_agent',
            memory_name=memory_name, max_actions=max_actions, max_step=max_step,
            review_steps=review_steps, require_grad=require_grad, **kwargs,
        )

    async def _get_agent_context(self, task: str, step_number: int = 0,
                                  ctx=None, **kwargs) -> Dict[str, Any]:
        base = await super()._get_agent_context(task, step_number=step_number, ctx=ctx, **kwargs)
        # Inject domain-specific context here
        target_name = kwargs.get('target_name')
        lines = [f'- **Target**: {target_name}'] if target_name else ['- **Target**: (not specified)']
        base['domain_target'] = '\n'.join(lines)
        base['workspace'] = self._workspace_snapshot(ctx)
        action_errors = kwargs.get('action_errors') or []
        base['errors'] = '\n'.join(f'- {e}' for e in action_errors) if action_errors else ''
        return base

    async def __call__(self, task: str, target_name=None, **kwargs) -> Response:
        logger.info(f'| 🚀 Starting {self.name}: {task}')
        ctx = kwargs.get('ctx', None)
        if ctx is None:
            ctx = AgentContext()
        if not ctx.work_dir:
            ctx.work_dir = self.base_dir
        task_id = make_id()
        on_start = {'event': HookEvent.ON_START, 'agent_name': self.name, 'task_id': task_id,
                    'task': task, 'memory_name': self.memory_name, 'use_memory': self.use_memory}
        await hook_manager(name='memory_hook', input=on_start, ctx=ctx)
        await hook_manager(name='trace_hook', input=on_start, ctx=ctx)
        step_number = 0
        action_errors = []
        response = {'done': False, 'result': None, 'reasoning': None, 'action_errors': []}
        while step_number < self.max_step:
            logger.info(f'| 🔄 [{self.name}] Step {step_number + 1}/{self.max_step}')
            # Canonical loop: check the resource budget ONCE per step, BEFORE building
            # the message, so the prompt reflects the current budget. _think_and_act
            # does NOT check (the base no longer does).
            reason, constraint_status = await self._constraint_check(task_id, ctx)
            if reason is not None:
                logger.warning(f'| 🛑 {self.name} constraint violated: {reason}')
                response = {'done': True, 'result': reason, 'reasoning': None,
                            'action_errors': [], 'stopped_by_constraint': True}
                break
            messages = await self._get_messages(
                task, ctx=ctx, target_name=target_name,
                step_number=step_number, action_errors=action_errors,
                constraint_status=constraint_status,
            )
            response = await self._think_and_act(messages, task_id, step_number, ctx=ctx, target_name=target_name)
            step_number += 1
            action_errors = response.get('action_errors') or []
            if response['done']:
                break
        on_stop = {'event': HookEvent.ON_STOP, 'agent_name': self.name, 'task_id': task_id,
                   'result': response.get('result'), 'memory_name': self.memory_name, 'use_memory': self.use_memory}
        await hook_manager(name='memory_hook', input=on_stop, ctx=ctx)
        await hook_manager(name='trace_hook', input=on_stop, ctx=ctx)
        return Response(type=ResponseType.AGENT,
                        success=response['done'] and not response.get('stopped_by_constraint', False),
                        message=response['result'] or '', data=response)
