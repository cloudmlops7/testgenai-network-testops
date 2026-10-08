import json
import os
from datetime import datetime
import streamlit as st

from src.schemas import GenerationRequest, ExtractedRequirement
from src.generator import TestGenerator
from src.rag.knowledge_base import KnowledgeBase

st.set_page_config(page_title="TestGenAI", page_icon="🧪", layout="wide")
st.title("🧪 TestGenAI")
st.caption("Phase 6 — Document/RFC Intelligence → normative requirements → grounded scenarios, test cases, plans and traceability")

@st.cache_resource
def get_kb():
    return KnowledgeBase(
        db_path=os.getenv("TESTGENAI_CHROMA_PATH", "./data/chroma"),
        embedding_model=os.getenv("TESTGENAI_EMBED_MODEL", "embeddinggemma"),
    )

kb = get_kb()

if "doc_analysis" not in st.session_state:
    st.session_state.doc_analysis = None

with st.sidebar:
    st.header("Generation Settings")
    mode = st.selectbox("Provider", ["demo", "ollama"], index=0 if os.getenv("TESTGENAI_MODE", "ollama") == "demo" else 1)
    model = st.text_input("Model", value=os.getenv("TESTGENAI_MODEL", "qwen3:4b"))
    st.info("Phase 6: documents are indexed with Chroma, normative statements are extracted, and Qwen3 generates the test suite from requirements + retrieved evidence.")

    st.divider()
    st.subheader("Knowledge Base")
    uploaded = st.file_uploader("Upload RFC / PDF / DOCX / TXT / Markdown", type=["pdf", "docx", "txt", "md"], accept_multiple_files=True)
    if st.button("📚 Index Documents", use_container_width=True, disabled=not uploaded):
        with st.spinner("Extracting, chunking and embedding documents..."):
            results = []
            for f in uploaded:
                try:
                    results.append(kb.ingest(f.name, f.getvalue()))
                except Exception as e:
                    msg = str(e)
                    st.error(f"{f.name}: {msg}")
                    if "/tokenize" in msg or "connection refused" in msg.lower():
                        st.warning("The failure is from the local Ollama embedding worker, not Chroma. Large embedding batches can restart the local tokenizer worker. Phase 6.1 retries smaller batches. If it persists, verify Ollama with `ollama list` and test the embedding model directly.")
        for r in results:
            st.success(f"Indexed {r['source']} — {r['chunks']} chunks (embedding batch: {r.get('batch_size', 'default')})")
        st.session_state.doc_analysis = None

    sources = kb.list_sources()
    st.caption(f"Knowledge base: {len(sources)} document(s)")
    source_names = [s["source"] for s in sources]
    for s in sources[:10]:
        st.write(f"• {s['source']}")

    selected_analysis_sources = st.multiselect(
        "Documents to analyze",
        source_names,
        default=source_names[-1:] if source_names else [],
        disabled=not sources,
        help="Choose which indexed documents should supply normative requirements. This prevents an older RFC in the knowledge base from being analyzed accidentally.",
    )

    if st.button("🔍 Analyze Documents", use_container_width=True, disabled=not selected_analysis_sources):
        with st.spinner("Extracting normative requirements from selected documents..."):
            try:
                st.session_state.doc_analysis = kb.analyze_documents(max_requirements=60, sources=selected_analysis_sources)
            except Exception as e:
                st.error(f"Document analysis failed: {e}")

analysis = st.session_state.doc_analysis

if analysis:
    st.subheader("📄 Document Intelligence")
    ac1, ac2, ac3, ac4 = st.columns(4)
    ac1.metric("Documents", len(analysis["documents"]))
    ac2.metric("Requirements", len(analysis["requirements"]))
    ac3.metric("MUST / MUST NOT", analysis["counts"].get("MUST", 0) + analysis["counts"].get("MUST NOT", 0))
    ac4.metric("SHOULD / MAY", analysis["counts"].get("SHOULD", 0) + analysis["counts"].get("MAY", 0))
    if analysis.get("primary_protocol"):
        related = analysis.get("related_protocols", [])
        suffix = f" | Related: {', '.join(related)}" if related else ""
        st.caption(f"Primary protocol: {analysis['primary_protocol']}{suffix}")
    st.dataframe([
        {"ID": r["id"], "Keyword": r["keyword"], "Requirement": r["statement"], "Source": r["source"], "Section": r["section"], "Chunk": r["chunk"]}
        for r in analysis["requirements"]
    ], use_container_width=True, height=260)

st.divider()

input_mode = st.radio(
    "Input Mode",
    ["User Story / Requirements", "Document / RFC", "Hybrid"],
    horizontal=True,
    help="Document/RFC mode lets the uploaded documents supply the requirements. Hybrid combines documents with an optional user story."
)

manual_feature = ""
manual_story = ""
manual_acceptance = ""
manual_domain = ""

if input_mode == "User Story / Requirements":
    manual_feature = st.text_input("Feature", value="BGP Session Establishment")
    manual_story = st.text_area("User Story / Requirement", value="As a network engineer, I want to validate BGP session establishment so that compliant peers can establish and maintain a routing session.", height=120)
    manual_acceptance = st.text_area("Acceptance Criteria", value="1. A valid peer can establish a BGP session.\n2. Invalid session parameters are rejected.\n3. The session reaches the expected established state.", height=130)
    manual_domain = st.text_input("Domain / Protocol", value="Networking / BGP")

