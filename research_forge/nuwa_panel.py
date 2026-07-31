from __future__ import annotations

"""Exact, auditable routing and aggregation for the Nuwa claim panel.

The model supplies persona votes.  Packet blinding, routing, schema checks,
hashes, and final aggregation are deterministic and cannot be overridden by a
meta-agent.
"""

import asyncio
import hashlib
import json
from pathlib import Path
from typing import Any, Awaitable, Callable, Literal

from pydantic import Field, model_validator

from .models import StrictModel, utc_now
from .paper_authoring import (
    EvidenceClaimMap,
    EvidencePointer,
    academicize_publication_text,
)
from .storage import sha256_file

NuwaClaimType = Literal["experiment", "literature", "novelty"]
NuwaPersona = Literal["feynman", "tukey", "shannon", "popper"]
NuwaVerdict = Literal["supported", "unsupported", "abstain"]
NuwaFailureMode = Literal[
    "missing_evidence",
    "source_misattribution",
    "experiment_artifact_mismatch",
    "exact_metric_mismatch",
    "statistical_invalidity",
    "internal_contradiction",
    "overgeneralization",
    "unsupported_novelty",
    "non_falsifiable_claim",
    "ambiguous_or_insufficient_packet",
]

NUWA_ROUTES: dict[str, tuple[NuwaPersona, NuwaPersona, NuwaPersona]] = {
    "experiment": ("feynman", "tukey", "shannon"),
    "literature": ("feynman", "shannon", "popper"),
    "novelty": ("popper", "shannon", "feynman"),
}
NUWA_HARD_VETOES = {
    "missing_evidence",
    "source_misattribution",
    "experiment_artifact_mismatch",
    "exact_metric_mismatch",
    "statistical_invalidity",
    "internal_contradiction",
}
_BLINDED_KEYS = {
    "arm",
    "arm_id",
    "author",
    "author_identity",
    "baseline",
    "effect_label",
    "prior_verdict",
    "seed",
    "task",
    "task_id",
    "treatment",
    "verdict",
}


def _sha(value: Any) -> str:
    return hashlib.sha256(
        json.dumps(
            value,
            ensure_ascii=False,
            sort_keys=True,
            separators=(",", ":"),
        ).encode("utf-8")
    ).hexdigest()


class NuwaEvidenceExcerpt(StrictModel):
    evidence_id: str
    content: str = Field(min_length=1, max_length=6_000)
    content_sha256: str = Field(pattern=r"^[a-f0-9]{64}$")
    source_hash_verified: bool | None = None


class NuwaBlindedClaim(StrictModel):
    audit_id: str = Field(pattern=r"^audit-[a-f0-9]{16}$")
    claim_type: NuwaClaimType
    claim_text: str = Field(min_length=3, max_length=8_000)
    linked_evidence: list[NuwaEvidenceExcerpt]


class NuwaBlindedPacket(StrictModel):
    schema_version: int = 1
    panel_id: str
    ensemble_label: Literal["same-model persona ensemble"] = (
        "same-model persona ensemble"
    )
    blinded: Literal[True] = True
    source_sample_sha256: str = Field(pattern=r"^[a-f0-9]{64}$")
    items: list[NuwaBlindedClaim] = Field(min_length=1)
    audit_to_claim_id: dict[str, str] = Field(exclude=True)


class NuwaVote(StrictModel):
    audit_id: str
    verdict: NuwaVerdict
    rationale: str = Field(min_length=5, max_length=3_000)
    failure_modes: list[NuwaFailureMode]
    decisive_evidence: list[str] = Field(max_length=20)

    @model_validator(mode="after")
    def verdict_matches_contract(self) -> "NuwaVote":
        if self.verdict == "supported":
            if self.failure_modes or not self.decisive_evidence:
                raise ValueError(
                    "supported requires no failure modes and decisive evidence"
                )
        elif self.verdict == "unsupported":
            if not self.failure_modes or self.failure_modes == [
                "ambiguous_or_insufficient_packet"
            ]:
                raise ValueError(
                    "unsupported requires a non-ambiguity failure mode"
                )
            if not self.decisive_evidence:
                raise ValueError(
                    "unsupported requires decisive packet evidence"
                )
        else:
            if self.failure_modes != ["ambiguous_or_insufficient_packet"]:
                raise ValueError(
                    "abstain requires exactly ambiguous_or_insufficient_packet"
                )
        return self


