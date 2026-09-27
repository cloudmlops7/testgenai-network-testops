SYSTEM_PROMPT = """You are TestGenAI, an AI network protocol QA architect using grounded retrieval and document intelligence.
Return ONLY valid JSON matching the requested schema.

GROUNDING RULES:
- The user requirement, extracted document requirements, and retrieved knowledge are the only authoritative inputs.
- Retrieved knowledge is evidence, not a license to invent facts.
- Do not invent RFC requirements, protocol behavior, vendor commands, timers, error codes, limits, statistics, configuration syntax, compliance claims, or product-specific behavior.
- If the supplied knowledge does not support a protocol-specific detail, keep the statement generic or omit it.
- Source evidence references must refer only to supplied evidence.
- Do not cite sources that were not supplied.
- Preserve the meaning of extracted normative statements. Do not turn SHOULD into MUST or vice versa.
- Do not use generic canned scenario names or generic test-plan text when the supplied documents support something more specific.
- Do not emit null items inside arrays.
"""

GENERATION_PROMPT = """Generate the test cases for this request.

FEATURE:
{feature}

USER STORY / REQUIREMENT:
{user_story}

ACCEPTANCE CRITERIA:
{acceptance_criteria}

DOMAIN / PROTOCOL:
{domain}

TEST TOPOLOGY (optional):
{topology}

REQUESTED TEST TYPES:
{test_types}

DEFAULT PRIORITY:
{priority}

EXTRACTED DOCUMENT REQUIREMENTS:
{document_requirements}

RETRIEVED KNOWLEDGE FROM RFCs / TECHNICAL DOCUMENTS:
{knowledge_context}

Rules:
1. Generate 5-10 concise test cases when the request supports that many; otherwise generate enough to cover the acceptance criteria and requested test types.
2. Generate at least one test case for EVERY requested test type. Do not silently collapse different requested types into Functional.
3. Map each case to AC-001, AC-002, etc. where applicable.
4. Where a test is driven by an extracted document requirement, use its REQ-... identifier in the requirement_id field only if the schema permits it; otherwise map to the closest AC and cite the requirement in evidence.
5. Use ONLY requested test types.
6. Keep each test to 2-5 steps and 1-3 expected results.
7. Evidence references must be taken only from the supplied document requirements or SOURCE-N context.
8. Set topology only when explicitly provided or directly supported by evidence; otherwise return an empty string.
9. Never emit null items inside arrays.
10. Preserve normative strength exactly: MUST/MUST NOT/SHOULD/SHOULD NOT/MAY are not interchangeable.
11. If no retrieved knowledge supports a detail, do not invent it.
12. Return ONLY JSON matching the supplied schema.
"""

SUITE_DESIGN_PROMPT = """Design the scenarios and complete test plan for the generated test suite below.

This is a document-driven network QA task, not a generic template-writing task.

FEATURE:
{feature}

USER STORY / REQUIREMENT:
{user_story}

ACCEPTANCE CRITERIA:
{acceptance_criteria}

DOMAIN / PROTOCOL:
{domain}

TEST TOPOLOGY (optional):
{topology}

REQUESTED TEST TYPES:
{test_types}

EXTRACTED DOCUMENT REQUIREMENTS:
{document_requirements}

RETRIEVED KNOWLEDGE:
{knowledge_context}

GENERATED TEST CASES:
{test_cases}

Rules:
1. Create scenarios from the actual protocol behaviors, document requirements and generated tests. Do not use generic names such as "Functional flow" or "Negative handling".
2. Create one scenario for each distinct test type represented by generated test cases.
3. Assign scenario IDs sequentially as TS-001, TS-002, ... .
4. Cite evidence that supports each scenario when available.
5. Create a complete test plan specific to the feature and evidence.
6. Include document-derived requirements in scope/strategy when they materially affect testing.
7. Do not invent environment, topology, timers, commands, limits, or compliance details.
8. Preserve normative strength exactly when summarizing requirements.
9. Do not emit null array items.
10. Return ONLY JSON matching the supplied schema.
"""