elif input_mode == "Document / RFC":
    if analysis and analysis["requirements"]:
        detected = analysis.get("primary_protocol") or (
            analysis["protocols"][0]
            if analysis.get("protocols")
            else "Network Protocol"
        )

        manual_feature = f"{detected} Document Validation"

        manual_story = (
            "Generate network protocol validation coverage from the "
            "uploaded technical documents and RFC requirements."
        )

        # Document/RFC mode uses REQ-* identifiers directly.
        # Do NOT convert RFC requirements into AC-001, AC-002, etc.
        manual_acceptance = (
            "Validate the extracted normative requirements from the "
            "selected document/RFC. Test cases must trace back to the "
            "applicable REQ-* document requirement IDs."
        )

        manual_domain = f"Networking / {detected}"

        st.success(
            "Requirements are being supplied automatically from the analyzed "
            "documents. RFC requirements retain their REQ-* identifiers."
        )
    else:
        st.info(
            "Upload and index an RFC/technical document, then click "
            "**Analyze Documents**. The feature, requirement and acceptance "
            "criteria fields will be derived automatically."
        )
        manual_feature = "Document Validation"
        manual_story = ""
        manual_acceptance = ""
        manual_domain = "Networking"

else:  # Hybrid
    detected = (analysis.get("primary_protocol") if analysis else "") or (analysis["protocols"][0] if analysis and analysis.get("protocols") else "")
    manual_feature = st.text_input("Feature (optional)", value=f"{detected} Validation" if detected else "")
    manual_story = st.text_area("User Story / Requirement (optional)", value="", height=100)
    manual_acceptance = st.text_area("Acceptance Criteria (optional)", value="", height=110)
    manual_domain = st.text_input("Domain / Protocol (optional)", value=f"Networking / {detected}" if detected else "Networking")
    if analysis and analysis["requirements"]:
        st.caption("The extracted document requirements will be used in addition to any fields you provide above.")

c1, c2 = st.columns(2)
with c1:
    test_types = st.multiselect("Test Types", ["Functional", "Negative", "Boundary", "Integration", "Performance", "Security", "Recovery", "Compatibility", "Regression"], default=["Functional", "Negative", "Integration"])
with c2:
    priority = st.selectbox("Default Priority", ["P1", "P2", "P3"], index=0)

topology = st.text_input("Test Topology (optional)", value="", placeholder="Example: DUT-A (BGP peer) ↔ DUT-B (BGP peer)")

generate = st.button("🚀 Generate Document-Driven Test Suite", type="primary", use_container_width=True)

