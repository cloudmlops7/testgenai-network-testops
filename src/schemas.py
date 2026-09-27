from typing import List
from pydantic import BaseModel, Field


class ExtractedRequirement(BaseModel):
    id: str
    keyword: str
    statement: str
    source: str
    section: str
    chunk: str
    evidence_ref: str


class DocumentAnalysis(BaseModel):
    documents: List[str] = Field(default_factory=list)
    protocols: List[str] = Field(default_factory=list)
    primary_protocol: str = ""
    related_protocols: List[str] = Field(default_factory=list)
    requirements: List[ExtractedRequirement] = Field(default_factory=list)
    counts: dict = Field(default_factory=dict)


class GenerationRequest(BaseModel):
    feature: str
    user_story: str
    acceptance_criteria: str
    test_types: List[str] = Field(default_factory=list)
    default_priority: str = "P1"
    domain: str = "Network"
    topology: str = ""
    knowledge_context: str = ""
    knowledge_refs: List[str] = Field(default_factory=list)
    document_requirements: List[ExtractedRequirement] = Field(default_factory=list)


class TestScenario(BaseModel):
    id: str
    title: str
    description: str
    type: str
    priority: str
    evidence_refs: List[str] = Field(default_factory=list)


class TestCase(BaseModel):
    id: str
    requirement_id: str
    scenario_id: str
    title: str
    type: str
    priority: str
    preconditions: List[str]
    steps: List[str]
    expected_results: List[str]
    automation_candidate: bool
    topology: str = ""
    evidence_refs: List[str] = Field(default_factory=list)
    document_requirement_ids: List[str] = Field(default_factory=list)


class TraceabilityItem(BaseModel):
    requirement_id: str
    acceptance_criterion: str
    covered: bool
    test_case_ids: List[str]
    gap: str = ""


class TestPlan(BaseModel):
    title: str
    objective: str
    scope: str
    out_of_scope: str
    test_strategy: str
    environment: str
    entry_criteria: str
    exit_criteria: str
    risks: str
    dependencies: str
    automation_strategy: str
    evidence_refs: List[str] = Field(default_factory=list)


class TestSuiteResult(BaseModel):
    feature: str
    scenarios: List[TestScenario]
    test_cases: List[TestCase]
    test_plan: TestPlan
    traceability: List[TraceabilityItem]
    gaps: List[str]
