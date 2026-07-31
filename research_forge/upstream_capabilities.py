from __future__ import annotations

"""Auditable claims about capabilities learned from external repositories.

This registry is deliberately stricter than a feature list.  A repository can
influence architecture without becoming a runtime dependency, and a configured
adapter is not a verified capability until an integration or real-run proof is
recorded.
"""

from enum import Enum
from pathlib import Path

from pydantic import Field, model_validator

from .models import StrictModel


class AdoptionStatus(str, Enum):
    VERIFIED_RUNTIME = "verified_runtime"
    PARTIAL_RUNTIME = "partial_runtime"
    ADAPTER_ONLY = "adapter_only"
    ARCHITECTURE_ONLY = "architecture_only"
    CODEX_SKILL_ONLY = "codex_skill_only"
    STUB_OR_MISSING = "stub_or_missing"
    NOT_ADOPTED = "not_adopted"


class ProofLevel(str, Enum):
    DESIGN = "design"
    SOURCE = "source"
    UNIT = "unit"
    INTEGRATION = "integration"
    REAL_RUN = "real_run"
    EXTERNAL_ACCEPTANCE = "external_acceptance"


class UpstreamCapabilityRecord(StrictModel):
    record_id: str = Field(pattern=r"^upstream-[a-z0-9-]+$")
    name: str
    kind: str
    source_url: str
    reviewed_at: str = "2026-07-31"
    license_note: str
    status: AdoptionStatus
    proof_levels: list[ProofLevel]
    upstream_capabilities: list[str]
    learned_capabilities: list[str]
    research_forge_evidence: list[str]
    real_run_evidence: list[str] = Field(default_factory=list)
    runtime_revalidated_at: str | None = None
    missing_or_bounded: list[str]
    permitted_claim: str

    @model_validator(mode="after")
    def proof_supports_status(self) -> "UpstreamCapabilityRecord":
        proof = set(self.proof_levels)
        if not self.upstream_capabilities:
            raise ValueError("every reviewed source needs an upstream capability summary")
        if self.status is AdoptionStatus.VERIFIED_RUNTIME and not (
            ProofLevel.INTEGRATION in proof and ProofLevel.REAL_RUN in proof
        ):
            raise ValueError(
                "verified_runtime requires integration and real_run proof"
            )
        if (
            self.status is AdoptionStatus.VERIFIED_RUNTIME
            and not self.real_run_evidence
        ):
            raise ValueError(
                "verified_runtime requires a durable real-run evidence locator"
            )
        if ProofLevel.REAL_RUN in proof and not self.real_run_evidence:
            raise ValueError("real_run proof requires a durable evidence locator")
        if self.real_run_evidence and ProofLevel.REAL_RUN not in proof:
            raise ValueError(
                "real-run evidence cannot be listed without REAL_RUN proof"
            )
        if self.status is AdoptionStatus.PARTIAL_RUNTIME and not (
            ProofLevel.SOURCE in proof and ProofLevel.UNIT in proof
        ):
            raise ValueError("partial_runtime requires source and unit proof")
        if self.status is AdoptionStatus.ARCHITECTURE_ONLY and (
            ProofLevel.INTEGRATION in proof or ProofLevel.REAL_RUN in proof
        ):
            raise ValueError(
                "architecture_only cannot claim integration or real-run proof"
            )
        return self


class UpstreamCapabilityAuditRecord(StrictModel):
    record_id: str
    status: AdoptionStatus
    source_locator_valid: bool
    checked_local_evidence: list[str]
    missing_local_evidence: list[str]
    code_evidence: list[str]
    test_evidence: list[str]
    real_run_evidence: list[str]
    findings: list[str]
    claim_supported: bool


class UpstreamCapabilityAuditReport(StrictModel):
    schema_version: int = 1
    repository_root: str
    record_count: int
    supported_count: int
    unsupported_count: int
    records: list[UpstreamCapabilityAuditRecord]


def _evidence_file(locator: str, root: Path) -> Path | None:
    """Resolve a durable local evidence locator.

    URI-like locators describe installed or deliberately missing Skills and are
    not repository files. Markdown fragments are allowed so a real-run proof
    can point to the exact section of a durable report.
    """

    if "://" in locator:
        return None
    path_text = locator.split("#", 1)[0].strip()
    path = Path(path_text)
    return path if path.is_absolute() else root / path


def audit_upstream_capabilities(
    repository_root: str | Path,
) -> UpstreamCapabilityAuditReport:
    """Check that every adoption claim has evidence appropriate to its level."""

    root = Path(repository_root).resolve()
    audited: list[UpstreamCapabilityAuditRecord] = []
    for item in UPSTREAM_CAPABILITIES:
        findings: list[str] = []
        source_locator_valid = (
            item.source_url.startswith("https://")
            and item.source_url.rstrip("/")
            not in {"https:", "https://github.com"}
        ) or item.source_url.startswith(("local-skill://", "missing-skill://"))
        if not source_locator_valid:
            findings.append("invalid or placeholder upstream source locator")

        checked: list[str] = []
        missing: list[str] = []
        for locator in [*item.research_forge_evidence, *item.real_run_evidence]:
            path = _evidence_file(locator, root)
            if path is None:
                continue
            checked.append(locator)
            if not path.exists():
                missing.append(locator)
        if missing:
            findings.append(
                "missing durable local evidence: " + ", ".join(missing)
            )

        normalized = [
            locator.replace("\\", "/")
            for locator in item.research_forge_evidence
        ]
        code_evidence = [
            locator
            for locator in normalized
            if locator.startswith(
                (
                    "research_forge/",
                    "research-forge-ui/src/",
                    "scripts/",
                )
            )
        ]
        test_evidence = [
            locator
            for locator in normalized
            if locator.startswith("tests/")
            or locator.endswith(".test.mjs")
            or "/tests/" in locator
        ]

        if ProofLevel.UNIT in item.proof_levels and not test_evidence:
            findings.append("unit proof is declared without a test locator")
        if ProofLevel.INTEGRATION in item.proof_levels:
            if not code_evidence:
                findings.append(
                    "integration proof is declared without platform code evidence"
                )
            if not test_evidence:
                findings.append(
                    "integration proof is declared without integration test evidence"
                )
        if item.status in {
            AdoptionStatus.VERIFIED_RUNTIME,
            AdoptionStatus.PARTIAL_RUNTIME,
        }:
            if not code_evidence:
                findings.append("runtime status lacks platform code evidence")
            if not test_evidence:
                findings.append("runtime status lacks test evidence")
        if item.status is AdoptionStatus.ADAPTER_ONLY and not code_evidence:
            findings.append(
                "adapter_only status lacks an implemented platform adapter"
            )
        if ProofLevel.REAL_RUN in item.proof_levels:
            if not item.real_run_evidence:
                findings.append("real-run proof has no durable evidence")
            if not item.runtime_revalidated_at:
                findings.append("real-run proof has no revalidation date")

        audited.append(
            UpstreamCapabilityAuditRecord(
                record_id=item.record_id,
                status=item.status,
                source_locator_valid=source_locator_valid,
                checked_local_evidence=checked,
                missing_local_evidence=missing,
                code_evidence=code_evidence,
                test_evidence=test_evidence,
                real_run_evidence=item.real_run_evidence,
                findings=findings,
                claim_supported=not findings,
            )
        )
    supported = sum(record.claim_supported for record in audited)
    return UpstreamCapabilityAuditReport(
        repository_root=str(root),
        record_count=len(audited),
        supported_count=supported,
        unsupported_count=len(audited) - supported,
        records=audited,
    )


