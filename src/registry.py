from mmengine.registry import Registry

MEMORY_SYSTEM = Registry("memory_system", locations=["src.memory"])
TOOL = Registry("tool", locations=["src.tool"])
AGENT = Registry("agent", locations=["src.agent"])
PROMPT = Registry("prompt", locations=["src.prompt"])
DATASET = Registry("dataset", locations=["src.data"])
BENCHMARK = Registry("benchmark", locations=["src.benchmark"])
SKILL = Registry("skill", locations=["src.skill"])
HOOK = Registry("hook", locations=["src.hook"])
CONSTRAINT = Registry("constraint", locations=["src.constraint"])