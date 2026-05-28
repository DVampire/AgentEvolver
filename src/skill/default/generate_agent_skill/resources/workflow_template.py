'''One-line description of what this workflow agent does.'''
from typing import Any, Dict, Optional
from pydantic import ConfigDict, Field
from src.agent.types import Agent, AgentContext, AgentExtra, AgentResponse
from src.hook.server import hook_manager
from src.hook.types import HookEvent
from src.logger import logger
from src.registry import AGENT
from src.utils.name_utils import make_id


@AGENT.register_module(force=True)
class MyWorkflowAgent(Agent):
    '''Docstring explaining what the agent does.'''

    model_config = ConfigDict(arbitrary_types_allowed=True, extra='allow')

    name: str = Field(default='my_workflow_agent')
    description: str = Field(default='Human-readable description.')
    metadata: Dict[str, Any] = Field(default_factory=dict)
    require_grad: bool = Field(default=True)

    def __init__(self, base_dir: str, name=None, description=None, metadata=None,
                 model_name=None, prompt_name=None, memory_name=None,
                 max_actions: int = 10, max_steps: int = 30, review_steps: int = 5,
                 require_grad: bool = True, **kwargs):
        super().__init__(
            base_dir=base_dir, name=name, description=description, metadata=metadata,
            model_name=model_name, prompt_name=prompt_name,
            memory_name=memory_name, max_actions=max_actions, max_steps=max_steps,
            review_steps=review_steps, require_grad=require_grad, **kwargs,
        )

    async def __call__(self, task: str, target_name=None, **kwargs) -> AgentResponse:
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
        try:
            step1_result = await self._step_read(task, target_name, ctx)
            step2_result = await self._step_process(step1_result, ctx)
            final_result = await self._step_report(step2_result, ctx)
            success, message = True, final_result
        except Exception as e:
            logger.error(f'| ❌ [{self.name}] Workflow failed: {e}')
            success, message = False, str(e)
        on_stop = {'event': HookEvent.ON_STOP, 'agent_name': self.name, 'task_id': task_id,
                   'result': message, 'memory_name': self.memory_name, 'use_memory': self.use_memory}
        await hook_manager(name='memory_hook', input=on_stop, ctx=ctx)
        await hook_manager(name='trace_hook', input=on_stop, ctx=ctx)
        return AgentResponse(success=success, message=message, extra=AgentExtra(data={}))

    async def _step_read(self, task: str, target_name: Optional[str], ctx) -> str:
        raise NotImplementedError

    async def _step_process(self, data: str, ctx) -> str:
        raise NotImplementedError

    async def _step_report(self, result: str, ctx) -> str:
        raise NotImplementedError
