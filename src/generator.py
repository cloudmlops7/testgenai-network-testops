import re
from typing import List
from pydantic import BaseModel, Field

from .schemas import GenerationRequest, TestScenario, TestCase, TraceabilityItem, TestPlan, TestSuiteResult
from .prompts import SYSTEM_PROMPT, GENERATION_PROMPT, SUITE_DESIGN_PROMPT


class LLMTestCase(BaseModel):
    requirement_id: str
    title: str
    type: str
    priority: str
    preconditions: List[str] = Field(default_factory=list)
    steps: List[str]
    expected_results: List[str]
    automation_candidate: bool = True
    topology: str = ""
    evidence_refs: List[str] = Field(default_factory=list)
    document_requirement_ids: List[str] = Field(default_factory=list)


class LLMTestCaseSet(BaseModel):
    test_cases: List[LLMTestCase]


class LLMScenario(BaseModel):
    title: str
    description: str
    type: str
    priority: str
    evidence_refs: List[str] = Field(default_factory=list)


class LLMSuiteDesign(BaseModel):
    scenarios: List[LLMScenario]
    test_plan: TestPlan


class TestGenerator:
    def __init__(self, provider="demo", model="qwen3:4b"):
        self.provider = provider
        self.model = model

    def generate(self, request: GenerationRequest) -> TestSuiteResult:
        if self.provider == "ollama":
            return self._generate_ollama(request)
        return self._generate_demo(request)

    def _generate_ollama(self, request: GenerationRequest) -> TestSuiteResult:
        import ollama

        criteria = self._parse_acceptance_criteria(request.acceptance_criteria)
        prompt = GENERATION_PROMPT.format(
            feature=request.feature,
            user_story=request.user_story,
            acceptance_criteria=request.acceptance_criteria,
            domain=request.domain,
            topology=request.topology or "NOT SPECIFIED",
            test_types=", ".join(request.test_types) or "Not specified",
            priority=request.default_priority,
            document_requirements=self._document_requirements_text(request),
            knowledge_context=request.knowledge_context or "NO RETRIEVED KNOWLEDGE",
        )
        response = ollama.chat(
            model=self.model,
            messages=[{"role": "system", "content": SYSTEM_PROMPT}, {"role": "user", "content": prompt}],
            format=LLMTestCaseSet.model_json_schema(),
            think=False,
            options={"temperature": 0, "num_predict": 2200, "num_ctx": 4096},
        )
        generated = LLMTestCaseSet.model_validate_json(response.message.content)
        cases = self._normalise_cases(generated.test_cases, criteria, request)

        # The old implementation fabricated scenarios and a generic plan in Python.
        # Phase 5 moves both artifacts into a second grounded LLM design pass.
        scenarios, plan = self._generate_suite_design(request, criteria, cases)
        return self._build_suite(request, criteria, cases, scenarios, plan)

    @staticmethod
    def _parse_acceptance_criteria(text: str) -> List[str]:
        criteria = []
        for line in text.splitlines():
            cleaned = re.sub(r"^\s*\d+[.)]\s*", "", line.strip()).strip()
            if cleaned:
                criteria.append(cleaned)
        return criteria or ["The feature should behave according to the stated requirement."]

    @staticmethod
    def _allowed_refs(request: GenerationRequest):
        # Evidence is represented inside generated JSON as SOURCE-N, while the UI
        # keeps the human-readable source mapping separately.
        return {f"SOURCE-{i}" for i, _ in enumerate(request.knowledge_refs, 1)}

    @staticmethod
    def _document_requirements_text(request: GenerationRequest) -> str:
        if not request.document_requirements:
            return "NO EXTRACTED DOCUMENT REQUIREMENTS"
        lines = []
        for r in request.document_requirements:
            lines.append(
                f"{r.id} | {r.keyword} | {r.statement} | {r.evidence_ref}"
            )
        return "\n".join(lines)

    @staticmethod
    def _clean_list(values, limit=5):
        """Remove null/blank/non-string LLM array items before rendering."""
        cleaned = []
        for value in values or []:
            if value is None:
                continue
            if isinstance(value, str):
                value = value.strip()
            else:
                value = str(value).strip()
            if value:
                cleaned.append(value)
        return cleaned[:limit]

    def _normalise_casesold(self, generated, criteria, request):
        valid_ids = {f"AC-{i:03d}" for i in range(1, len(criteria) + 1)}
        requested_types = [x.strip().title() for x in request.test_types if x.strip()]
        allowed_refs = self._allowed_refs(request)
        cases = []

        for item in generated:
            rid = item.requirement_id.strip().upper()
            if rid not in valid_ids:
                rid = "AC-001"
            test_type = item.type.strip().title()
            if requested_types and test_type not in requested_types:
                continue
            refs = [r.strip().upper() for r in item.evidence_refs if r.strip().upper() in allowed_refs]
            cases.append(TestCase(
                id=f"TC-{len(cases)+1:03d}",
                requirement_id=rid,
                scenario_id="",
                title=item.title.strip(),
                type=test_type,
                priority=item.priority.strip() or request.default_priority,
                preconditions=self._clean_list(item.preconditions, 5),
                steps=self._clean_list(item.steps, 5) or ["Test step not specified by the generated case."],
                expected_results=self._clean_list(item.expected_results, 3) or ["Expected result not specified by the generated case."],
                automation_candidate=item.automation_candidate,
                topology=(item.topology or "").strip(),
                evidence_refs=refs,
                document_requirement_ids=[r for r in item.document_requirement_ids if any(x.id == r for x in request.document_requirements)],
            ))

        missing_types = [t for t in requested_types if not any(c.type == t for c in cases)]
        if missing_types:
            cases.extend(self._generate_missing_types(request, criteria, missing_types, len(cases)))

        return cases

    def _normalise_cases(self, generated, criteria, request):
        """
        Normalize LLM-generated test cases while preserving document
        requirement IDs in Document/RFC mode.

        User Story mode:
            AC-001, AC-002, ...

        Document/RFC mode:
            REQ-<DOCUMENT>-001, REQ-<DOCUMENT>-002, ...

        Hybrid mode:
            Prefer explicit document requirement IDs when supplied.
        """
        requested_types = [x.strip().title() for x in request.test_types if x.strip()]
        allowed_refs = self._allowed_refs(request)

        document_req_ids = {
            r.id.strip()
            for r in request.document_requirements
            if r.id and r.id.strip()
        }

        # User Story mode continues to use AC-* IDs.
        valid_ac_ids = {
            f"AC-{i:03d}"
            for i in range(1, len(criteria) + 1)
        }

        document_mode = bool(document_req_ids)

        cases = []

        for item in generated:
            test_type = item.type.strip().title()

            if requested_types and test_type not in requested_types:
                continue

            # Keep only evidence references supplied by the RAG layer.
            refs = [
                r.strip().upper()
                for r in item.evidence_refs
                if r.strip().upper() in allowed_refs
            ]

            # Keep only valid document requirement IDs.
            doc_req_ids = [
                r.strip()
                for r in item.document_requirement_ids
                if r.strip() in document_req_ids
            ]

            raw_requirement_id = item.requirement_id.strip()

            if document_mode:
                if raw_requirement_id in document_req_ids:
                    requirement_id = raw_requirement_id
                elif doc_req_ids:
                    requirement_id = doc_req_ids[0]
                else:
                    requirement_id = ""
            else:
                # Existing User Story / Acceptance Criteria behavior.
                requirement_id = raw_requirement_id.upper()

                if requirement_id not in valid_ac_ids:
                    requirement_id = "AC-001"

            cases.append(
                TestCase(
                    id=f"TC-{len(cases) + 1:03d}",
                    requirement_id=requirement_id,
                    scenario_id="",
                    title=item.title.strip(),
                    type=test_type,
                    priority=item.priority.strip() or request.default_priority,
                    preconditions=self._clean_list(item.preconditions, 5),
                    steps=self._clean_list(item.steps, 5)
                    or ["Test step not specified by the generated case."],
                    expected_results=self._clean_list(item.expected_results, 3)
                    or ["Expected result not specified by the generated case."],
                    automation_candidate=item.automation_candidate,
                    topology=(item.topology or "").strip(),
                    evidence_refs=refs,
                    document_requirement_ids=doc_req_ids,
                )
            )

        missing_types = [
            t
            for t in requested_types
            if not any(c.type == t for c in cases)
        ]

        if missing_types:
            cases.extend(
                self._generate_missing_types(
                    request,
                    criteria,
                    missing_types,
                    len(cases),
                )
            )
        # Final sequential numbering.
        for index, case in enumerate(cases, 1):
            case.id = f"TC-{index:03d}"
            
        return cases
    def _generate_missing_types(self, request, criteria, missing_types, existing_count):
        import ollama
        missing = ", ".join(missing_types)
        prompt = f"""Generate exactly one test case for each missing requested test type: {missing}.

Feature: {request.feature}
User story: {request.user_story}
Acceptance criteria:\n{request.acceptance_criteria}
Domain/protocol: {request.domain}
Test topology: {request.topology or "NOT SPECIFIED"}
Extracted document requirements:
{self._document_requirements_text(request)}
Retrieved knowledge:\n{request.knowledge_context or 'NO RETRIEVED KNOWLEDGE'}

Rules:
- One case for every requested type and no other types.
- In Document/RFC mode, map each case to the most relevant REQ-* document requirement ID.
- In User Story mode, map each case to the most relevant AC-###.
- Use only facts supported by the requirement or retrieved knowledge.
- Cite only SOURCE-N references supplied in the retrieved knowledge.
- Keep 2-5 steps and 1-3 expected results.
- Return ONLY JSON matching the schema.
"""
        response = ollama.chat(
            model=self.model,
            messages=[{"role": "system", "content": SYSTEM_PROMPT}, {"role": "user", "content": prompt}],
            format=LLMTestCaseSet.model_json_schema(),
            think=False,
            options={"temperature": 0, "num_predict": 1500, "num_ctx": 4096},
        )
        generated = LLMTestCaseSet.model_validate_json(response.message.content).test_cases
        valid_ids = {f"AC-{i:03d}" for i in range(1, len(criteria) + 1)}
        allowed_refs = self._allowed_refs(request)
        result = []
        for item in generated:
            test_type = item.type.strip().title()
            if test_type not in missing_types:
                continue
            rid = item.requirement_id.strip().upper()
            if rid not in valid_ids:
                rid = "AC-001"
            refs = [r.strip().upper() for r in item.evidence_refs if r.strip().upper() in allowed_refs]
            result.append(TestCase(
                id=f"TC-{existing_count + len(result) + 1:03d}",
                requirement_id=rid,
                scenario_id="",
                title=item.title.strip(),
                type=test_type,
                priority=item.priority.strip() or request.default_priority,
                preconditions=self._clean_list(item.preconditions, 5),
                steps=self._clean_list(item.steps, 5) or ["Test step not specified by the generated case."],
                expected_results=self._clean_list(item.expected_results, 3) or ["Expected result not specified by the generated case."],
                automation_candidate=item.automation_candidate,
                topology=(item.topology or "").strip(),
                evidence_refs=refs,
                document_requirement_ids=[r for r in item.document_requirement_ids if any(x.id == r for x in request.document_requirements)],
            ))
        return result

    def _generate_suite_design(self, request, criteria, cases):
        import ollama
        case_lines = []
        for c in cases:
            case_lines.append(
                f"{c.id} | {c.requirement_id} | {c.type} | {c.title} | evidence={','.join(c.evidence_refs) or 'none'}"
            )
        prompt = SUITE_DESIGN_PROMPT.format(
            feature=request.feature,
            user_story=request.user_story,
            acceptance_criteria=request.acceptance_criteria,
            domain=request.domain,
            topology=request.topology or "NOT SPECIFIED",
            test_types=", ".join(request.test_types) or "Not specified",
            document_requirements=self._document_requirements_text(request),
            knowledge_context=request.knowledge_context or "NO RETRIEVED KNOWLEDGE",
            test_cases="\n".join(case_lines) or "NO TEST CASES",
        )
        response = ollama.chat(
            model=self.model,
            messages=[{"role": "system", "content": SYSTEM_PROMPT}, {"role": "user", "content": prompt}],
            format=LLMSuiteDesign.model_json_schema(),
            think=False,
            options={"temperature": 0, "num_predict": 2200, "num_ctx": 4096},
        )
        design = LLMSuiteDesign.model_validate_json(response.message.content)
        return self._normalise_design(design, request, cases)

    def _normalise_design(self, design, request, cases):
        allowed_refs = self._allowed_refs(request)
        type_order = []
        for case in cases:
            if case.type not in type_order:
                type_order.append(case.type)

        by_type = {s.type.strip().title(): s for s in design.scenarios}
        scenarios = []
        type_to_id = {}
        for stype in type_order:
            src = by_type.get(stype)
            if not src:
                # This is not a hardcoded scenario definition; it is a minimal
                # validation fallback when the LLM omitted a represented type.
                # The description is derived from the actual generated test titles.
                related = [c for c in cases if c.type == stype][:3]
                if related:
                    src = LLMScenario(
                        title=related[0].title,
                        description="Scenario derived from the generated test objective: " + related[0].title,
                        type=stype,
                        priority=request.default_priority,
                        evidence_refs=related[0].evidence_refs,
                    )
                else:
                    continue
            sid = f"TS-{len(scenarios)+1:03d}"
            type_to_id[stype] = sid
            refs = [r.strip().upper() for r in src.evidence_refs if r.strip().upper() in allowed_refs]
            scenarios.append(TestScenario(id=sid, title=src.title.strip(), description=src.description.strip(),
                                           type=stype, priority=src.priority.strip() or request.default_priority,
                                           evidence_refs=refs))

        for case in cases:
            case.scenario_id = type_to_id.get(case.type, "")

        plan = design.test_plan
        plan.evidence_refs = [r.strip().upper() for r in plan.evidence_refs if r.strip().upper() in allowed_refs]
        return scenarios, plan

    def _build_suiteold(self, request, criteria, cases, scenarios, plan):
        trace = []

        if request.document_requirements:
            # Document/RFC mode:
            # trace directly against the stable REQ-* identifiers.
            for requirement in request.document_requirements:
                rid = requirement.id

                ids = [
                    c.id
                    for c in cases
                    if rid in c.document_requirement_ids
                    or c.requirement_id == rid
                ]

                trace.append(
                    TraceabilityItem(
                        requirement_id=rid,
                        acceptance_criterion=requirement.statement,
                        covered=bool(ids),
                        test_case_ids=ids,
                        gap=""
                        if ids
                        else "No test case mapped to this document requirement.",
                    )
                )

        else:
            # User Story / Acceptance Criteria mode.
            for i, criterion in enumerate(criteria, 1):
                rid = f"AC-{i:03d}"

                ids = [
                    c.id
                    for c in cases
                    if c.requirement_id == rid
                ]

                trace.append(
                    TraceabilityItem(
                        requirement_id=rid,
                        acceptance_criterion=criterion,
                        covered=bool(ids),
                        test_case_ids=ids,
                        gap=""
                        if ids
                        else "No test case mapped to this acceptance criterion.",
                    )
                )

        gaps = [t.gap for t in trace if t.gap]

        return TestSuiteResult(
            feature=request.feature,
            scenarios=scenarios,
            test_cases=cases,
            test_plan=plan,
            traceability=trace,
            gaps=gaps,
        )

    def _build_suite(self, request, criteria, cases, scenarios, plan):
        trace = []

        if request.document_requirements:
            # Document/RFC mode:
            # trace directly against the stable REQ-* identifiers.
            for requirement in request.document_requirements:
                rid = requirement.id

                ids = [
                    c.id
                    for c in cases
                    if rid in c.document_requirement_ids
                    or c.requirement_id == rid
                ]

                trace.append(
                    TraceabilityItem(
                        requirement_id=rid,
                        acceptance_criterion=requirement.statement,
                        covered=bool(ids),
                        test_case_ids=ids,
                        gap=""
                        if ids
                        else "No test case mapped to this document requirement.",
                    )
                )

        else:
            # User Story / Acceptance Criteria mode.
            for i, criterion in enumerate(criteria, 1):
                rid = f"AC-{i:03d}"

                ids = [
                    c.id
                    for c in cases
                    if c.requirement_id == rid
                ]

                trace.append(
                    TraceabilityItem(
                        requirement_id=rid,
                        acceptance_criterion=criterion,
                        covered=bool(ids),
                        test_case_ids=ids,
                        gap=""
                        if ids
                        else "No test case mapped to this acceptance criterion.",
                    )
                )

        gaps = [t.gap for t in trace if t.gap]

        return TestSuiteResult(
            feature=request.feature,
            scenarios=scenarios,
            test_cases=cases,
            test_plan=plan,
            traceability=trace,
            gaps=gaps,
        )
    
    def _generate_demo(self, request):
        # Offline mode remains available for UI testing, but Ollama mode is the
        # document-driven path for the portfolio POC.
        criteria = self._parse_acceptance_criteria(request.acceptance_criteria)
        cases = [TestCase(
            id=f"TC-{i:03d}", requirement_id=f"AC-{i:03d}", scenario_id="",
            title=f"Verify: {c}", type="Functional", priority=request.default_priority,
            preconditions=["Required test environment is available."],
            steps=[f"Configure the environment for: {c}", f"Execute the feature operation related to: {c}"],
            expected_results=[c], automation_candidate=True, topology=request.topology.strip(), evidence_refs=[])
            for i, c in enumerate(criteria, 1)]
        scenarios = [TestScenario(id="TS-001", title=request.feature, description=request.user_story,
                                  type="Functional", priority=request.default_priority, evidence_refs=[])]
        for c in cases:
            c.scenario_id = "TS-001"
        plan = TestPlan(
            title=f"{request.feature} — Test Plan",
            objective=request.user_story,
            scope=request.feature,
            out_of_scope="Not specified",
            test_strategy="Functional validation of the supplied acceptance criteria.",
            environment="Required QA environment.",
            entry_criteria="Requirement available.",
            exit_criteria="Acceptance criteria covered.",
            risks="Not specified",
            dependencies="Not specified",
            automation_strategy="Automate repeatable checks with pytest.",
            evidence_refs=[],
        )
        return self._build_suite(request, criteria, cases, scenarios, plan)
