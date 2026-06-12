'''One-line description of what this environment provides (e.g. a key-value store, a shell, a game).'''
from typing import Any, Dict, Optional

from pydantic import ConfigDict, Field

from src.environment.server import environment_manager
from src.environment.types import Environment
from src.logger import logger
from src.registry import ENVIRONMENT
from src.utils import assemble_project_path


@ENVIRONMENT.register_module(force=True)
class MyEnvironment(Environment):
    '''Docstring explaining what the environment does and what actions it exposes.'''

    model_config = ConfigDict(arbitrary_types_allowed=True, extra='allow')

    name: str = Field(default='my_environment')
    description: str = Field(default='Human-readable description of the environment.')
    metadata: Dict[str, Any] = Field(default={
        'has_vision': False,
        'additional_rules': {
            'state': 'Describe what get_state returns for this environment.',
        },
    })
    require_grad: bool = Field(default=True)

    def __init__(self, base_dir: str = None, **kwargs):
        # Pop env-specific config kwargs BEFORE super().__init__ so pydantic does not reject them.
        super().__init__(**kwargs)
        self.base_dir = assemble_project_path(base_dir) if base_dir else assemble_project_path('my_environment')
        # Initialize lightweight in-memory state here. Heavy resources (servers,
        # browsers, sockets) should be started in initialize(), not __init__.
        self._state: Dict[str, Any] = {}

    # ------------------------------------------------------------------ lifecycle

    async def initialize(self) -> None:
        '''Start any external resources. Called once before the environment is used.'''
        logger.info(f'| 🌱 {self.name} ready')

    async def cleanup(self) -> None:
        '''Release resources. Called on shutdown.'''
        self._state.clear()
        logger.info(f'| 🧹 {self.name} cleaned up')

    # ------------------------------------------------------------------ actions

    @environment_manager.action(
        name='set_value',
        description='Store a value under a key. Args: key (str), value (str).',
    )
    async def set_value(self, key: str, value: str, **kwargs) -> Dict[str, Any]:
        try:
            self._state[key] = value
            return {'success': True, 'message': f'Set {key}={value}', 'extra': {'key': key, 'value': value}}
        except Exception as e:
            logger.error(f'| ❌ set_value failed: {e}')
            return {'success': False, 'message': str(e), 'extra': {'error': str(e)}}

    @environment_manager.action(
        name='get_value',
        description='Read the value stored under a key. Args: key (str).',
    )
    async def get_value(self, key: str, **kwargs) -> Dict[str, Any]:
        try:
            value = self._state.get(key)
            return {'success': True, 'message': f'{key}={value}', 'extra': {'key': key, 'value': value}}
        except Exception as e:
            logger.error(f'| ❌ get_value failed: {e}')
            return {'success': False, 'message': str(e), 'extra': {'error': str(e)}}

    # ------------------------------------------------------------------ state

    async def get_state(self, **kwargs) -> Dict[str, Any]:
        '''Return the observable state. MUST return {"state": <text>, "extra": {...}}.'''
        try:
            state_text = f'<info>\nStored keys: {list(self._state.keys())}\n</info>'
            return {
                'state': state_text,
                'extra': {
                    'base_dir': self.base_dir,
                    'keys': list(self._state.keys()),
                },
            }
        except Exception as e:
            logger.error(f'| ❌ get_state failed: {e}')
            return {'state': 'Failed to get state', 'extra': {'error': str(e)}}
