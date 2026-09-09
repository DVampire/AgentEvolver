from typing import List, Optional, Literal
from pydantic import BaseModel, Field, model_validator


class EvaluationCase(BaseModel):
    case_id: str = Field(min_length=1)
    expected: str = Field(min_length=1)
    observed: str = Field(min_length=1)
    passed: bool
    evidence_ids: List[str] = Field(min_length=1)
    kind: Optional[Literal["comparison", "reuse", "regression"]] = None


class CapabilityGap(BaseModel):
    """Diagnosis submitted by the builder; evidence provenance is checked at runtime."""
    user_need: str = Field(min_length=1)
    required_operation: str = Field(min_length=1)
    limitation: str = Field(min_length=1)
    acceptance_criterion: str = Field(min_length=1)
    observation_evidence_ids: List[str] = Field(min_length=1)
    baseline_evidence_ids: List[str] = Field(min_length=1)


class ComponentEvaluation(BaseModel):
    """A version-scoped evaluator judgment, not a claim of deterministic proof."""
    module: str
    name: str = Field(min_length=1)
    version: str = Field(min_length=1)
    verdict: Literal["pass", "fail", "inconclusive"]
    baseline: str = Field(min_length=1)
    cases: List[EvaluationCase] = Field(default_factory=list)
    capability_gap: Optional[CapabilityGap] = None

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


def component_evaluation_schema():
    """Inline this acyclic report model for embedding in a tool parameter schema."""
    schema = ComponentEvaluation.model_json_schema()
    definitions = schema.pop("$defs", {})

    def expand(value):
        if isinstance(value, list):
            return [expand(item) for item in value]
        if isinstance(value, dict):
            if "$ref" in value:
                return expand(definitions[value["$ref"].removeprefix("#/$defs/")])
            return {key: expand(item) for key, item in value.items()}
        return value

    return expand(schema)


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