class NuwaPersonaReview(StrictModel):
    schema_version: int = 1
    panel_id: str
    persona_id: NuwaPersona
    source_sample_sha256: str = Field(pattern=r"^[a-f0-9]{64}$")
    blinded: Literal[True] = True
    items: list[NuwaVote]


class NuwaAggregatedClaim(StrictModel):
    audit_id: str
    claim_id: str
    final_verdict: NuwaVerdict
    votes: list[NuwaVote]
    routed_personas: list[NuwaPersona] = Field(min_length=3, max_length=3)
    hard_veto_failure_modes: list[NuwaFailureMode]
    disagreement: bool
    requires_human_adjudication: bool


class NuwaPanelReport(StrictModel):
    schema_version: int = 1
    panel_id: str
    panel_version: Literal["nuwa-scientist-panel-v1"] = (
        "nuwa-scientist-panel-v1"
    )
    ensemble_label: Literal["same-model persona ensemble"] = (
        "same-model persona ensemble"
    )
    status: Literal["completed", "degraded"]
    created_at: str = Field(default_factory=utc_now)
    input_sha256: str = Field(pattern=r"^[a-f0-9]{64}$")
    blinded_packet_sha256: str = Field(pattern=r"^[a-f0-9]{64}$")
    persona_job_sha256: dict[str, str]
    persona_reviews: list[NuwaPersonaReview]
    claims: list[NuwaAggregatedClaim]
    human_adjudication_queue: list[str]
    limitations: list[str]


def _claim_type(kind: str, pointers: list[EvidencePointer]) -> NuwaClaimType:
    folded = kind.casefold()
    if "novel" in folded:
        return "novelty"
    if "literature" in folded or any(
        item.evidence_type == "verified_literature" for item in pointers
    ):
        return "literature"
    return "experiment"


def _blind_json(value: Any) -> Any:
    if isinstance(value, dict):
        return {
            str(key): _blind_json(item)
            for key, item in value.items()
            if str(key).casefold() not in _BLINDED_KEYS
        }
    if isinstance(value, list):
        return [_blind_json(item) for item in value]
    return value


def _json_pointer(value: Any, pointer: str | None) -> Any:
    if not pointer:
        return value
    current = value
    for token in pointer.lstrip("$.").replace("[", ".").replace("]", "").split(
        "."
    ):
        if not token:
            continue
        if isinstance(current, list) and token.isdigit():
            current = current[int(token)]
        elif isinstance(current, dict) and token in current:
            current = current[token]
        else:
            return value
    return current


def _pointer_excerpt(
    root: Path,
    pointer: EvidencePointer,
    *,
    evidence_index: int,
    literature_by_id: dict[str, Any],
) -> NuwaEvidenceExcerpt:
    content: str
    verified: bool | None = None
    candidate = (root / pointer.path).resolve()
    try:
        candidate.relative_to(root.resolve())
        inside_root = True
    except ValueError:
        inside_root = False
    if inside_root and candidate.is_file():
        raw = candidate.read_bytes()
        verified = (
            hashlib.sha256(raw).hexdigest() == pointer.sha256
            if pointer.sha256
            else None
        )
        try:
            parsed = json.loads(raw.decode("utf-8"))
            selected = _json_pointer(parsed, pointer.json_path)
            content = json.dumps(
                _blind_json(selected),
                ensure_ascii=False,
                sort_keys=True,
                separators=(",", ":"),
            )
        except (UnicodeDecodeError, json.JSONDecodeError):
            content = raw.decode("utf-8", errors="replace")
    elif pointer.source_id and pointer.source_id in literature_by_id:
        content = json.dumps(
            _blind_json(literature_by_id[pointer.source_id]),
            ensure_ascii=False,
            sort_keys=True,
            separators=(",", ":"),
        )
    else:
        content = json.dumps(
            {
                "evidence_type": pointer.evidence_type,
                "content_available": False,
                "registered_hash_present": bool(pointer.sha256),
            },
            separators=(",", ":"),
        )
    content = content[:6_000] or '{"content_available":false}'
    return NuwaEvidenceExcerpt(
        evidence_id=f"evidence-{evidence_index:02d}",
        content=content,
        content_sha256=hashlib.sha256(content.encode("utf-8")).hexdigest(),
        source_hash_verified=verified,
    )