UPSTREAM_CAPABILITIES: tuple[UpstreamCapabilityRecord, ...] = (
    UpstreamCapabilityRecord(
        record_id="upstream-autoresearchclaw",
        name="AutoResearchClaw",
        kind="repository",
        source_url="https://github.com/aiming-lab/AutoResearchClaw",
        license_note="MIT",
        status=AdoptionStatus.ARCHITECTURE_ONLY,
        proof_levels=[ProofLevel.DESIGN, ProofLevel.SOURCE],
        upstream_capabilities=[
            "23-stage, eight-phase idea-to-paper pipeline",
            "six human-intervention modes with pause, approve, reject, and guidance",
            "persistent ACP sessions across stages and pluggable SKILL.md loading",
            "branch exploration, experiment self-repair, citation verification, and cross-run lessons",
            "domain-specific experiment executors and ARC-Bench task manifests",
        ],
        learned_capabilities=[
            "multi-stage research lifecycle",
            "co-pilot gates",
            "literature and experiment repair loops",
        ],
        research_forge_evidence=[
            "docs/design-notes.md",
            "docs/workflow-v2.md",
        ],
        missing_or_bounded=[
            "Research Forge does not run AutoResearchClaw's 23-stage engine",
            "no compatibility claim with AutoResearchClaw modes or artifacts",
            "no general skill loader, six-mode co-pilot controller, or cross-run lesson injection",
            "no ARC-Bench or domain-executor compatibility",
        ],
        permitted_claim="Workflow architecture was compared with AutoResearchClaw.",
    ),
    UpstreamCapabilityRecord(
        record_id="upstream-karpathy-autoresearch",
        name="autoresearch",
        kind="repository",
        source_url="https://github.com/karpathy/autoresearch",
        license_note="MIT",
        status=AdoptionStatus.ARCHITECTURE_ONLY,
        proof_levels=[ProofLevel.DESIGN, ProofLevel.SOURCE],
        upstream_capabilities=[
            "single-GPU autonomous iteration over a fixed training program",
            "five-minute bounded runs scored by one scalar validation metric",
            "keep-or-discard research log that preserves experiment history",
        ],
        learned_capabilities=[
            "fixed-budget scalar evaluator loop",
            "keep-or-discard experiment iteration",
        ],
        research_forge_evidence=["docs/design-notes.md"],
        missing_or_bounded=[
            "no train.py mutation loop",
            "Research Forge is not a single-GPU val_bpb optimizer",
        ],
        permitted_claim="The bounded evaluator loop informed experiment design.",
    ),
    UpstreamCapabilityRecord(
        record_id="upstream-ai-scientist-v1",
        name="AI Scientist",
        kind="repository",
        source_url="https://github.com/SakanaAI/AI-Scientist",
        license_note="See upstream repository",
        status=AdoptionStatus.ARCHITECTURE_ONLY,
        proof_levels=[ProofLevel.DESIGN, ProofLevel.SOURCE],
        upstream_capabilities=[
            "template-based idea generation, code modification, experiment execution, figures, paper writing, and review",
            "automated novelty search and LaTeX production",
        ],
        learned_capabilities=[
            "idea-to-experiment-to-paper lifecycle",
            "template-bound experiment execution warning",
        ],
        research_forge_evidence=[
            "docs/research-forge-positioning-2026-07-19.md"
        ],
        missing_or_bounded=[
            "no AI-Scientist template runtime is embedded",
            "no compatibility with its generated experiments",
        ],
        permitted_claim="Research Forge was positioned against AI Scientist.",
    ),
    UpstreamCapabilityRecord(
        record_id="upstream-ai-scientist-v2",
        name="AI Scientist v2",
        kind="repository",
        source_url="https://github.com/SakanaAI/AI-Scientist-v2",
        license_note="See upstream repository",
        status=AdoptionStatus.ARCHITECTURE_ONLY,
        proof_levels=[ProofLevel.DESIGN, ProofLevel.SOURCE],
        upstream_capabilities=[
            "template-free experiment implementation",
            "progressive agentic tree search over experiment and manuscript states",
            "automated paper writing and review from explored nodes",
        ],
        learned_capabilities=[
            "progressive agentic tree search",
            "journaled experiment nodes",
        ],
        research_forge_evidence=["docs/design-notes.md"],
        missing_or_bounded=[
            "tree search is not implemented",
            "Research Forge does not execute AI Scientist v2 generated code",
        ],
        permitted_claim="Its search-node model informed deferred design work.",
    ),
    UpstreamCapabilityRecord(
        record_id="upstream-deepscientist",
        name="DeepScientist",
        kind="repository",
        source_url="https://github.com/ResearAI/DeepScientist",
        license_note="See upstream repository",
        status=AdoptionStatus.ARCHITECTURE_ONLY,
        proof_levels=[ProofLevel.DESIGN, ProofLevel.SOURCE],
        upstream_capabilities=[
            "local-first, one-repository-per-quest persistent research workspace",
            "baseline reproduction, Findings Memory, research maps, and iterative branching",
            "human takeover plus Codex, Claude, Kimi, and OpenCode runners",
            "web, TUI, messaging, figure, LaTeX, and paper-delivery surfaces",
        ],
        learned_capabilities=[
            "durable quest and artifact state",
            "human takeover",
            "baseline reproduction before optimization",
        ],
        research_forge_evidence=["docs/design-notes.md"],
        missing_or_bounded=[
            "no Bayesian optimization research map",
            "no one-repository-per-quest compatibility",
        ],
        permitted_claim="Durable study state was compared with DeepScientist.",
    ),
    UpstreamCapabilityRecord(
        record_id="upstream-agent-laboratory",
        name="Agent Laboratory",
        kind="repository",
        source_url="https://github.com/SamuelSchmidgall/AgentLaboratory",
        license_note="See upstream repository",
        status=AdoptionStatus.ARCHITECTURE_ONLY,
        proof_levels=[ProofLevel.DESIGN, ProofLevel.SOURCE],
        upstream_capabilities=[
            "end-to-end literature, experimentation, and report workflow",
            "phase-specialized agents, checkpoints, and iterative researcher feedback",
        ],
        learned_capabilities=[
            "phase-specific agent roles",
            "checkpointed co-pilot workflow",
        ],
        research_forge_evidence=["docs/design-notes.md"],
        missing_or_bounded=[
            "no AgentRxiv runtime",
            "no Agent Laboratory checkpoint compatibility",
        ],
        permitted_claim="Role separation informed the four-phase workflow.",
    ),
    UpstreamCapabilityRecord(
        record_id="upstream-codescientist",
        name="CodeScientist",
        kind="repository",
        source_url="https://github.com/allenai/codescientist",
        license_note="See upstream repository",
        status=AdoptionStatus.ARCHITECTURE_ONLY,
        proof_levels=[ProofLevel.DESIGN, ProofLevel.SOURCE],
        upstream_capabilities=[
            "autonomous discovery restricted to code-based experiments",
            "multiple independent attempts with container isolation and meta-analysis",
        ],
        learned_capabilities=[
            "containerized code-expressible experiments",
            "independent attempts followed by meta-analysis",
        ],
        research_forge_evidence=[
            "docs/research-forge-positioning-2026-07-19.md"
        ],
        missing_or_bounded=[
            "no CodeScientist mutation or meta-analysis runtime",
        ],
        permitted_claim="Its isolated experiment pattern informed Stage 3.",
    ),
    UpstreamCapabilityRecord(
        record_id="upstream-ai-researcher",
        name="AI-Researcher",
        kind="repository",
        source_url="https://github.com/HKUDS/AI-Researcher",
        license_note="See upstream repository",
        status=AdoptionStatus.ARCHITECTURE_ONLY,
        proof_levels=[ProofLevel.DESIGN, ProofLevel.SOURCE],
        upstream_capabilities=[
            "idea-driven and reference-paper-driven research entry modes",
            "multi-agent literature, algorithm design, implementation, validation, and manuscript production",
        ],
        learned_capabilities=[
            "idea and reference-paper entry modes",
            "end-to-end research lifecycle",
        ],
        research_forge_evidence=[
            "docs/research-forge-positioning-2026-07-19.md"
        ],
        missing_or_bounded=["no AI-Researcher benchmark compatibility"],
        permitted_claim="Its dual-entry pattern informed Research Forge entry modes.",
    ),
    UpstreamCapabilityRecord(
        record_id="upstream-xscientist",
        name="XScientist",
        kind="repository",
        source_url="https://github.com/smileformylove/XScientist",
        license_note="Apache-2.0 badge; verify upstream files before redistribution",
        status=AdoptionStatus.ARCHITECTURE_ONLY,
        proof_levels=[ProofLevel.DESIGN, ProofLevel.SOURCE],
        upstream_capabilities=[
            "ARA v1 git-like research artifact protocol with normalized hashes",
            "fork, inspect, re-execute, diff, and continue any evidence node",
            "claimref bindings, exploration graphs, repair history, and environment fingerprints",
            "long-running daemon, feedback boards, integrity forensics, and isolated executors",
        ],
        learned_capabilities=[
            "git-like research runs",
            "claim-to-evidence references",
            "fork, re-execute, review, and repair",
        ],
        research_forge_evidence=[
            "docs/research-forge-positioning-2026-07-19.md",
            "docs/workflow-v2.md",
        ],
        missing_or_bounded=["no ARA protocol compatibility"],
        permitted_claim="Lineage and successor-run ideas were compared with XScientist.",
    ),
    UpstreamCapabilityRecord(
        record_id="upstream-paperqa",
        name="PaperQA2",
        kind="repository",
        source_url="https://github.com/Future-House/paper-qa",
        license_note="Apache-2.0",
        status=AdoptionStatus.VERIFIED_RUNTIME,
        proof_levels=[
            ProofLevel.SOURCE,
            ProofLevel.UNIT,
            ProofLevel.INTEGRATION,
            ProofLevel.REAL_RUN,
        ],
        upstream_capabilities=[
            "metadata-aware full-text scientific RAG",
            "agentic evidence retrieval, reranking, and citation-grounded answers",
            "multi-document corpus indexing and question answering",
        ],
        learned_capabilities=[
            "metadata-aware full-text retrieval",
            "evidence-bundle synthesis",
            "citation-bound answer validation",
        ],
        research_forge_evidence=[
            "docs/paperqa-integration.md",
            "research_forge/retrieval/evidence/paperqa_service.py",
            "research_forge/retrieval/evidence/paperqa_runtime.py",
            "tests/test_paperqa_synthesis_v2.py",
            "tests/test_paperqa_comparison.py",
        ],
        real_run_evidence=[
            "docs/paperqa-integration.md#paperqa-comparison",
            "docs/paperqa-runtime-revalidation-2026-07-31.md",
        ],
        runtime_revalidated_at="2026-07-31",
        missing_or_bounded=[
            "smoke comparisons do not establish general superiority over PaperQA",
            "answers remain bounded by frozen Evidence Bundles",
            "the 2026-07-31 rerun revalidated retrieval only; the most recent "
            "Codex synthesis acceptance remains the 2026-07-24 run",
        ],
        permitted_claim="Pinned PaperQA retrieval and evidence analysis run inside the gateway.",
    ),
    UpstreamCapabilityRecord(
        record_id="upstream-airs-bench",
        name="AIRS-Bench",
        kind="repository",
        source_url="https://github.com/facebookresearch/airs-bench",
        license_note="CC-BY-NC-4.0",
        status=AdoptionStatus.VERIFIED_RUNTIME,
        proof_levels=[
            ProofLevel.SOURCE,
            ProofLevel.UNIT,
            ProofLevel.INTEGRATION,
            ProofLevel.REAL_RUN,
        ],
        upstream_capabilities=[
            "end-to-end AI research benchmark across diverse ML tasks",
            "official RAD task packaging, submission validation, and normalized scoring",
        ],
        learned_capabilities=[
            "official RAD task import",
            "normalized score and valid-submission semantics",
        ],
        research_forge_evidence=[
            "research_forge/official_airs.py",
            "research_forge/official_airs_matrix.py",
            "research_forge/official_airs_audit.py",
            "tests/test_official_airs.py",
            "tests/test_official_airs_audit.py",
            "docs/stage-3-execution-kernel.md",
            "docs/official-airs-rad-validation-2026-07-31.md",
        ],
        real_run_evidence=["docs/official-airs-rad-validation-2026-07-31.md"],
        runtime_revalidated_at="2026-07-31",
        missing_or_bounded=[
            "four official RAD tasks were executed locally; this is not the complete AIRS matrix",
            "no leaderboard submission or external acceptance is claimed",
            "AIRS-lite output is not an official benchmark result",
        ],
        permitted_claim=(
            "Four official AIRS RAD tasks completed 40 isolated local cells with "
            "the unchanged official evaluator; leaderboard performance and external "
            "acceptance are not claimed."
        ),
    ),
    UpstreamCapabilityRecord(
        record_id="upstream-aira-dojo",
        name="AIRA Dojo",
        kind="repository",
        source_url="https://github.com/facebookresearch/aira-dojo",
        license_note="CC-BY-NC-4.0",
        status=AdoptionStatus.ARCHITECTURE_ONLY,
        proof_levels=[ProofLevel.DESIGN, ProofLevel.SOURCE],
        upstream_capabilities=[
            "composable task, solver, operator, interpreter, and runner framework",
            "local, container, and Slurm-oriented evaluation infrastructure",
            "journaled search operators for AI research agents",
        ],
        learned_capabilities=[
            "task, solver, operator, runner separation",
            "isolated large-scale execution design",
        ],
        research_forge_evidence=[
            "research_forge/container_execution.py",
            "tests/test_container_execution.py",
        ],
        missing_or_bounded=[
            "full AIRA Dojo and Slurm harness are not deployed",
            "no thousand-agent scale acceptance",
            "Research Forge's container runner does not import AIRA Dojo",
        ],
        permitted_claim="Selected execution abstractions were adapted, not the full Dojo.",
    ),
    UpstreamCapabilityRecord(
        record_id="upstream-drawio",
        name="draw.io",
        kind="repository",
        source_url="https://github.com/jgraph/drawio",
        license_note="Apache-2.0 code; upstream asset restrictions still apply",
        status=AdoptionStatus.VERIFIED_RUNTIME,
        proof_levels=[
            ProofLevel.SOURCE,
            ProofLevel.UNIT,
            ProofLevel.INTEGRATION,
            ProofLevel.REAL_RUN,
        ],
        upstream_capabilities=[
            "client-side editable diagram and whiteboard authoring",
            "desktop, browser, Docker, SVG, PDF, and image export paths",
        ],
        learned_capabilities=[
            "editable diagram source",
            "desktop SVG/PDF export",
        ],
        research_forge_evidence=[
            "research_forge/ai_drawio.py",
            "research_forge/drawio_backend.py",
            "tests/test_ai_drawio.py",
            "tests/test_drawio_backend.py",
        ],
        real_run_evidence=[
            "docs/drawio-runtime-revalidation-2026-07-31.md",
        ],
        runtime_revalidated_at="2026-07-31",
        missing_or_bounded=[
            "draw.io is not treated as a scientific evaluator",
            "real-time collaboration is not provided by this integration",
        ],
        permitted_claim=(
            "Research Forge writes and audits editable .drawio sources and "
            "exports them with the revalidated local draw.io Desktop path."
        ),
    ),
    UpstreamCapabilityRecord(
        record_id="upstream-next-ai-drawio",
        name="next-ai-draw-io",
        kind="repository",
        source_url="https://github.com/DayuanJiang/next-ai-draw-io",
        license_note="Apache-2.0",
        status=AdoptionStatus.ADAPTER_ONLY,
        proof_levels=[ProofLevel.SOURCE, ProofLevel.UNIT],
        upstream_capabilities=[
            "natural-language creation and revision of draw.io diagrams",
            "image, PDF, and text-to-diagram workflows",
            "version history, interactive chat, desktop app, and MCP server",
        ],
        learned_capabilities=[
            "MCP configuration for AI-assisted diagram editing",
            "audit of AI-edited draw.io output",
        ],
        research_forge_evidence=[
            "research_forge/ai_drawio.py",
            "docs/next-ai-drawio.md",
            "tests/test_ai_drawio.py",
        ],
        missing_or_bounded=[
            "live MCP editing has not received a recent end-to-end acceptance run",
        ],
        permitted_claim="MCP configuration and output audit exist; live editing is not yet verified.",
    ),
    UpstreamCapabilityRecord(
        record_id="upstream-sci-ssci-skills",
        name="sci-ssci-skills",
        kind="repository-and-skill",
        source_url="https://github.com/Yila-AI/sci-ssci-skills",
        license_note="Apache-2.0",
        status=AdoptionStatus.PARTIAL_RUNTIME,
        proof_levels=[
            ProofLevel.DESIGN,
            ProofLevel.SOURCE,
            ProofLevel.UNIT,
            ProofLevel.INTEGRATION,
        ],
        upstream_capabilities=[
            "SCI/SSCI planning, drafting, revising, and polishing skills",
            "reader-question section planning and target-journal modeling",
            "evidence, citation, and claim-strength preservation",
        ],
        learned_capabilities=[
            "section-by-section reader-question writing",
            "target-journal modeling",
            "evidence-preserving polishing",
        ],
        research_forge_evidence=[
            "research_forge/sci_ssci_writing.py",
            "docs/third-party/sci-ssci-skills.md",
            "tests/test_sci_ssci_writing.py",
        ],
        missing_or_bounded=[
            "the upstream skill runtime and corpus builder are not embedded",
            "venue fit still depends on verified local policy inputs",
        ],
        permitted_claim="Core writing constraints are locally adapted with attribution.",
    ),
    UpstreamCapabilityRecord(
        record_id="upstream-awesome-ai-research-writing",
        name="awesome-ai-research-writing",
        kind="repository-and-curated-writing-guide",
        source_url="https://github.com/Leey21/awesome-ai-research-writing",
        license_note=(
            "No repository-wide license was verified during this review; "
            "only general writing ideas are referenced"
        ),
        status=AdoptionStatus.ARCHITECTURE_ONLY,
        proof_levels=[ProofLevel.DESIGN, ProofLevel.SOURCE],
        upstream_capabilities=[
            "curated prompts for Chinese-to-English academic rewriting, "
            "paragraph-level polishing, results analysis, and reviewer simulation",
            "catalog of third-party research-writing Skills and installation patterns",
        ],
        learned_capabilities=[
            "reader-facing paragraphs instead of audit-field enumeration",
            "reduced mechanical connectives, ornamental formatting, and code-like prose",
            "reviewer-oriented manuscript audit before submission",
        ],
        research_forge_evidence=[
            "docs/manuscript-writing-audit-2026-07-18.md",
            "research_forge/paper_pipeline.py",
            "research_forge/paper_humanize.py",
            "tests/test_stage_four_publication_control.py",
        ],
        missing_or_bounded=[
            "the upstream repository is not installed or executed by Research Forge",
            "its prompt collection is not a scientific authority or evidence source",
            "Research Forge does not redistribute its prompt text",
        ],
        permitted_claim=(
            "General paragraph and reviewer-audit ideas informed the local writing "
            "pipeline; no upstream runtime integration is claimed."
        ),
    ),
    UpstreamCapabilityRecord(
        record_id="upstream-nuwa-panel",
        name="Nuwa Scientist Panel",
        kind="local-skill",
        source_url="https://github.com/CKwin26/Auto-Agentic-Research-Forge/tree/main/skills/nuwa-scientist-panel",
        license_note="project-owned skill",
        status=AdoptionStatus.PARTIAL_RUNTIME,
        proof_levels=[
            ProofLevel.SOURCE,
            ProofLevel.UNIT,
            ProofLevel.INTEGRATION,
        ],
        upstream_capabilities=[
            "blinded Feynman-, Tukey-, Shannon-, and Popper-inspired claim review",
            "fixed three-person routing with deterministic veto and abstention aggregation",
            "human adjudication queue for mixed or abstaining outcomes",
        ],
        learned_capabilities=[
            "blinded persona reviews",
            "deterministic veto and abstention rules",
        ],
        research_forge_evidence=[
            "research_forge/nuwa_panel.py",
            "research_forge/paper_expansion.py",
            "tests/test_paper_authoring.py",
            "tests/test_nuwa_panel.py",
        ],
        missing_or_bounded=[
            "exact three-person claim routing, packet blinding, hashing, and deterministic aggregation are implemented",
            "the human-adjudication queue is persisted but has no dedicated owner-decision UI",
            "a recent real-model end-to-end acceptance run is still missing",
        ],
        permitted_claim=(
            "Stage 4 implements the exact Nuwa protocol as a same-model advisory "
            "panel; real-model and human-queue acceptance remain pending."
        ),
    ),
    UpstreamCapabilityRecord(
        record_id="upstream-academic-humanizer",
        name="academic-humanizer",
        kind="skill",
        source_url="https://github.com/AIScientists-Dev/academic-humanizer",
        license_note="MIT",
        status=AdoptionStatus.PARTIAL_RUNTIME,
        proof_levels=[
            ProofLevel.SOURCE,
            ProofLevel.UNIT,
            ProofLevel.INTEGRATION,
        ],
        upstream_capabilities=[
            "claim-preserving academic prose revision",
            "AI-writing-tell removal without detector-evasion promises",
            "paper, thesis, rebuttal, NSF, and NIH writing modes",
        ],
        learned_capabilities=[
            "claim-preserving academic copy edit",
            "calibrated verbs and AI-tell removal",
            "author voice profile and semantic diff",
        ],
        research_forge_evidence=[
            "research_forge/paper_humanize.py",
            "research_forge/paper_expansion.py",
            "tests/test_paper_expansion_callouts.py",
        ],
        missing_or_bounded=[
            "funding-proposal mode is not a Stage 4 product workflow",
            "humanization never changes numbers, citations, or verdicts",
        ],
        permitted_claim="The manuscript pipeline adapts its claim-preserving editing discipline.",
    ),
    UpstreamCapabilityRecord(
        record_id="upstream-academic-research-suite",
        name="Academic Research Suite",
        kind="skill-suite",
        source_url="https://github.com/Imbad0202/academic-research-skills",
        license_note="local snapshot v3.17; current upstream v3.19 is CC-BY-NC",
        status=AdoptionStatus.CODEX_SKILL_ONLY,
        proof_levels=[ProofLevel.DESIGN, ProofLevel.SOURCE],
        upstream_capabilities=[
            "deep research, systematic review, paper writing, peer review, and full-pipeline modes",
            "multi-agent integrity gates, Material Passport, claim audit, and citation locators",
            "style calibration, AI disclosure, cross-model verification, and experiment provenance intake",
        ],
        learned_capabilities=[
            "deep research, paper, review, and full-pipeline roles",
            "Material Passport and failure-mode gates",
        ],
        research_forge_evidence=[
            "local-skill://academic-research-suite/SKILL.md"
        ],
        missing_or_bounded=[
            "full suite runtime is not invoked automatically by Research Forge",
            "RAISE and PRISMA claims require separate runtime evidence",
        ],
        permitted_claim="Available to Codex as a point-in-time skill snapshot, not a platform runtime.",
    ),
    UpstreamCapabilityRecord(
        record_id="upstream-experiment-agent",
        name="experiment-agent",
        kind="skill",
        source_url="https://github.com/Imbad0202/experiment-agent",
        license_note="see pinned local suite manifest and upstream",
        status=AdoptionStatus.CODEX_SKILL_ONLY,
        proof_levels=[ProofLevel.DESIGN, ProofLevel.SOURCE],
        upstream_capabilities=[
            "execution and monitoring of user-supplied computational experiments",
            "human-study protocol and ethics checklist support",
            "statistical fallacy checks and reproducibility verification",
        ],
        learned_capabilities=[
            "run, validate, and reproduce user-supplied experiments",
            "separation of statistical interpretation from scientific conclusion",
        ],
        research_forge_evidence=[
            "local-skill://academic-research-suite/SKILL.md"
        ],
        missing_or_bounded=[
            "it does not authorize automatic code generation or modification",
            "Research Forge Stage 3 is an independent implementation",
        ],
        permitted_claim="Its constraints informed Stage 3; it is not the Stage 3 engine.",
    ),
    UpstreamCapabilityRecord(
        record_id="upstream-ui-ux-pro-max",
        name="UI/UX Pro Max",
        kind="skill",
        source_url="https://github.com/nextlevelbuilder/ui-ux-pro-max-skill",
        license_note="verify upstream license before redistribution",
        status=AdoptionStatus.ARCHITECTURE_ONLY,
        proof_levels=[ProofLevel.DESIGN, ProofLevel.SOURCE],
        upstream_capabilities=[
            "searchable UI styles, palettes, typography, components, and UX patterns",
            "stack-aware design-system generation",
            "accessibility, responsive layout, and loading-state guidance",
        ],
        learned_capabilities=[
            "searchable UI pattern and palette guidance",
            "design-system artifact generation",
            "accessibility and loading-state checklist",
        ],
        research_forge_evidence=[
            "research-forge-ui/design-system/research-forge/MASTER.md",
            "research-forge-ui/src/workflow.test.mjs",
        ],
        missing_or_bounded=[
            "no complete automated accessibility acceptance suite",
            "current Ocean palette is a later Research Forge design decision",
        ],
        permitted_claim="The UI used its design workflow and checklist; conformance is partial.",
    ),
    UpstreamCapabilityRecord(
        record_id="upstream-read-wechat-skill",
        name="read-wechat-articles",
        kind="skill",
        source_url="local-skill://read-wechat-articles/SKILL.md",
        license_note="local placeholder; no usable upstream metadata",
        status=AdoptionStatus.STUB_OR_MISSING,
        proof_levels=[ProofLevel.SOURCE],
        upstream_capabilities=[
            "intended WeChat article browsing workflow; no executable specification is present",
        ],
        learned_capabilities=[],
        research_forge_evidence=[
            "local-skill://read-wechat-articles/SKILL.md"
        ],
        missing_or_bounded=[
            "SKILL.md is still a TODO stub",
            "no scripts or executable browsing workflow",
            "RedFox retrieval provider is a separate platform capability",
        ],
        permitted_claim="The named WeChat skill is not implemented.",
    ),
    UpstreamCapabilityRecord(
        record_id="upstream-self-play-skill",
        name="self-play skill",
        kind="skill",
        source_url="missing-skill://self-play",
        license_note="no installed skill or upstream reference found",
        status=AdoptionStatus.STUB_OR_MISSING,
        proof_levels=[ProofLevel.DESIGN],
        upstream_capabilities=[
            "requested self-play ideation capability; no installed skill or upstream specification was found",
        ],
        learned_capabilities=[],
        research_forge_evidence=["examples/bounded-self-play-demo/"],
        missing_or_bounded=[
            "no installed self-play SKILL.md",
            "examples and claim-discovery terms are not a reusable skill",
        ],
        permitted_claim="Only a bounded example exists; no self-play skill is installed.",
    ),
    UpstreamCapabilityRecord(
        record_id="upstream-evidently",
        name="Evidently",
        kind="repository",
        source_url="https://github.com/evidentlyai/evidently",
        license_note="see upstream repository",
        status=AdoptionStatus.ADAPTER_ONLY,
        proof_levels=[ProofLevel.DESIGN, ProofLevel.SOURCE, ProofLevel.UNIT],
        upstream_capabilities=[
            "ML, LLM, and data-pipeline evaluation, testing, and monitoring",
            "100-plus metrics with structured reports and test conditions",
        ],
        learned_capabilities=["normalized external validation records"],
        research_forge_evidence=[
            "research_forge/external_validators.py",
            "docs/open-source-scientific-validity-integrations.md",
            "tests/test_scientific_validity.py",
        ],
        missing_or_bounded=[
            "package is not installed",
            "no executor or real-run acceptance",
        ],
        permitted_claim="Research Forge can normalize supplied Evidently reports.",
    ),
    UpstreamCapabilityRecord(
        record_id="upstream-great-expectations",
        name="Great Expectations",
        kind="repository",
        source_url="https://github.com/fivetran/great_expectations",
        license_note="see upstream repository",
        status=AdoptionStatus.VERIFIED_RUNTIME,
        proof_levels=[
            ProofLevel.DESIGN,
            ProofLevel.SOURCE,
            ProofLevel.UNIT,
            ProofLevel.INTEGRATION,
            ProofLevel.REAL_RUN,
        ],
        upstream_capabilities=[
            "declarative data-quality expectations and validation runs",
            "generated validation documentation and reusable data contracts",
        ],
        learned_capabilities=[
            "hash-bound frozen tabular expectation contracts",
            "offline structural-data qualification before scientific admission",
        ],
        research_forge_evidence=[
            "research_forge/external_validators.py",
            "research_forge/great_expectations_gate.py",
            "research_forge/resource_validation.py",
            "scripts/validate_great_expectations_gate.py",
            "docs/open-source-scientific-validity-integrations.md",
            "tests/test_scientific_validity.py",
            "tests/test_great_expectations_gate.py",
            "tests/test_resource_validation.py",
        ],
        real_run_evidence=[
            "docs/great-expectations-controlled-gate-acceptance-2026-07-31.md",
        ],
        runtime_revalidated_at="2026-07-31",
        missing_or_bounded=[
            "the controlled gate qualifies structural tabular properties only",
            "passing expectations cannot establish label semantics, causal validity, or a scientific verdict",
        ],
        permitted_claim=(
            "Research Forge runs a hash-bound, offline Great Expectations gate "
            "for frozen tabular structure; scientific meaning remains outside that gate."
        ),
    ),
    UpstreamCapabilityRecord(
        record_id="upstream-manubot",
        name="Manubot",
        kind="repository",
        source_url="https://github.com/manubot/manubot",
        license_note="see upstream repository",
        status=AdoptionStatus.STUB_OR_MISSING,
        proof_levels=[ProofLevel.DESIGN, ProofLevel.SOURCE],
        upstream_capabilities=[
            "persistent-identifier citation metadata retrieval",
            "Pandoc-oriented manuscript processing, templating, and AI revision utilities",
        ],
        learned_capabilities=["citation and manuscript processing design"],
        research_forge_evidence=[
            "docs/open-source-scientific-validity-integrations.md"
        ],
        missing_or_bounded=["no installed executor or accepted output"],
        permitted_claim=(
            "Manubot was reviewed as a possible validator; Research Forge has "
            "no Manubot adapter or runtime."
        ),
    ),
    UpstreamCapabilityRecord(
        record_id="upstream-statcheck",
        name="statcheck",
        kind="repository",
        source_url="https://github.com/MicheleNuijten/statcheck",
        license_note="see upstream repository",
        status=AdoptionStatus.STUB_OR_MISSING,
        proof_levels=[ProofLevel.DESIGN, ProofLevel.SOURCE],
        upstream_capabilities=[
            "automatic consistency checks for APA-style null-hypothesis significance tests",
        ],
        learned_capabilities=["APA-style NHST consistency-check design"],
        research_forge_evidence=[
            "docs/open-source-scientific-validity-integrations.md"
        ],
        missing_or_bounded=["no R runtime integration or accepted report"],
        permitted_claim=(
            "statcheck was reviewed as a possible validator; Research Forge "
            "has no statcheck adapter or runtime."
        ),
    ),
    UpstreamCapabilityRecord(
        record_id="upstream-dvc",
        name="DVC",
        kind="repository",
        source_url="https://github.com/treeverse/dvc",
        license_note="Apache-2.0",
        status=AdoptionStatus.ARCHITECTURE_ONLY,
        proof_levels=[ProofLevel.DESIGN, ProofLevel.SOURCE],
        upstream_capabilities=[
            "versioned data, pipelines, models, metrics, and ML experiment state",
        ],
        learned_capabilities=["data and artifact versioning pattern"],
        research_forge_evidence=[
            "docs/open-source-scientific-validity-integrations.md"
        ],
        missing_or_bounded=["no mandatory DVC repository or remote"],
        permitted_claim="DVC-style versioning informed design; DVC is optional.",
    ),
    UpstreamCapabilityRecord(
        record_id="upstream-osf",
        name="OSF",
        kind="repository-and-platform",
        source_url="https://github.com/CenterForOpenScience/osf.io",
        license_note="Apache-2.0",
        status=AdoptionStatus.ARCHITECTURE_ONLY,
        proof_levels=[ProofLevel.DESIGN, ProofLevel.SOURCE],
        upstream_capabilities=[
            "open-science project, registration, storage, sharing, and preservation platform",
            "time-stamped registrations that preserve a study plan before analysis",
        ],
        learned_capabilities=[
            "immutable frozen research contracts",
            "versioned successor rather than in-place protocol mutation",
        ],
        research_forge_evidence=[
            "research_forge/workflow_domain.py",
            "research_forge/stage_two.py",
            "tests/test_workflow_domain.py",
            "tests/test_stage_two.py",
        ],
        missing_or_bounded=[
            "Research Forge does not submit registrations to OSF",
            "no OSF API or registration-schema compatibility is claimed",
        ],
        permitted_claim="OSF registration semantics informed contract freezing and successors.",
    ),
    UpstreamCapabilityRecord(
        record_id="upstream-mlflow",
        name="MLflow",
        kind="repository-and-platform",
        source_url="https://github.com/mlflow/mlflow",
        license_note="Apache-2.0",
        status=AdoptionStatus.ARCHITECTURE_ONLY,
        proof_levels=[ProofLevel.DESIGN, ProofLevel.SOURCE],
        upstream_capabilities=[
            "tracking of runs, parameters, metrics, artifacts, models, and lineage",
            "evaluation, observability, prompt management, and model registry surfaces",
        ],
        learned_capabilities=[
            "durable run and artifact records",
            "configuration, metric, and lineage comparison",
        ],
        research_forge_evidence=[
            "research_forge/workflow_domain.py",
            "docs/stage-3-execution-kernel.md",
        ],
        missing_or_bounded=[
            "MLflow is not installed or used as the Research Forge state store",
            "no MLflow tracking or model-registry compatibility",
        ],
        permitted_claim="MLflow was used as an experiment-tracking comparison, not embedded.",
    ),
    UpstreamCapabilityRecord(
        record_id="upstream-nextflow",
        name="Nextflow",
        kind="repository",
        source_url="https://github.com/nextflow-io/nextflow",
        license_note="Apache-2.0",
        status=AdoptionStatus.ARCHITECTURE_ONLY,
        proof_levels=[ProofLevel.DESIGN, ProofLevel.SOURCE],
        upstream_capabilities=[
            "data-driven computational DAGs across local, HPC, and cloud executors",
            "task hashing, caching, resume, retry, resource declaration, and reports",
        ],
        learned_capabilities=[
            "persistent dependency DAG",
            "bounded retry, resume, and cache-eligibility rules",
        ],
        research_forge_evidence=[
            "research_forge/workflow_scheduler.py",
            "tests/test_workflow_scheduler.py",
        ],
        missing_or_bounded=[
            "Research Forge is not a Nextflow executor",
            "no Nextflow DSL, cache, or report compatibility",
        ],
        permitted_claim="Nextflow informed resilient DAG execution semantics.",
    ),
    UpstreamCapabilityRecord(
        record_id="upstream-snakemake",
        name="Snakemake",
        kind="repository",
        source_url="https://github.com/snakemake/snakemake",
        license_note="MIT",
        status=AdoptionStatus.ARCHITECTURE_ONLY,
        proof_levels=[ProofLevel.DESIGN, ProofLevel.SOURCE],
        upstream_capabilities=[
            "rule-based reproducible workflows from workstation to cluster or cloud",
            "DAG scheduling, provenance, caching, profiles, and self-contained reports",
        ],
        learned_capabilities=[
            "dependency-aware workflow execution",
            "portable audit and offline reporting pattern",
        ],
        research_forge_evidence=[
            "research_forge/workflow_scheduler.py",
            "research_forge/stage_three_trust.py",
            "tests/test_workflow_scheduler.py",
        ],
        missing_or_bounded=[
            "Research Forge does not parse Snakefiles or run Snakemake",
            "its HTML completion view is not a Snakemake report",
        ],
        permitted_claim="Snakemake reporting and DAG patterns informed platform design.",
    ),
    UpstreamCapabilityRecord(
        record_id="upstream-openml",
        name="OpenML",
        kind="repository-and-platform",
        source_url="https://github.com/openml/openml-python",
        license_note="BSD-3-Clause",
        status=AdoptionStatus.PARTIAL_RUNTIME,
        proof_levels=[
            ProofLevel.SOURCE,
            ProofLevel.UNIT,
            ProofLevel.INTEGRATION,
            ProofLevel.REAL_RUN,
        ],
        upstream_capabilities=[
            "versioned datasets, tasks, runs, flows, and benchmark suites",
            "instance-level predictions uploaded for independent metric evaluation",
        ],
        learned_capabilities=[
            "task and run identity separated from submitted predictions",
            "sample-level independent evaluation",
        ],
        research_forge_evidence=[
            "research_forge/retrieval/providers/openml.py",
            "research_forge/retrieval/interfaces/service.py",
            "research_forge/benchmarks/openml_tasks.py",
            "scripts/verify_openml_gateway.py",
            "tests/test_openml_retrieval.py",
            "tests/test_openml_capability_benchmark.py",
        ],
        real_run_evidence=[
            "docs/openml-capability-benchmark-2026-07-31.md",
        ],
        runtime_revalidated_at="2026-07-31",
        missing_or_bounded=[
            "exact Task/Dataset resolution and frozen acquisition are implemented; free-form OpenML search is intentionally not used for scientific selection",
            "Research Forge does not publish OpenML Runs or Flows",
            "Research Forge formal datasets are not published to OpenML",
        ],
        permitted_claim=(
            "Five official tasks passed the narrow tabular benchmark, and the "
            "Retrieval Gateway resolves and freezes exact OpenML datasets; full "
            "OpenML Run/Flow compatibility is not claimed."
        ),
    ),
    UpstreamCapabilityRecord(
        record_id="upstream-mlperf",
        name="MLPerf Inference",
        kind="repository-and-benchmark",
        source_url="https://github.com/mlcommons/inference",
        license_note="Apache-2.0; benchmark rules and datasets have separate terms",
        status=AdoptionStatus.ARCHITECTURE_ONLY,
        proof_levels=[ProofLevel.DESIGN, ProofLevel.SOURCE],
        upstream_capabilities=[
            "standardized benchmark scenarios, reference implementations, and accuracy checks",
            "submission checker and compliance tests before results are accepted",
        ],
        learned_capabilities=[
            "formal package checker independent of the web service",
            "qualification before a result can become authoritative",
        ],
        research_forge_evidence=[
            "research_forge/stage_three_trust.py",
            "tests/test_stage_three.py",
        ],
        missing_or_bounded=[
            "Research Forge is not an MLPerf implementation or submission",
            "no MLPerf compliance or leaderboard claim",
        ],
        permitted_claim="MLPerf checker semantics informed the offline completion verifier.",
    ),
    UpstreamCapabilityRecord(
        record_id="upstream-code-ocean",
        name="Code Ocean",
        kind="commercial-platform-reference",
        source_url=(
            "https://docs.codeocean.com/osl-guide/getting-started/"
            "what-is-a-compute-capsule"
        ),
        license_note="commercial service; public product and reproducibility documentation",
        status=AdoptionStatus.ARCHITECTURE_ONLY,
        proof_levels=[ProofLevel.DESIGN, ProofLevel.SOURCE],
        upstream_capabilities=[
            "computational capsules that bind code, data, environment, and results",
            "reproducible runs and independent computational reproducibility review",
        ],
        learned_capabilities=[
            "portable sealed execution package",
            "reproduction evidence distinct from internal evidence verification",
        ],
        research_forge_evidence=[
            "research_forge/reproduction_package.py",
            "research_forge/reproduction_domain.py",
            "tests/test_reproduction.py",
        ],
        missing_or_bounded=[
            "Research Forge does not create or validate Code Ocean Capsules",
            "no Code Ocean external reproduction is claimed",
        ],
        permitted_claim="Code Ocean capsule and replay concepts informed reproduction packaging.",
    ),
    UpstreamCapabilityRecord(
        record_id="upstream-acm-artifact-evaluation",
        name="ACM Artifact Review and Badging",
        kind="evaluation-standard",
        source_url="https://www.acm.org/publications/policies/artifact-review-and-badging-current",
        license_note="ACM policy reference",
        status=AdoptionStatus.ARCHITECTURE_ONLY,
        proof_levels=[ProofLevel.DESIGN, ProofLevel.SOURCE],
        upstream_capabilities=[
            "separate artifact available, artifact evaluated, results reproduced, and results replicated claims",
            "independent artifact inspection and execution evidence",
        ],
        learned_capabilities=[
            "multi-level reproduction evidence",
            "internal verification is not external replication",
        ],
        research_forge_evidence=[
            "research_forge/reproduction_domain.py",
            "research_forge/reproduction_verifier.py",
            "tests/test_reproduction.py",
        ],
        missing_or_bounded=[
            "Research Forge does not award ACM badges",
            "L3 and L4 require genuinely independent or external work",
        ],
        permitted_claim="ACM badge distinctions informed Research Forge evidence levels.",
    ),
    UpstreamCapabilityRecord(
        record_id="upstream-w3c-prov",
        name="W3C PROV",
        kind="standard-and-repository",
        source_url="https://github.com/w3c/prov",
        license_note="W3C document and software notices apply",
        status=AdoptionStatus.VERIFIED_RUNTIME,
        proof_levels=[
            ProofLevel.DESIGN,
            ProofLevel.SOURCE,
            ProofLevel.UNIT,
            ProofLevel.INTEGRATION,
            ProofLevel.REAL_RUN,
        ],
        upstream_capabilities=[
            "standard Entity, Activity, Agent, usage, generation, derivation, and attribution model",
            "interoperable provenance serializations and constraints",
        ],
        learned_capabilities=[
            "completion-package PROV JSON-LD export",
            "explicit entity, activity, and agent lineage",
        ],
        research_forge_evidence=[
            "research_forge/stage_three_trust.py",
            "research_forge/prov_validation.py",
            "scripts/validate_prov_external.py",
            "tests/test_stage_three_trust.py",
            "tests/test_prov_validation.py",
            "docs/stage-3-execution-kernel.md",
        ],
        real_run_evidence=[
            "docs/prov-external-validation-acceptance-2026-07-31.md",
        ],
        runtime_revalidated_at="2026-07-31",
        missing_or_bounded=[
            "PySHACL validates the bounded Research Forge Stage 3 projection, not every W3C PROV constraint",
            "no official W3C certification or third-party conformance badge is claimed",
            "internal schemas remain authoritative for Research Forge",
        ],
        permitted_claim=(
            "The bounded Stage 3 PROV projection passes an external PySHACL semantic "
            "engine; full W3C PROV conformance and certification are not claimed."
        ),
    ),
    UpstreamCapabilityRecord(
        record_id="upstream-ro-crate",
        name="Workflow Run RO-Crate",
        kind="standard-and-repository",
        source_url="https://github.com/ResearchObject/ro-crate",
        license_note="Apache-2.0 specification; JSON-LD contexts and examples CC0",
        status=AdoptionStatus.VERIFIED_RUNTIME,
        proof_levels=[
            ProofLevel.DESIGN,
            ProofLevel.SOURCE,
            ProofLevel.UNIT,
            ProofLevel.INTEGRATION,
            ProofLevel.REAL_RUN,
        ],
        upstream_capabilities=[
            "JSON-LD research object packaging with contextual metadata",
            "workflow-run prospective and retrospective provenance profiles",
        ],
        learned_capabilities=[
            "completion-package RO-Crate metadata export",
            "portable research object description",
            "external required-check validation with a frozen machine report",
        ],
        research_forge_evidence=[
            "research_forge/stage_three_trust.py",
            "research_forge/rocrate_validation.py",
            "tests/test_stage_three_trust.py",
            "tests/test_rocrate_validation.py",
            "docs/stage-3-execution-kernel.md",
            "docs/workflow-run-rocrate-external-validation-2026-08-01.md",
        ],
        real_run_evidence=[
            "docs/workflow-run-rocrate-external-validation-2026-08-01.md"
        ],
        runtime_revalidated_at="2026-08-01",
        missing_or_bounded=[
            "Workflow Run Crate 0.5 and all inherited required checks are accepted",
            "Provenance Run profiles are not yet claimed",
            "no third-party certification is claimed",
        ],
        permitted_claim=(
            "A Stage 3 minimal crate passes the maintained CRS4 external "
            "validator's Workflow Run Crate 0.5 required checks, including "
            "its inherited profiles; certification is not claimed."
        ),
    ),
    UpstreamCapabilityRecord(
        record_id="upstream-slsa",
        name="SLSA",
        kind="supply-chain-standard",
        source_url="https://github.com/slsa-framework/slsa",
        license_note="Community Specification License 1.0 and legacy Apache-2.0 portions",
        status=AdoptionStatus.ARCHITECTURE_ONLY,
        proof_levels=[ProofLevel.DESIGN, ProofLevel.SOURCE],
        upstream_capabilities=[
            "graduated software supply-chain integrity requirements",
            "trusted builder provenance and verifiable artifact attestations",
        ],
        learned_capabilities=[
            "control-plane-signed completion attestations",
            "signing authority isolated from candidate workers",
        ],
        research_forge_evidence=[
            "research_forge/stage_three_trust.py",
            "research_forge/reproduction_attestation.py",
            "tests/test_stage_three_trust.py",
        ],
        missing_or_bounded=[
            "Research Forge attestations are not SLSA provenance statements",
            "no SLSA build-level certification is claimed",
        ],
        permitted_claim="SLSA trust-boundary concepts informed custom Research Forge attestations.",
    ),
    UpstreamCapabilityRecord(
        record_id="upstream-sigstore",
        name="Sigstore",
        kind="repository-and-ecosystem",
        source_url="https://github.com/sigstore/sigstore",
        license_note="Apache-2.0",
        status=AdoptionStatus.ARCHITECTURE_ONLY,
        proof_levels=[ProofLevel.DESIGN, ProofLevel.SOURCE],
        upstream_capabilities=[
            "keyless or key-based artifact signing and verification ecosystem",
            "identity-bound certificates and transparency-log-backed verification",
        ],
        learned_capabilities=[
            "signed immutable package and receipt verification",
            "public verifier registry separated from private signing custody",
        ],
        research_forge_evidence=[
            "research_forge/reproduction_attestation.py",
            "research_forge/reproduction_checker.py",
            "tests/test_reproduction.py",
        ],
        missing_or_bounded=[
            "Research Forge uses custom Ed25519 records, not Fulcio, Rekor, or Cosign",
            "no Sigstore transparency or identity claim",
        ],
        permitted_claim="Sigstore informed signing architecture; Sigstore itself is not integrated.",
    ),
    UpstreamCapabilityRecord(
        record_id="upstream-ccf-deadlines",
        name="ccf-deadlines",
        kind="repository",
        source_url="https://github.com/ccfddl/ccf-deadlines",
        license_note="MIT",
        status=AdoptionStatus.ADAPTER_ONLY,
        proof_levels=[ProofLevel.SOURCE, ProofLevel.UNIT, ProofLevel.INTEGRATION],
        upstream_capabilities=[
            "community-maintained conference ranks, cycles, deadlines, website, CLI, and WeChat applet",
        ],
        learned_capabilities=[
            "optional conference rank and cycle metadata overlay",
            "official CFP remains final submission authority",
        ],
        research_forge_evidence=[
            "research_forge/journal_recommendation.py",
            "tests/test_journal_recommendation.py",
            "README.md",
        ],
        missing_or_bounded=[
            "only an owner-supplied local checkout is read",
            "community deadlines never authorize submission or replace official CFP confirmation",
        ],
        permitted_claim="A tested local metadata overlay exists; deadline authority remains external.",
    ),
    UpstreamCapabilityRecord(
        record_id="upstream-deepchecks",
        name="Deepchecks",
        kind="repository",
        source_url="https://github.com/deepchecks/deepchecks",
        license_note="AGPL boundary requires separate adoption review",
        status=AdoptionStatus.NOT_ADOPTED,
        proof_levels=[ProofLevel.DESIGN, ProofLevel.SOURCE],
        upstream_capabilities=[
            "tabular, NLP, and vision validation suites for data and models",
            "custom checks, CI conditions, monitoring, and JSON or HTML reports",
        ],
        learned_capabilities=["ML validation-suite comparison"],
        research_forge_evidence=[
            "docs/open-source-scientific-validity-integrations.md"
        ],
        missing_or_bounded=[
            "not imported or executed",
            "license boundary intentionally preserved",
        ],
        permitted_claim="Deepchecks was reviewed but not adopted.",
    ),
)


def upstream_capability_map() -> dict[str, UpstreamCapabilityRecord]:
    return {item.record_id: item for item in UPSTREAM_CAPABILITIES}


__all__ = [
    "AdoptionStatus",
    "ProofLevel",
    "UPSTREAM_CAPABILITIES",
    "UpstreamCapabilityAuditRecord",
    "UpstreamCapabilityAuditReport",
    "UpstreamCapabilityRecord",
    "audit_upstream_capabilities",
    "upstream_capability_map",
]
