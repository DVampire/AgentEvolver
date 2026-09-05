from typing import List, Optional, Literal
from pydantic import BaseModel, Field, model_validator


class EvaluationCase(BaseModel):
    case_id: str = Field(min_length=1)
    expected: str = Field(min_length=1)
    observed: str = Field(min_length=1)
    passed: bool
    evidence_ids: List[str] = Field(min_length=1)


class ComponentEvaluation(BaseModel):
    """A version-scoped evaluator judgment, not a claim of deterministic proof."""
    module: str
    name: str = Field(min_length=1)
    version: str = Field(min_length=1)
    verdict: Literal["pass", "fail", "inconclusive"]
    baseline: str = Field(min_length=1)
    cases: List[EvaluationCase] = Field(default_factory=list)

    @model_validator(mode="after")
    def validate_evidence(self):
        from agentevolver.capability.types import COMPONENT_TYPE_NAMES

        if self.module not in COMPONENT_TYPE_NAMES:
            raise ValueError("Evaluation must name one of the eight component families")
        ids = [case.case_id for case in self.cases]
        if len(ids) != len(set(ids)):
            raise ValueError("Evaluation case IDs must be unique")
        if self.verdict == "pass" and (not self.cases or not all(case.passed for case in self.cases)):
            raise ValueError("A passing evaluation requires observed passing cases")
        return self


class ManifestComponent(BaseModel):
    """One active extension component."""
    module: str = Field(description="Owning module: tool / agent / prompt / skill / environment")
    name: str = Field(description="Registered name (the key used to unregister)")
    version: str = Field(default="1.0.0", description="Currently active version of this component")
    file: str = Field(description="Active file/dir path relative to the extension root, e.g. 'tool/calculator_tool.py'")


class Manifest(BaseModel):
    """The single source of truth for the active extension set.

    Maps each active component to the version currently live and the flat working
    file that holds its source. All historical versions of a component coexist under
    `.versions/<module>/<name>/` — this manifest only names the active one.
    """
    components: List[ManifestComponent] = Field(default_factory=list)

    def find(self, module: str, name: str) -> Optional[ManifestComponent]:
        for c in self.components:
            if c.module == module and c.name == name:
                return c
        return None

    def upsert(self, comp: ManifestComponent) -> None:
        self.components = [c for c in self.components if not (c.module == comp.module and c.name == comp.name)]
        self.components.append(comp)

    def remove(self, module: str, name: str) -> None:
        self.components = [c for c in self.components if not (c.module == module and c.name == name)]