def build_nuwa_packet(
    evidence_map: EvidenceClaimMap,
    *,
    root: Path,
    panel_id: str,
    publication_aliases: dict[str, str] | None = None,
    literature_by_id: dict[str, Any] | None = None,
) -> NuwaBlindedPacket:
    """Freeze a publication-safe packet with no arm, task, seed, or prior verdict."""

    original = evidence_map.model_dump(mode="json")
    input_sha = _sha(original)
    items: list[NuwaBlindedClaim] = []
    audit_to_claim: dict[str, str] = {}
    literature = literature_by_id or {}
    for binding in evidence_map.bindings:
        audit_id = "audit-" + hashlib.sha256(
            f"{binding.claim_id}\n{binding.statement}".encode("utf-8")
        ).hexdigest()[:16]
        audit_to_claim[audit_id] = binding.claim_id
        items.append(
            NuwaBlindedClaim(
                audit_id=audit_id,
                claim_type=_claim_type(binding.kind, binding.evidence),
                claim_text=academicize_publication_text(
                    binding.statement,
                    aliases=publication_aliases,
                ),
                linked_evidence=[
                    _pointer_excerpt(
                        root,
                        pointer,
                        evidence_index=index,
                        literature_by_id=literature,
                    )
                    for index, pointer in enumerate(binding.evidence, start=1)
                ],
            )
        )
    packet_payload = {
        "panel_id": panel_id,
        "blinded": True,
        "items": [item.model_dump(mode="json") for item in items],
    }
    return NuwaBlindedPacket(
        panel_id=panel_id,
        source_sample_sha256=input_sha,
        items=items,
        audit_to_claim_id=audit_to_claim,
    )


def build_nuwa_jobs(
    packet: NuwaBlindedPacket,
) -> dict[NuwaPersona, dict[str, Any]]:
    jobs: dict[NuwaPersona, dict[str, Any]] = {}
    for item in packet.items:
        for persona in NUWA_ROUTES[item.claim_type]:
            job = jobs.setdefault(
                persona,
                {
                    "schema_version": 1,
                    "panel_id": packet.panel_id,
                    "persona_id": persona,
                    "source_sample_sha256": packet.source_sample_sha256,
                    "blinded": True,
                    "items": [],
                },
            )
            job["items"].append(item.model_dump(mode="json"))
    return jobs


def validate_nuwa_review(
    review: NuwaPersonaReview,
    *,
    job: dict[str, Any],
) -> None:
    if review.panel_id != job["panel_id"]:
        raise ValueError("Nuwa review panel_id mismatch")
    if review.persona_id != job["persona_id"]:
        raise ValueError("Nuwa review persona mismatch")
    if review.source_sample_sha256 != job["source_sample_sha256"]:
        raise ValueError("Nuwa review source hash mismatch")
    expected = [item["audit_id"] for item in job["items"]]
    observed = [item.audit_id for item in review.items]
    if len(observed) != len(set(observed)) or set(observed) != set(expected):
        raise ValueError("Nuwa review audit IDs do not exactly match its route")


def aggregate_nuwa_panel(
    packet: NuwaBlindedPacket,
    reviews: list[NuwaPersonaReview],
    *,
    job_hashes: dict[str, str],
    status: Literal["completed", "degraded"] = "completed",
    extra_limitations: list[str] | None = None,
) -> NuwaPanelReport:
    by_persona = {review.persona_id: review for review in reviews}
    claims: list[NuwaAggregatedClaim] = []
    queue: list[str] = []
    for item in packet.items:
        route = list(NUWA_ROUTES[item.claim_type])
        votes = [
            vote
            for persona in route
            for vote in by_persona.get(
                persona,
                NuwaPersonaReview(
                    panel_id=packet.panel_id,
                    persona_id=persona,
                    source_sample_sha256=packet.source_sample_sha256,
                    blinded=True,
                    items=[],
                ),
            ).items
            if vote.audit_id == item.audit_id
        ]
        hard_vetoes = sorted(
            {
                mode
                for vote in votes
                for mode in vote.failure_modes
                if mode in NUWA_HARD_VETOES
            }
        )
        verdicts = [vote.verdict for vote in votes]
        if len(votes) != 3:
            final: NuwaVerdict = "abstain"
        elif hard_vetoes:
            final = "unsupported"
        elif verdicts.count("unsupported") >= 2:
            final = "unsupported"
        elif verdicts.count("unsupported") == 1:
            final = "abstain"
        elif verdicts.count("supported") >= 2:
            final = "supported"
        else:
            final = "abstain"
        disagreement = len(set(verdicts)) > 1 or len(votes) != 3
        requires_human = disagreement or final == "abstain"
        if requires_human:
            queue.append(item.audit_id)
        claims.append(
            NuwaAggregatedClaim(
                audit_id=item.audit_id,
                claim_id=packet.audit_to_claim_id[item.audit_id],
                final_verdict=final,
                votes=votes,
                routed_personas=route,
                hard_veto_failure_modes=hard_vetoes,
                disagreement=disagreement,
                requires_human_adjudication=requires_human,
            )
        )
    public_packet = {
        "panel_id": packet.panel_id,
        "blinded": True,
        "items": [item.model_dump(mode="json") for item in packet.items],
    }
    return NuwaPanelReport(
        panel_id=packet.panel_id,
        status=status,
        input_sha256=packet.source_sample_sha256,
        blinded_packet_sha256=_sha(public_packet),
        persona_job_sha256=job_hashes,
        persona_reviews=reviews,
        claims=claims,
        human_adjudication_queue=queue,
        limitations=[
            (
                "Operationally isolated persona contexts share one underlying "
                "model and can have correlated blind spots."
            ),
            (
                "Model-proposed hard vetoes remain provisional unless a "
                "separate deterministic checker verifies them."
            ),
            "This is not human review, cross-model validation, or replication.",
            *(extra_limitations or []),
        ],
    )


