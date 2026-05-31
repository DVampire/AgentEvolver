from typing import Any, Dict, Optional
from pydantic import ConfigDict, Field
from src.agent.types import Agent, AgentContext, AgentExtra, AgentResponse
from src.hook.server import hook_manager
from src.hook.types import HookEvent
from src.logger import logger
from src.registry import AGENT
from src.tool.server import tool_manager
from src.utils.name_utils import make_id
import json

@AGENT.register_module(force=True)
class summary_agent(Agent):
    '''A workflow agent that reads a text file and summarizes it using bash_tool.'''

    model_config = ConfigDict(arbitrary_types_allowed=True, extra='allow')

    name: str = Field(default='summary_agent')
    description: str = Field(default='Reads a text file and summarizes its content using bash_tool.')
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
            file_path = task.strip()
            read_result = await self._step_read(file_path, target_name, ctx)
            summary = await self._step_process(read_result, ctx)
            success, message = True, summary
        except Exception as e:
            logger.error(f'| ❌ [{self.name}] Workflow failed: {e}')
            success, message = False, str(e)
            
        on_stop = {'event': HookEvent.ON_STOP, 'agent_name': self.name, 'task_id': task_id,
                   'result': message, 'memory_name': self.memory_name, 'use_memory': self.use_memory}
        await hook_manager(name='memory_hook', input=on_stop, ctx=ctx)
        await hook_manager(name='trace_hook', input=on_stop, ctx=ctx)
        return AgentResponse(success=success, message=message, extra=AgentExtra(data={}))

    async def _step_read(self, file_path: str, target_name: Optional[str], ctx) -> str:
        logger.info(f'| 📖 Reading file: {file_path}')
        result = await tool_manager(name='read_file_tool', input={'path': file_path})
        return str(result)

    async def _step_process(self, data: str, ctx) -> str:
        logger.info(f'| 🧠 Summarizing content')
        escaped_data = json.dumps(data)
        command = f"python3 -c 'import sys; data=sys.argv[1]; print(\"Summary: \" + data[:100] + \"...\")' {escaped_data}"
        result = await tool_manager(name='bash_tool', input={'command': command})
        return str(result)