if generate:
    if not manual_story and not manual_acceptance and not (analysis and analysis["requirements"]):
        st.error("Provide a user story/requirement, acceptance criteria, or analyze at least one document.")
        st.stop()

    feature = manual_feature or "Document Validation"
    story = manual_story or "Generate validation coverage from the supplied document requirements."
    acceptance = manual_acceptance
    if not acceptance and analysis:
        acceptance = "\n".join(f"{i}. {r['statement']}" for i, r in enumerate(analysis["requirements"][:20], 1))
    domain = manual_domain or "Network"

    query_parts = [feature, story, acceptance, domain]
    if analysis:
        query_parts.extend(r["statement"] for r in analysis["requirements"][:8])
    query = "\n".join(x for x in query_parts if x.strip())

    with st.spinner("Retrieving relevant RFC/document knowledge..."):
        try:
            context, refs = kb.build_context(query, top_k=8)
        except Exception as e:
            st.error(f"RAG retrieval failed: {e}")
            context, refs = "", []

    if context:
        with st.expander("Retrieved knowledge", expanded=False):
            st.write("Grounding sources:")
            for i, r in enumerate(refs, 1):
                st.write(f"- SOURCE-{i}: {r}")
            st.text(context)
    else:
        st.warning("No indexed knowledge matched this request. Generation will rely on supplied requirements only and will not invent protocol facts.")

    extracted = []
    if analysis:
        extracted = [ExtractedRequirement.model_validate(r) for r in analysis["requirements"]]

    request = GenerationRequest(
        feature=feature,
        user_story=story,
        acceptance_criteria=acceptance or "The supplied document requirements must be validated.",
        test_types=test_types,
        default_priority=priority,
        domain=domain,
        topology=topology,
        knowledge_context=context,
        knowledge_refs=refs,
        document_requirements=extracted,
    )

    with st.spinner("Generating document-driven scenarios, test cases and test plan with Qwen3..."):
        try:
            result = TestGenerator(provider=mode, model=model).generate(request)
        except Exception as e:
            st.error(f"Generation failed: {e}")
            st.stop()

    st.success(f"Generated at {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}")
    grounded_count = sum(bool(x.evidence_refs) for x in result.test_cases)
    doc_linked = sum(bool(x.document_requirement_ids) for x in result.test_cases)
    st.caption(f"Grounding: {grounded_count}/{len(result.test_cases)} test cases cite retrieved evidence; {doc_linked}/{len(result.test_cases)} link to extracted document requirements; plan cites {len(result.test_plan.evidence_refs)} source(s).")

    tabs = st.tabs(["Document Requirements", "Scenarios", "Test Cases", "Test Plan", "Traceability", "Sources", "Raw JSON"])
    with tabs[0]:
        if analysis and analysis["requirements"]:
            st.dataframe([
                {"ID": r["id"], "Keyword": r["keyword"], "Statement": r["statement"], "Evidence": r["evidence_ref"]}
                for r in analysis["requirements"]
            ], use_container_width=True)
        else:
            st.info("No normative document requirements were extracted.")

    with tabs[1]:
        for s in result.scenarios:
            with st.expander(f"{s.id} — {s.title} [{s.type}]"):
                st.write(s.description)
                st.write(f"**Priority:** {s.priority}")
                st.write(f"**Evidence:** {', '.join(s.evidence_refs) if s.evidence_refs else 'Requirement-derived / no direct source citation'}")

    with tabs[2]:
        st.dataframe([
            {"ID":t.id,"Requirement":t.requirement_id,"Scenario":t.scenario_id,"Title":t.title,"Type":t.type,"Priority":t.priority,"Automation":t.automation_candidate,"Document Reqs":", ".join(t.document_requirement_ids),"Evidence":", ".join(t.evidence_refs)}
            for t in result.test_cases
        ], use_container_width=True)
        for t in result.test_cases:
            with st.expander(f"{t.id} — {t.title}"):
                st.write(f"**Requirement:** {t.requirement_id} | **Scenario:** {t.scenario_id} | **Type:** {t.type} | **Priority:** {t.priority}")
                st.write(f"**Document Requirements:** {', '.join(t.document_requirement_ids) if t.document_requirement_ids else 'None'}")
                st.write(f"**Evidence:** {', '.join(t.evidence_refs) if t.evidence_refs else 'None'}")
                st.write(f"**Topology:** {t.topology if t.topology else 'Not specified — no topology was supplied or supported by the retrieved evidence.'}")
                st.write("**Preconditions**")
                for x in t.preconditions: st.write(f"- {x}")
                st.write("**Steps**")
                for i, x in enumerate(t.steps,1): st.write(f"{i}. {x}")
                st.write("**Expected Results**")
                for x in t.expected_results: st.write(f"- {x}")

    with tabs[3]:
        p=result.test_plan
        st.markdown(f"### {p.title}")
        for heading,value in [("Objective",p.objective),("Scope",p.scope),("Out of Scope",p.out_of_scope),("Test Strategy",p.test_strategy),("Environment",p.environment),("Entry Criteria",p.entry_criteria),("Exit Criteria",p.exit_criteria),("Risks",p.risks),("Dependencies",p.dependencies),("Automation Strategy",p.automation_strategy)]:
            st.markdown(f"**{heading}**")
            st.write(value)
        st.write(f"**Evidence:** {', '.join(p.evidence_refs) if p.evidence_refs else 'No direct source citation'}")

    with tabs[4]:
        if input_mode == "Document / RFC" and analysis and analysis["requirements"]:
            st.metric(
                "Document Requirements",
                len(result.traceability),
            )

            covered = sum(
                1 for x in result.traceability
                if x.covered
            )

            coverage = (
                round(
                    (covered / len(result.traceability)) * 100,
                    1,
                )
                if result.traceability
                else 0
            )

            st.metric(
                "Document Requirement Coverage",
                f"{coverage}%",
            )

            st.dataframe(
                [
                    {
                        "Requirement": x.requirement_id,
                        "Document Requirement": x.acceptance_criterion,
                        "Covered": x.covered,
                        "Test Cases": ", ".join(x.test_case_ids),
                        "Gap": x.gap,
                    }
                    for x in result.traceability
                ],
                use_container_width=True,
            )

        else:
            st.metric(
                "Acceptance Criteria",
                len(result.traceability),
            )

            covered = sum(
                1 for x in result.traceability
                if x.covered
            )

            coverage = (
                round(
                    (covered / len(result.traceability)) * 100,
                    1,
                )
                if result.traceability
                else 0
            )

            st.metric(
                "Acceptance-Criteria Coverage",
                f"{coverage}%",
            )

            st.dataframe(
                [
                    {
                        "Requirement": x.requirement_id,
                        "Acceptance Criteria": x.acceptance_criterion,
                        "Covered": x.covered,
                        "Test Cases": ", ".join(x.test_case_ids),
                        "Gap": x.gap,
                    }
                    for x in result.traceability
                ],
                use_container_width=True,
            )

        for gap in result.gaps:
            st.warning(gap)

    with tabs[5]:
        if refs:
            for i, r in enumerate(refs, 1): st.write(f"SOURCE-{i}: {r}")
        else:
            st.info("No retrieved sources were used.")

    with tabs[6]:
        payload=result.model_dump()
        st.json(payload)
        st.download_button("Download JSON", data=json.dumps(payload,indent=2), file_name="testgenai_document_driven_result.json", mime="application/json")