_PERSONA_CARDS: dict[str, dict[str, Any]] = {
    "feynman": {
        "role": "Detect gaps between what was measured and what is claimed.",
        "questions": [
            "What direct observation supports each material part?",
            "Could the evidence arise if the explanation were false?",
            "Are settings, failures, or counterexamples omitted?",
        ],
    },
    "tukey": {
        "role": "Audit summaries, uncertainty, and study-design validity.",
        "questions": [
            "Does the statistic match the unit of analysis?",
            "Are uncertainty and variability represented?",
            "Could missingness, selection, or aggregation reverse the result?",
        ],
    },
    "shannon": {
        "role": "Check definitions, information flow, and exact correspondence.",
        "questions": [
            "Are quantities and terms stable?",
            "Does each number map to packet evidence?",
            "Does the conclusion add information absent from evidence?",
        ],
    },
    "popper": {
        "role": "Challenge falsifiability, scope, and novelty boundaries.",
        "questions": [
            "What result would count against the claim?",
            "Is novelty tested against an explicit comparison set?",
            "Does the claim retreat when challenged?",
        ],
    },
}


async def run_nuwa_panel(
    packet: NuwaBlindedPacket,
    *,
    cwd: Path,
) -> NuwaPanelReport:
    from .agent_runtime import review_nuwa_claim_packet

    jobs = build_nuwa_jobs(packet)
    job_hashes = {persona: _sha(job) for persona, job in jobs.items()}

    async def invoke(persona: NuwaPersona, job: dict[str, Any]):
        review = await review_nuwa_claim_packet(
            json.dumps(
                {
                    "persona_card": _PERSONA_CARDS[persona],
                    "review_contract": {
                        "judge_only_supplied_packet": True,
                        "verdicts": [
                            "supported",
                            "unsupported",
                            "abstain",
                        ],
                        "hard_veto_failure_modes": sorted(NUWA_HARD_VETOES),
                        "abstain_failure_mode": (
                            "ambiguous_or_insufficient_packet"
                        ),
                        "no_other_reviewer_outputs": True,
                    },
                    "job": job,
                },
                ensure_ascii=False,
                separators=(",", ":"),
            ),
            cwd=cwd,
        )
        validate_nuwa_review(review, job=job)
        return review

    results = await asyncio.gather(
        *(invoke(persona, job) for persona, job in jobs.items()),
        return_exceptions=True,
    )
    reviews = [item for item in results if isinstance(item, NuwaPersonaReview)]
    errors = [
        f"{type(item).__name__}: {item}"
        for item in results
        if isinstance(item, BaseException)
    ]
    return aggregate_nuwa_panel(
        packet,
        reviews,
        job_hashes=job_hashes,
        status="degraded" if errors else "completed",
        extra_limitations=(
            ["One or more persona jobs failed schema or runtime validation: " + "; ".join(errors)]
            if errors
            else []
        ),
    )


__all__ = [
    "NUWA_HARD_VETOES",
    "NUWA_ROUTES",
    "NuwaBlindedPacket",
    "NuwaPanelReport",
    "NuwaPersonaReview",
    "NuwaVote",
    "aggregate_nuwa_panel",
    "build_nuwa_jobs",
    "build_nuwa_packet",
    "run_nuwa_panel",
    "validate_nuwa_review",
]
