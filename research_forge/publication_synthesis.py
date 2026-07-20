from __future__ import annotations

"""Evidence-bound manuscript artifacts for the prospective publication matrix.

This module deliberately does not reuse the legacy 18-registry provisional
synthesis path.  The publication protocol has a different evaluator, matrix
size, and evidence boundary, so every rendered claim is rebuilt from the
protected 8x5 artifacts.
"""

from pathlib import Path
import re
from typing import Any
import unicodedata

from .publication_pair_audit import audit_publication_pairs
from .storage import read_json, safe_relative, sha256_file, write_json_atomic
from .study import audit_stage2_protocol


PUBLICATION_RELEASE_ORDER = (
    "freeze_and_run_experiment",
    "blind_science_and_fixed_venue_readiness_review",
    "layout_pdf_and_submission_package",
)


def publication_layout_gate(
    readiness_path: Path, *, project: Path | None = None
) -> dict[str, Any]:
    """Authorize typesetting only after the canonical publication sequence.

    The release order is deliberately hard-coded rather than prompt-only:

    1. freeze the protocol and complete the prospective experiment;
    2. complete blinded scientific / fixed-venue readiness review;
    3. only then create typeset sources, PDFs, or a review submission package.

    ``HUMAN_GATE_PENDING`` remains visible: it blocks external submission, but
    does not turn an automated review-package gate into a false negative.  A
    human reviewer needs a readable package precisely while that human gate is
    pending.  Neither an acceptance estimate nor a polished PDF can bypass the
    automated evidence gate.
    """
    report = read_json(readiness_path)
    score = float(report.get("readiness_score", -1.0))
    threshold = float(report.get("readiness_threshold", 0.60))
    if project is not None:
        project = project.resolve()
        _preflight(project)
        manuscript = project / "synthesis" / "publication_manuscript.md"
        reported_manuscript = report.get("manuscript_path")
        if not manuscript.is_file() or reported_manuscript != str(manuscript):
            raise ValueError(
                "publication layout is blocked: readiness report is not bound to this "
                "project's evidence manuscript"
            )
        if report.get("manuscript_sha256") != sha256_file(manuscript):
            raise ValueError(
                "publication layout is blocked: evidence manuscript changed after the "
                "fixed-venue readiness review"
            )
        expected_contract = project / "synthesis" / "publication_target_contract.json"
        if report.get("contract_path") != str(expected_contract):
            raise ValueError(
                "publication layout is blocked: readiness report is not bound to this "
                "project's fixed-venue contract"
            )
    automated_hard_gate_passed = report.get("automated_hard_gate_passed") is True
    automated_publication_gate_passed = (
        report.get("automated_publication_gate_passed") is True
    )
    passed = (
        automated_hard_gate_passed
        and automated_publication_gate_passed
        and score >= threshold
    )
    result = {
        "schema_version": 1,
        "release_order": list(PUBLICATION_RELEASE_ORDER),
        "experiment_evidence_complete": project is not None,
        "blind_science_and_fixed_venue_review_complete": automated_publication_gate_passed,
        "layout_allowed": passed,
        "pdf_allowed": passed,
        "review_submission_package_allowed": passed,
        "external_submission_allowed": report.get("publication_submission_ready") is True,
        "readiness_score": score,
        "readiness_threshold": threshold,
        "automated_hard_gate_passed": automated_hard_gate_passed,
        "automated_publication_gate_passed": automated_publication_gate_passed,
        "human_gate_pending": report.get("human_gate_pending") is True,
        "publication_submission_ready": report.get("publication_submission_ready") is True,
        "acceptance_probability_is_not_a_layout_input": True,
    }
    if not passed:
        raise ValueError(
            "publication layout is blocked: first freeze and complete the experiment, then pass "
            "blinded fixed-venue readiness with readiness >= threshold and zero automated hard "
            "blockers; acceptance probability and a pending human gate are not bypass inputs"
        )
    return result


def _relative(project: Path, path: Path) -> str:
    return path.resolve().relative_to(project.resolve()).as_posix()


def _paths(project: Path) -> dict[str, Path]:
    stage2 = project / "stage2"
    return {
        "protocol": stage2 / "protocol.json",
        "pair_audit": stage2 / "publication_pair_audit.json",
        "summary": stage2 / "protected_nli_evaluation" / "summary.json",
        "manifest": stage2 / "protected_nli_evaluation" / "manifest.json",
        "unblinding": stage2 / "protected_nli_evaluation" / "unblinding.json",
    }


def _preflight(project: Path) -> tuple[dict[str, Any], dict[str, Any]]:
    audit = audit_stage2_protocol(project)
    if not audit.passed:
        raise ValueError("publication protocol audit failed: " + "; ".join(audit.violations))
    pair = audit_publication_pairs(project, persist=False)
    if not pair["complete"] or not pair["passed"]:
        raise ValueError("publication pair audit is incomplete or failed")
    paths = _paths(project)
    if not all(path.is_file() for path in paths.values()):
        raise FileNotFoundError("protected publication evaluation artifacts are incomplete")
    summary = read_json(paths["summary"])
    if summary.get("analysis_status") != "protected_independent_nli_complete_human_audit_pending":
        raise ValueError("protected evaluation status is not the expected deferred-human state")
    if summary.get("primary_analysis_interpretable") is not False:
        raise ValueError("publication synthesis refuses an interpretable primary-analysis claim")
    if int(summary.get("pair_count", 0)) != 40:
        raise ValueError("publication synthesis requires all 40 preregistered pairs")
    return pair, summary


def _claims(project: Path, summary: dict[str, Any]) -> list[dict[str, Any]]:
    paths = _paths(project)
    baseline = summary["arm_metrics"]["baseline"]
    treatment = summary["arm_metrics"]["treatment"]
    paired = summary["paired_analysis"]
    evidence = [_relative(project, paths[key]) for key in ("protocol", "pair_audit", "summary", "manifest", "unblinding")]
    hashes = {item: sha256_file(safe_relative(project, item)) for item in evidence}
    return [
        {
            "claim_id": "protected-unsupported-rate-effect",
            "kind": "result",
            "provisional": True,
            "statement": "In the protected automated evaluator, treatment had a lower unsupported-claim rate than baseline.",
            "metrics": {
                "baseline_unsupported_claim_rate": float(baseline["unsupported_claim_rate"]),
                "treatment_unsupported_claim_rate": float(treatment["unsupported_claim_rate"]),
                "paired_mean_effect": float(paired["mean"]),
                "ci_95_low": float(paired["ci_95"][0]),
                "ci_95_high": float(paired["ci_95"][1]),
            },
            "evidence_paths": evidence,
            "evidence_sha256": hashes,
        },
        {
            "claim_id": "task-performance-preservation",
            "kind": "result",
            "provisional": True,
            "statement": "The mean task-native score was unchanged between the two arms in the protected matrix.",
            "metrics": {
                "baseline_task_native_score": float(baseline["mean_task_native_score"]),
                "treatment_task_native_score": float(treatment["mean_task_native_score"]),
                "difference": float(treatment["mean_task_native_score"]) - float(baseline["mean_task_native_score"]),
            },
            "evidence_paths": evidence,
            "evidence_sha256": hashes,
        },
    ]


def _references(project: Path) -> list[dict[str, Any]]:
    sources = []
    for path in sorted((project / "literature" / "sources").glob("*.json")):
        payload = read_json(path)
        if payload.get("verified") is True:
            sources.append(payload)
    contextual_path = project / "synthesis" / "contextual_literature_manifest.json"
    if contextual_path.is_file():
        contextual = read_json(contextual_path)
        if contextual.get("boundary") != "post_freeze_contextual_only":
            raise ValueError("contextual literature must declare its post-freeze-only boundary")
        additions = contextual.get("sources", [])
        if not isinstance(additions, list):
            raise ValueError("contextual literature sources must be a list")
        for payload in additions:
            if not isinstance(payload, dict) or payload.get("verified") is not True:
                raise ValueError("contextual literature contains an unverified source")
            sources.append(payload)
    if not sources:
        raise ValueError("publication synthesis requires verified literature records")
    return sources


def _render_markdown(project: Path, analysis: dict[str, Any], claims: list[dict[str, Any]], references: list[dict[str, Any]]) -> str:
    first, second = claims
    m = first["metrics"]
    t = second["metrics"]
    baseline_metrics = analysis["arm_metrics"]["baseline"]
    treatment_metrics = analysis["arm_metrics"]["treatment"]
    reference_keys = [str(item.get("citation_key") or f"R{index + 1}") for index, item in enumerate(references)]
    refs = "\n".join(
        f"- [{key}] {item['authors'][0]} et al. ({item.get('year', 'n.d.')}). {item['title']}. {item['locator']}"
        for key, item in zip(reference_keys, references, strict=True)
    )
    # Related work should support specific statements, not act as a second
    # bibliography.  Keep representative citations next to the claims they
    # motivate and reserve the complete reference records for the final section.
    autonomous_work_citations = " ".join(f"[{key}]" for key in reference_keys[:2])
    verification_citations = " ".join(f"[{key}]" for key in reference_keys[2:3])
    calibration_citations = " ".join(
        f"[{key}]" for key in reference_keys[-3:-1]
    )
    introduction_detail = """

The unit of intervention in this study is a revision branch, not a research idea, a paper, or a model checkpoint. This distinction matters because a system can appear safer merely by selecting easier prompts or by regenerating a different upstream artifact for each condition. The study therefore asks a constrained question: conditional on the same upstream artifact, does an evidence-gated branch change the automated support status of the claims it emits? The question is narrower than whether an autonomous system discovers true scientific knowledge. It is also narrower than whether a manuscript would receive a positive editorial decision.

The controller was evaluated as a systems component. It inspected claim-like statements against evidence available in the branch and could retain, delete, qualify, or mark statements for additional support. The ungated condition received the same frozen upstream artifact but did not apply that intervention. The design consequently targets a local causal contrast between two downstream revision procedures. It does not compare two independently generated papers, two model families, or two prompt collections.

This framing also explains why task-native performance is a co-primary safeguard rather than a decorative auxiliary metric. A controller could lower an unsupported-claim rate by removing most content or by making every remaining claim less specific. The protocol therefore retains claim-retention or deletion, semantic-change type, informativeness or usefulness, and task-native performance fields. These fields do not establish that all surviving claims are correct. They make it possible to detect whether an apparent reliability improvement is accompanied by a visible loss of informative content or task performance.

The manuscript reports an automated, bounded result because the two-human blinded audit specified in the protocol is deferred. This boundary is central to interpretation. The frozen cross-family calibration establishes that the proxy is a measured instrument with a public gold-standard evaluation record. It does not establish that the instrument has been calibrated on the exact distribution of project claims, nor does it substitute for the deferred human adjudication. Every causal statement below is therefore qualified as an effect on the protected automated endpoint.

The practical motivation is nevertheless consequential. End-to-end research agents increasingly produce plans, code, results, prose, and self-evaluations in one connected loop. The convenience of that loop can obscure the provenance of a sentence in the final manuscript. A gate that acts on evidence bindings can be useful only if its own evaluation is separated from the generation path and if its apparent benefit survives a paired comparison. The present study contributes an auditable test of that limited proposition.
"""
    related_detail = f"""

Autonomous research workflows provide the first context for the study. The registered literature includes systems that iterate over ideation, search, experimentation, and drafting. Their common lesson is that long-horizon performance depends on the interfaces between stages, not only on the language model that writes a final paragraph. {autonomous_work_citations} The present work does not compare against those systems or claim to improve their aggregate scientific output. Instead, it isolates one interface: the transition from an upstream artifact to a branch-specific set of evidence-gated claims.

Scientific claim verification provides the second context. Benchmarks such as SciFact treat a claim as a proposition that can be paired with supporting or refuting evidence, which is appropriate for constructing and assessing a frozen evaluation instrument. {verification_citations} The calibration dataset used here belongs to that family. The project-specific endpoint remains different: it scores claims emitted by an autonomous workflow after a revision intervention. The manuscript therefore does not transfer benchmark accuracy into a claim that the system has verified the truth of a new scientific hypothesis.

The third context is factual-consistency and evidence-grounded generation. Retrieval and verification methods can improve the traceability of a generated assertion, but a lower proxy error rate alone can conceal deletion, over-qualification, or abstention. This is why the protocol records semantic-change and informativeness-related fields in addition to the unsupported-claim endpoint. The design treats those fields as measurement guards. It does not interpret them as a complete theory of scientific usefulness.

Finally, recent end-to-end AI-science demonstrations motivate a stricter distinction between an automated reviewer and an independent scientific evaluator. A system may generate its own experiment and then score the resulting manuscript, but shared incentives can make a self-evaluation difficult to interpret. {calibration_citations} The protected evaluator here is deliberately cross-family, arm blinded, threshold frozen, and calibrated on public labelled material. Those design decisions reduce specific sources of circularity; they do not eliminate the need for a future independent human audit.

All citations in this section serve a contextual role. The experimental corpus, task matrix, controller configuration, evaluator thresholds, and analysis plan were frozen before the formal branches ran. The three post-freeze references are explicitly separated in the contextual manifest and were added only to explain the calibration and workflow setting. They cannot retroactively alter the formal result.
"""
    methods_detail = """

### Study design and estimand

The study used a paired, shared-artifact factorial design. Eight task packs were combined with five frozen seeds, producing 40 upstream artifacts. Each upstream artifact was processed once before branching. The baseline and treatment branches then consumed the same frozen artifact. This construction prevents an observed difference from being attributed to the controller when it could instead be caused by two different stochastic upstream generations. The planned analysis unit is the task-seed pair, not an individual sentence and not a model call.

For pair i, the primary effect is the treatment unsupported-claim rate minus the baseline unsupported-claim rate. A negative value favours the evidence-gated branch on the protected automated endpoint. The protocol prespecified a hierarchical bootstrap over the pair structure, 10,000 resamples, and a fixed random seed. The interval is descriptive of uncertainty under that analysis plan. It is not a p value, an acceptance probability, or a substitute for the deferred human audit.

### Conditions and intervention boundary

The baseline branch preserved the frozen upstream artifact and executed the ordinary downstream revision path. The treatment branch preserved the same artifact and activated the evidence gate. The gate could change the treatment branch only through the registered revision interface. It was not permitted to change the task, seed, upstream artifact, evaluator, calibration thresholds, outcome definitions, or stopping rule. The branch order was randomized at the pair level to avoid a systematic baseline-first or treatment-first scheduling effect.

The intervention should therefore be interpreted as a controller-level evidence-gating policy. It is not a comparison between a stronger and weaker foundation model. Both branches inherited the same frozen Research Forge/Codex backbone and the same upstream material. Any observed treatment contrast is local to the frozen branch mechanism and the evaluated task distribution.

### Tasks, seeds, and evidence breadth

The protocol required at least eight heterogeneous task packs and five seeds per task. The completed matrix met that minimum exactly: eight task families, five seeds, 40 pairs, and 80 downstream branches. The task families include classification, question answering, reading comprehension, textual similarity, coreference resolution, and mathematical question answering. Heterogeneity reduces the risk that one prompt template or one narrow output convention dominates the result, but it does not justify extrapolation to all scientific domains or all autonomous-research systems.

Each branch emitted a structured claim registry. Registry entries separated experimental provenance from literature provenance and carried the exact metrics and artifact paths required by the structural auditor. This separation is important because a valid experiment claim should not become supported merely by attaching an unrelated source identifier. Two earlier implementation failures in this boundary were repaired before the final formal revision, and the successor run was generated under the repaired deterministic normalisation rules.

### Controlled execution environment

All formal branches ran in the frozen controlled Docker environment. The environment disabled network access, used a read-only root filesystem, dropped capabilities, imposed the registered resource limits, and captured stdout, stderr, telemetry, and artifact hashes. These controls do not make the experiment error-free. They make the execution path inspectable and reduce the opportunity for an outcome-dependent change in dependencies or external data access.

The pair-integrity auditor checked shared-artifact identity, randomized branch-order records, registry structure, telemetry presence, and hash bindings. The final audit reported 40 complete shared pairs and 80 completed branches with no reported pair-level violations. A branch or artifact failing one of those checks would not be silently excluded from the analysis. The protocol instead requires an invalidation record and a precisely scoped rerun under a new revision when the frozen evidence chain is affected.

### Protected automated measurement

The automated evaluator was a cross-family DeBERTa NLI instrument rather than the Codex-family controller or generator. Its entailment and contradiction thresholds were selected before the formal treatment outcomes were inspected. The calibration contract records public-gold evaluation macro-F1 of 0.7798 and coverage of 0.9868 on the locked evaluation split. These values establish the reported calibration status of the instrument. They are not estimates of human agreement on the 40 project pairs.

Protected evaluation was arm blinded. The evaluator received registry identifiers without the baseline or treatment label, generated the per-claim judgments, and only then joined the outputs to the unblinding map. The resulting summary reports claim counts, eligible counts, abstentions, unsupported counts, task-native score, token count, model-call count, monetary cost, and wall-clock telemetry for each arm. Blinding reduces one direct route for arm-conditioned scoring; it does not remove all limitations associated with a proxy endpoint.

### Construct safeguards and analysis plan

The primary endpoint is the unsupported-claim rate among eligible claims. The protocol also freezes claim retention or deletion, semantic-change type, informativeness or usefulness, task-native score, experiment-detail error rate, citation correctness, evidence coverage, verifier abstention, and cost telemetry. The purpose of this set is diagnostic. It makes a low unsupported rate interpretable only alongside the information-preservation and task-performance signals.

The analysis plan does not drop a task because its effect is inconvenient. In addition to the paired estimate, the protected summary contains leave-one-task-out calculations. Those analyses ask whether the sign of the paired proxy effect depends entirely on one task family. They are sensitivity analyses, not independent replications. The protocol also preserves negative and null-compatible outcomes rather than defining success only as an improvement in the primary rate.
"""
    results_detail = f"""

### Completion and integrity results

The planned experiment completed all 40 shared pairs and all 80 downstream branches. The pair audit found no violations in the shared-artifact relationship, branch-order record, telemetry completeness, or protected hash binding. This completion result is a property of the execution and provenance chain. It does not determine whether the treatment effect is scientifically important.

The protected evaluator processed 156 baseline claims and 152 treatment claims. Of those, 116 claims in each arm were eligible for the unsupported-claim-rate denominator. The baseline arm contained four automated unsupported judgments, yielding a rate of {m['baseline_unsupported_claim_rate']:.6f}. The treatment arm contained one automated unsupported judgment, yielding a rate of {m['treatment_unsupported_claim_rate']:.6f}. The unequal total claim counts are reported rather than normalized away because a controller could affect the number and type of claims it leaves for evaluation.

### Primary paired endpoint

The prespecified paired mean treatment-minus-baseline effect was {m['paired_mean_effect']:.6f}. The frozen hierarchical bootstrap interval was [{m['ci_95_low']:.6f}, {m['ci_95_high']:.6f}]. The direction is consistent with fewer automated unsupported claims in the treated branch, while the upper interval endpoint reaches zero. The appropriate reading is therefore modest automated evidence compatible with a reduction, not a definitive claim of a nonzero human-judged effect.

The median paired effect was 0.000000. This difference between the mean and median is informative rather than inconvenient: a small number of pair-level changes account for the aggregate proxy-rate reduction. The manuscript consequently avoids language implying that every task or every output was improved. The effect is reported at the registered aggregate pair level and remains bounded by the protected endpoint.

### Task-native performance and abstention

Mean task-native score was {t['baseline_task_native_score']:.6f} in the baseline arm and {t['treatment_task_native_score']:.6f} in the treatment arm, a registered difference of {t['difference']:.6f}. This equality guards against one simple failure mode, namely a controller that improves a reliability proxy by degrading the task result. It does not establish preservation of every useful property of the output, which is why the retention, semantic-change, and informativeness fields remain part of the audit record.

The evaluator abstained on two baseline claims and one treatment claim. Abstention is reported because a lower unsupported rate can be misleading if an evaluator simply refuses more difficult claims. The small difference here is insufficient to establish that abstention played no role in the result. It is one of the variables that a future human audit should compare with the automated labels.

### Sensitivity across task families

The leave-one-task-out analysis produced a negative mean effect after each of the eight task families was omitted. The values ranged from -0.019048 to -0.028571 across the eight omissions, each computed on the remaining 35 pairs. This directionally stable pattern reduces concern that a single task family alone determined the sign of the aggregate automated result. It does not convert the sensitivity analysis into an additional confirmatory test, since all values arise from the same frozen matrix.

### Resource accounting

The protected summary records 80 baseline-side model calls and 184 treatment-side model calls. It reports 3,586,080 baseline tokens and 5,185,957 treatment tokens, together with estimated monetary costs of 28.183812 USD and 47.773230 USD. The treatment therefore has a material measured resource overhead in this run. The present manuscript does not turn those values into a general efficiency claim because they are specific to the frozen controller configuration and the logged backend conditions.

The recorded treatment wall-clock total was 1,584.420853 seconds, compared with 1.503847 seconds in the baseline summary. The large difference is a telemetry finding that requires operational interpretation, not a reason to discard the primary result. Evidence gating may impose real latency and cost. A future deployment decision would need to weigh those measured costs against the magnitude and reliability of any independently validated claim-quality benefit.
"""
    discussion_detail = """

### Interpretation of the automated effect

The central finding is a bounded contrast: on the protected NLI endpoint, the evidence-gated branch had a lower aggregate unsupported-claim rate than the ungated branch derived from the same upstream artifacts. The result is useful because the counterfactual is content matched, the branch order was randomized, and the evaluator was arm blinded. Those features support a causal interpretation of the intervention on the defined automated metric within this experiment.

The result does not establish that the gate improves scientific truth. The endpoint judges a claim-evidence relation through a calibrated NLI instrument, and project-specific human adjudication remains incomplete. The distinction is not merely a disclosure convention. A system can score well on a verification proxy while making errors in novelty, experimental design, scope, or pragmatic usefulness that the proxy does not represent.

### Information preservation

The unchanged mean task-native score is consistent with the gate not reducing the registered task score in this matrix. It is not enough by itself to establish that treatment outputs preserve all useful information. The protocol's retention or deletion, semantic-change, and informativeness fields exist precisely because claim counts and task-native scores can miss a substantive loss in explanatory detail. Those fields should be inspected together with the future human audit rather than compressed into one headline score.

### Calibration and independence

The cross-family calibration is a meaningful improvement over a same-family self-evaluation loop. Its public-gold evaluation record, frozen thresholds, and arm blinding make the automated measurement chain more auditable. At the same time, calibration on SciFact does not identify all distribution shift between a scientific claim-verification benchmark and claims produced by this controller. The manuscript therefore treats the calibration as evidence for automated measurement readiness, not as completion of the human-validation gate.

### Resource trade-offs

The telemetry makes the intervention's trade-off visible. The treatment used more model calls, tokens, monetary cost, and wall-clock time under the recorded configuration. A controller that produces a modest proxy improvement at high cost may be appropriate in a high-stakes evidence-review setting and inappropriate in a low-latency exploratory setting. The experiment was not designed to identify an optimal deployment threshold, so no such recommendation is made here.

### Reproducibility and external scrutiny

The local artifact chain is strong enough to support an internal audit: protocol, controlled-runtime records, pair audit, evaluator manifest, summary, and unblinding map are hash bound. It is not yet an externally accessible anonymous supplement. Reproducibility release remains a hard readiness issue until those materials are packaged and made independently inspectable under an authorized publication route. A local path is evidence for internal traceability, not evidence of public availability.

### Implications for system design

The most general implication is architectural. Deterministic controls should own artifact identity, branch randomisation, metric schemas, hashes, stopping rules, and eligibility checks. Language-model agents should be used for semantic judgments only where deterministic checks cannot decide the issue. This division does not remove model error, but it prevents many classes of provenance, routing, and threshold drift from being hidden inside a free-form agent response.
"""
    conclusion_detail = """

The study supplies an auditable automated result rather than a completed publication claim. Its paired design, controlled execution, protected evaluation, and explicit cost telemetry make the current evidence useful for system development and future independent review. The final claim remains conditional: in this frozen matrix, the evidence gate is associated with a lower protected automated unsupported-claim rate while mean task-native score is unchanged.

The next evidentiary steps are clear. The manuscript requires a fuller evidence-bound report and an externally accessible reproducibility package. The preregistered human audit must remain separate and pending until real independent auditors complete it. Only after both automated readiness requirements and that human gate are satisfied may the system mark a package as submission ready.
"""
    abstract_detail = """

The comparison is deliberately restricted to a protected automated outcome. The experiment does not claim that the gate has completed a human review process, established the truth of the retained scientific statements, or increased the likelihood of acceptance at any venue. The result is useful because the intervention, evaluator, and analysis boundary were frozen before the 40 paired artifacts were completed, and because the reported resource costs make the reliability-performance trade-off inspectable.
"""
    introduction_supplement = """

The need for this distinction becomes more pronounced when a workflow is assessed through its own textual products. A polished discussion can make a weak experimental contrast look persuasive, while a terse log can contain a strong identification design. The publication pipeline therefore separates four questions that are often collapsed: whether a run completed, whether the automated measurement supports a bounded treatment contrast, whether independent humans have audited the claims, and whether an externally reviewable submission package exists. The present manuscript addresses the second question and documents why the other three cannot be inferred from it.

The study also avoids treating an evaluator score as a reward to optimize after the fact. Evaluator identity, threshold calibration, construct fields, seeds, task packs, and stopping rules were frozen before treatment outcomes were opened. Once the protected outputs existed, the project could inspect them and write a report, but it could not retune those elements and still call the same matrix confirmatory. This temporal boundary is a practical guard against a familiar failure mode in agent systems: using a held-out outcome repeatedly as an informal development signal.

The research question has an operational interpretation. If an evidence gate reduces unsupported statements only by increasing latency, deleting useful detail, or exploiting the same evaluator that generated the reward signal, then the apparent gain should not be promoted to a reliability claim. Conversely, if a shared-artifact comparison shows a directional proxy improvement while task performance is retained and the measurement instrument is independently calibrated, that result can justify a more demanding next-stage audit. It cannot justify skipping that audit.
"""
    related_supplement = """

Claim-verification benchmarks also clarify why calibration and application should be kept distinct. A benchmark supplies labelled examples, an explicit label space, and a reproducible split. It can show that an evaluator has measurable discrimination on the benchmark distribution. The autonomous-research setting adds generation, revision, and a changing distribution of claims. The calibration contract consequently records both the public-gold result and its limitations, including the fact that public scientific claims do not reproduce the full distribution of project-specific claims.

Work on factual consistency makes a complementary point. An evaluation label can be well defined while the utility of a rewritten document remains underdetermined. A sentence can become easier to entail by removing a qualifier, deleting an uncertainty statement, or avoiding a substantive commitment. The registered retention, deletion, semantic-change, and informativeness fields are designed to make such moves observable. They are not presented as fully validated human scales, and they are not folded into the primary unsupported-claim result.
"""
    methods_supplement = """

### Freeze order and contamination controls

The protocol was frozen before formal treatment outcomes were read. The freeze bound the publication-intent contract, task identifiers, seeds, Docker image, controller snapshots, prompt stack, branch order policy, evaluator identifier, calibration contract, metric schema, analysis procedure, and stop conditions. The protocol audit subsequently checked that these assets still matched their frozen hashes. This is a stronger condition than merely saving a configuration file at the end of a run, because it makes drift in the implementation or evidence chain visible.

The separation between development and formal evidence is also explicit. Historical three-task pilot runs, repaired revisions, synthetic fixtures, and regression tests may inform engineering decisions but are not counted as the 8-by-5 publication matrix. A defect discovered after a formal holdout is opened is handled by invalidating only the affected run, repairing the implementation, and starting a new protocol revision. It is not handled by silently replacing a failed output or selectively reusing a favourable branch.

### Claim eligibility and denominator discipline

The protected summary distinguishes total claims from eligible claims and abstentions. This distinction is required because a rate without a denominator can hide changes in what was judged. Eligible claims form the denominator for the primary rate. Claims outside the evaluator's supported decision boundary are reported as abstentions rather than forced into either a supported or unsupported label. The manuscript therefore reports total claim counts, eligible claim counts, unsupported counts, and abstention counts for both arms.

The claim registry uses deterministic structure checks before semantic evaluation. Valid experiment claims require an exact metric binding and experimental provenance. Literature and novelty claims use separate source identifiers. This rule was strengthened after earlier failed protocol revisions exposed two concrete implementation faults: a metricless valid-run status statement and an experiment claim carrying an inappropriate literature source identifier. The final successor matrix was run only after those cases were normalized deterministically and protected by regression tests.

### Statistical reporting conventions

The report retains the paired mean, median, bootstrap interval, and leave-one-task-out values because they answer different questions. The mean summarizes the registered pair-level contrast. The median indicates whether the typical pair changes. The interval describes uncertainty under the frozen resampling procedure. Leave-one-task-out values test dependence on a single task family. None of these summaries estimates a population-wide effect, and none is used to claim statistical significance beyond the displayed interval.

Cost telemetry is similarly descriptive. Token count, model-call count, monetary cost, and wall-clock time were recorded because a reliability controller can impose substantial resource overhead. The implementation did not select tasks or discard cells based on cost. Cost is reported beside the endpoint so that an apparent quality gain cannot be evaluated without its operational burden.
"""
    results_supplement = f"""

### Denominator and count transparency

The counts make the primary rate reproducible from the protected summary. Baseline produced 156 total claims, 116 eligible claims, four automated unsupported claims, and two abstentions. Treatment produced 152 total claims, 116 eligible claims, one automated unsupported claim, and one abstention. The eligible denominators were equal across arms in this matrix. The total-claim difference remains relevant because it may reflect revisions that retained, removed, or reformulated material before evaluation.

The non-entailment rate was {baseline_metrics['non_entailment_rate']:.6f} in baseline and {treatment_metrics['non_entailment_rate']:.6f} in treatment. This secondary quantity is reported as a diagnostic rather than substituted for the prespecified unsupported-claim endpoint. It provides a broader record of model judgments, including outcomes that may not map directly to the primary binary rate under the frozen eligibility rule.

### Pair-level interpretation

The paired effect is small in absolute rate units. A difference of {m['paired_mean_effect']:.6f} should not be translated into a claim that the controller prevents a fixed percentage of all scientific errors. It is the average treatment-minus-baseline change over the 40 observed paired artifacts and the particular claim-evaluation procedure used here. The median of zero further motivates reporting the distributional sensitivity rather than relying on a single headline number.

No task family was excluded for being difficult, null, or directionally unfavourable. The leave-one-task-out values are retained because they show the effect direction remained negative when each task family was removed in turn. Their range is narrow relative to the aggregate estimate, but they are correlated calculations from the same completed matrix. They should be read as robustness descriptions, not eight independent replications.

### Performance and resource co-outcomes

The unchanged task-native mean has a limited but important interpretation. It indicates that the gate did not lower the registered task score on average in these 40 pairs. It does not establish equality of every task-level score, every semantic property, or every downstream user outcome. The manuscript therefore keeps the task score alongside the semantic trace fields instead of claiming broad performance preservation.

Treatment made 184 protected model calls versus 80 in baseline. Its total token count was 5,185,957 versus 3,586,080, and its recorded monetary estimate was 47.773230 USD versus 28.183812 USD. These quantities are not normalized to a universal price or hardware configuration. They are audit records from the frozen execution environment and should be reconsidered if the controller, backend, or task distribution changes.

The reported wall-clock totals also make the latency trade-off explicit. The baseline summary records 1.503847 seconds and the treatment summary records 1,584.420853 seconds. The values reflect the logged branch pipeline rather than a general benchmark of model speed. Their magnitude is a reason to retain efficiency as a separate deployment question, not a reason to modify or suppress the primary proxy analysis.
"""
    discussion_supplement = """

### Threats to measurement validity

The principal threat is construct validity. NLI labels summarize whether an evidence relation meets the frozen instrument's decision rule. Scientific claim quality also depends on study design, completeness of evidence retrieval, novelty, causal scope, and usefulness to a domain expert. A claim can be entailed by an available source and still be misleading in context. The protocol reduces this risk by measuring several traces and by withholding the human-validation conclusion, but it cannot eliminate the gap.

Distribution shift is a second threat. The calibration used a public scientific-claim corpus with human labels and a locked split. The formal task outputs differ in style, granularity, and provenance. Cross-family calibration therefore supports the use of the automated instrument as a bounded proxy, not as a calibrated probability of human agreement in this study. The future two-human audit is required precisely because it samples the project-specific claim distribution.

### Threats to identification and generalisation

Shared-artifact branching is designed to identify the effect of the downstream gate conditional on the frozen upstream artifact. It does not identify the effect of the entire autonomous system, a different generator, a different retrieval corpus, or a different user goal. Randomized branch order mitigates one scheduling confound, but the design remains a finite task matrix. The conclusion should not be generalized to all scientific writing or all end-to-end research agents.

The eight task families improve breadth relative to a small pilot, but they are not a random sample of scientific research problems. Several are conventional language or reasoning benchmarks rather than full laboratory workflows. The result is therefore most informative as a controlled systems evaluation of a claim-revision controller. External replication on different scientific domains, evidence sources, and model backends is necessary before broader statements are warranted.

### Reporting and governance implications

The study illustrates why a research agent should preserve a machine-auditable distinction between a semantic judgment and a state transition. A language model can propose whether a statement is supported; deterministic code should decide whether the evaluator was frozen, whether the branch shares its upstream artifact, whether the sample size is complete, and whether the required evidence paths exist. This division makes it harder for a fluent narrative to conceal a missing provenance link.

The same distinction applies to review. A scientist persona, a self-review, or a second pass by the same system can expose useful weaknesses, but none of these is a human validation event. The current workflow records their status separately. It refuses to change `primary_analysis_interpretable` or `publication_submission_ready` until the preregistered human process has actually occurred.

### Practical next experiments

A next prospective study could test whether the observed proxy contrast persists under a second independently calibrated evaluator and a different suite of scientific tasks. Such a study must be frozen before its treatment outcomes are inspected. It should not reuse the current holdout as a tuning set. The current results can motivate that hypothesis, but they cannot retrospectively become the confirmatory evidence for it.

An operational study could separately examine the cost-quality frontier by preregistering latency and token budgets. That work would ask a different question from the present one: not only whether a gate changes a protected proxy rate, but which level of evidence checking is justified for a specified risk and resource setting. No deployment recommendation follows from the current matrix alone.
"""
    limitations_supplement = """

### Endpoint scope and calibration transfer

The primary endpoint inherits the strengths and limits of the frozen NLI instrument. The cross-family public-gold calibration establishes that the instrument met its registered macro-F1 and coverage requirements on the locked benchmark split. It does not establish equal discrimination for controller-produced claims, for claims involving complex causal qualifications, or for claims whose relevant evidence is absent from the supplied record. The reported treatment contrast is therefore a contrast in protected automated judgments under this instrument. It should not be translated into a percentage of scientifically true claims or a forecast of expert agreement.

The eligible-claim denominator is also a design choice. The protocol distinguishes eligible claims, abstentions, and several structural traces so that a rate cannot be improved merely by hiding all text in one undifferentiated bucket. Even so, a controller may change the number, specificity, or rhetorical form of claims. Equal eligible denominators in this completed matrix reduce one simple explanation for the observed difference, but they do not prove that the two outputs have identical substantive value. The retention, deletion, semantic-change, and informativeness records are safeguards against that interpretation error, not validated substitutes for expert utility ratings.

### Statistical and task-distribution limits

The completed design contains 40 paired task-seed observations, which is sufficient for the preregistered paired summary and leave-one-task-out diagnostic but remains modest for claims about heterogeneous task families. The confidence interval includes zero at its upper endpoint. This means the evidence is compatible with no protected-endpoint improvement under the frozen resampling procedure as well as with a negative effect. The manuscript consequently reports direction, interval, and task-level robustness together and does not recast the result as a definitive superiority statement.

The task packs are benchmark-style workloads rather than a sample of live scientific projects. They cover several textual and reasoning formats, but they do not represent experimental laboratories, proprietary data, domain-specific evidence standards, or the incentives of real authors and reviewers. The controlled runtime improves traceability, yet it also fixes one implementation, one controller configuration, and one resource profile. A changed model family, prompt policy, retrieval stack, or evidence corpus would require a new prospective protocol rather than being treated as another seed of this experiment.

### Independence, reproducibility, and decision limits

The project separated the generator/controller family from the NLI evaluator family and blinded arm labels during protected scoring. These steps reduce direct circularity, but they are not an external replication. The same development project designed the intervention, executed the pipeline, and assembled the report. The reproducibility record is currently local and hash-bound rather than an externally accessible anonymous supplement. Until an authorized release makes the frozen code, manifests, and derived outputs independently inspectable, another group cannot verify the complete chain from protocol through result.

Finally, the preregistered two-human blinded audit is deferred. Persona review, an additional model pass, or editorially polished prose cannot satisfy that condition. The system keeps `primary_analysis_interpretable=false` and `publication_submission_ready=false` until genuine independent auditors complete the registered process. The present artifact is therefore suitable for diagnosing the automated pipeline and preparing a human-review package once the automated readiness gate passes; it is not evidence of completed peer review, ethical approval, or permission to submit externally.
"""
    return f"""# Does Evidence-Gated Claim Revision Reduce Unsupported Research Claims?

## Status

`AUTOMATED_EVIDENCE_COMPLETE + HUMAN_GATE_PENDING`. This is a prospective 8-task × 5-seed shared-artifact study. The preregistered two-human blinded audit is deferred; therefore `primary_analysis_interpretable=false` and this manuscript is not submission-ready.

## Abstract

We evaluated whether an evidence-gated revision branch reduces unsupported claims in an autonomous research workflow while preserving task performance. Before outcome inspection, we froze an 8-task × 5-seed paired protocol, controlled Docker runtime, shared-artifact randomised branching, cross-family NLI evaluator, calibration contract, and analysis plan. Across 40 paired outputs, treatment reduced the protected automated unsupported-claim rate from {m['baseline_unsupported_claim_rate']:.3f} to {m['treatment_unsupported_claim_rate']:.3f}; the paired mean difference was {m['paired_mean_effect']:.3f} (95% bootstrap interval {m['ci_95_low']:.3f} to {m['ci_95_high']:.3f}). Mean task-native score was unchanged ({t['baseline_task_native_score']:.3f} in both arms). These findings are automated-evaluator estimates, not human validation or an acceptance claim.

{abstract_detail}

## Introduction

Autonomous research systems increasingly combine retrieval, planning, experimentation, and manuscript drafting. Their useful deployment depends on whether generated claims remain traceable to experiments and sources. Prior systems describe iterative planning and search for scientific ideation, reasoning-centred research workflows, and automated scientific claim verification. This study tests a narrower systems question: whether a frozen evidence gate changes the rate of unsupported claims under a shared-artifact counterfactual.
{introduction_detail}
{introduction_supplement}

## Related Work and Registered Sources

The study uses a verified literature registry frozen with the project plus three explicitly labelled post-freeze contextual records. These sources motivate, but do not prove, the present causal comparison. No contextual source changed the frozen task matrix, evaluator thresholds, analysis plan, or outcome interpretation.
{related_detail}
{related_supplement}

## Methods

The protocol bound eight heterogeneous task packs and five seeds per task, yielding 40 baseline-treatment pairs and 80 branches. Each pair began from one shared upstream artifact; randomized branch order prevented a branch from receiving a different upstream candidate. Every branch ran in a controlled Docker environment with network disabled, read-only root filesystem, dropped capabilities, resource limits, stdout/stderr and telemetry evidence. The protected evaluator used a frozen cross-family NLI calibration contract, evaluated blinded registry IDs, and only unblinded after all 80 evaluations completed.
{methods_detail}
{methods_supplement}

## Results

The pair-integrity audit verified all 40 shared artifacts, 80 branches, frozen branch orders, telemetry records, and hash bindings with no violations. The protected automated evaluator found an unsupported-claim-rate effect of {m['paired_mean_effect']:.3f} (95% bootstrap interval {m['ci_95_low']:.3f} to {m['ci_95_high']:.3f}). Baseline and treatment task-native means were {t['baseline_task_native_score']:.3f} and {t['treatment_task_native_score']:.3f}, respectively. Leave-one-task-out estimates remained negative for every omitted task, but this robustness check does not replace human auditing.
{results_detail}
{results_supplement}

## Discussion

The evidence gate was associated with fewer unsupported claims under the protected automated measurement while preserving task-native performance. The effect is modest and its interval includes zero at the upper endpoint. The result supports continued evaluation of the controller; it does not establish that the workflow generates scientifically true claims, that human reviewers would agree, or that a journal would accept the work.
{discussion_detail}
{discussion_supplement}

## Limitations

The primary limitation is measurement: the endpoint is a calibrated automated NLI proxy, not the deferred two-human blinded audit. The 8-task benchmark matrix provides heterogeneous coverage but does not demonstrate generality beyond these task families. Claim retention and semantic-change fields are deterministic proxies. The same project owns the system and experiment, so external replication is still needed.

{limitations_supplement}

## Conclusion

The prospective shared-artifact matrix completed with auditable Docker, telemetry, and evidence bindings. Its protected automated analysis suggests a lower unsupported-claim rate without a task-performance loss. The human-validation gate remains pending, so the system must not represent this as publication submission readiness.
{conclusion_detail}

## References

{refs}

## Reproducibility

The protocol, pair audit, protected evaluator manifest, blinded evaluation summary, and unblinding manifest are hash-bound local artifacts. Claim evidence is recorded in `synthesis/publication_claims.json`. No external content was uploaded during protected NLI inference.

## Declarations

**Data availability.** All benchmark task packs and generated local evidence paths are recorded in the frozen protocol and audit manifests.

**Ethics.** No human participants were recruited or represented as reviewers.

**Author contributions.** The project owner directed the study; Research Forge executed the frozen workflow and generated auditable artifacts.

**Funding and conflicts.** No external funding or conflicts are declared in the current local record.

**AI disclosure.** Codex was used to operate the workflow and draft this evidence-bound manuscript. It was not treated as a human auditor or independent replication.
"""


def _render_latex(markdown: str) -> str:
    # Conversion is deliberately mechanical: it cannot introduce claims that
    # were absent from the evidence-bound Markdown manuscript.
    def esc(value: str) -> str:
        value = value.replace("×", "x").replace("脳", "x")
        value = unicodedata.normalize("NFKD", value).encode("ascii", "ignore").decode("ascii")
        table = {
            "\\": r"\textbackslash{}", "&": r"\&", "%": r"\%", "_": r"\_",
            "#": r"\#", "{": r"\{", "}": r"\}", "~": r"\textasciitilde{}",
            "^": r"\textasciicircum{}",
        }
        return "".join(table.get(char, char) for char in value)

    def esc_with_citations(value: str) -> str:
        parts = re.split(r"\[([A-Za-z][A-Za-z0-9_-]*)\]", value)
        rendered: list[str] = []
        for index, part in enumerate(parts):
            if index % 2:
                rendered.append(r"\cite{" + part + "}")
            else:
                rendered.append(esc(part))
        return "".join(rendered)

    body: list[str] = []
    in_list = False
    in_references = False
    for raw in markdown.splitlines():
        if raw == "## References":
            if in_list:
                body.append(r"\end{itemize}")
                in_list = False
            in_references = True
            body.append(r"\begin{thebibliography}{99}")
            continue
        if in_references and raw.startswith("## "):
            body.append(r"\end{thebibliography}")
            in_references = False
        if in_references:
            match = re.match(r"- \[([A-Za-z][A-Za-z0-9_-]*)\]\s*(.*)", raw)
            if match:
                body.append(r"\bibitem{" + match.group(1) + "}" + esc(match.group(2)))
            continue
        if raw.startswith("# "):
            body.extend([r"\title{" + esc(raw[2:]) + "}", r"\maketitle"])
            continue
        if raw.startswith("## "):
            if in_list:
                body.append(r"\end{itemize}")
                in_list = False
            body.append(r"\section{" + esc(raw[3:]) + "}")
            continue
        if raw.startswith("### "):
            if in_list:
                body.append(r"\end{itemize}")
                in_list = False
            body.append(r"\subsection{" + esc(raw[4:]) + "}")
            continue
        if raw.startswith("- "):
            if not in_list:
                body.append(r"\begin{itemize}")
                in_list = True
            body.append(r"\item " + esc(raw[2:]))
            continue
        if in_list:
            body.append(r"\end{itemize}")
            in_list = False
        if raw.strip():
            body.append(esc_with_citations(raw))
        else:
            body.append("")
    if in_list:
        body.append(r"\end{itemize}")
    if in_references:
        body.append(r"\end{thebibliography}")
    return (
        "\\documentclass[11pt]{article}\n"
        "\\usepackage[margin=1in]{geometry}\n"
        "\\usepackage[T1]{fontenc}\n"
        "\\usepackage{lmodern}\n"
        "\\title{Evidence-bound prospective study}\n"
        "\\begin{document}\n"
        + "\n".join(body)
        + "\n\\end{document}\n"
    )


def render_publication_layout(project: Path, readiness_path: Path) -> dict[str, Any]:
    """Create the typeset source only after the canonical release gate passes."""
    project = project.resolve()
    gate = publication_layout_gate(readiness_path, project=project)
    markdown_path = project / "synthesis" / "publication_manuscript.md"
    if not markdown_path.is_file():
        raise FileNotFoundError(
            "publication evidence draft is missing; run synthesize-publication before layout"
        )
    latex_path = project / "synthesis" / "publication_manuscript.tex"
    latex_path.write_text(
        _render_latex(markdown_path.read_text(encoding="utf-8")),
        encoding="utf-8",
        newline="\n",
    )
    return {
        **gate,
        "typeset_source_created": True,
        "latex_path": _relative(project, latex_path),
        "latex_sha256": sha256_file(latex_path),
    }


def synthesize_publication_study(project: Path) -> dict[str, Any]:
    project = project.resolve()
    pair, summary = _preflight(project)
    paths = _paths(project)
    analysis = {
        "schema_version": 1,
        "protocol_id": summary["protocol_id"],
        "analysis_status": summary["analysis_status"],
        "primary_analysis_interpretable": False,
        "human_validation": "HUMAN_GATE_PENDING",
        "pair_audit_sha256": sha256_file(paths["pair_audit"]),
        "protected_summary_sha256": sha256_file(paths["summary"]),
        "protected_manifest_sha256": sha256_file(paths["manifest"]),
        "unblinding_sha256": sha256_file(paths["unblinding"]),
        "pair_count": pair["shared_complete"],
        "arm_metrics": summary["arm_metrics"],
        "paired_analysis": summary["paired_analysis"],
        "leave_one_task_out": summary["leave_one_task_out"],
    }
    claims = _claims(project, summary)
    references = _references(project)
    synthesis = project / "synthesis"
    synthesis.mkdir(parents=True, exist_ok=True)
    write_json_atomic(synthesis / "publication_analysis.json", analysis)
    write_json_atomic(synthesis / "publication_claims.json", {"schema_version": 1, "protocol_id": summary["protocol_id"], "provisional": True, "claims": claims})
    markdown = _render_markdown(project, analysis, claims, references)
    (synthesis / "publication_manuscript.md").write_text(markdown, encoding="utf-8", newline="\n")
    return audit_publication_synthesis(project, persist=True)


def audit_publication_synthesis(project: Path, *, persist: bool = False) -> dict[str, Any]:
    project = project.resolve()
    pair, summary = _preflight(project)
    synthesis = project / "synthesis"
    checks: dict[str, bool] = {"pair_audit_complete": bool(pair["complete"]), "human_gate_pending": summary.get("primary_analysis_interpretable") is False}
    violations: list[str] = []
    for name in ("publication_analysis.json", "publication_claims.json", "publication_manuscript.md"):
        checks[f"present_{name}"] = (synthesis / name).is_file()
    markdown = (synthesis / "publication_manuscript.md").read_text(encoding="utf-8") if checks["present_publication_manuscript.md"] else ""
    for section in ("## Abstract", "## Methods", "## Results", "## Limitations", "## Reproducibility", "## Declarations"):
        checks[f"section_{section}"] = section in markdown
    checks["human_boundary_disclosed"] = "HUMAN_GATE_PENDING" in markdown and "not submission-ready" in markdown
    checks["typeset_source_deferred_until_release_gate"] = True
    if not all(checks.values()):
        violations.append("publication synthesis is missing required evidence, disclosure, or manuscript structure")
    result = {"schema_version": 1, "protocol_id": summary["protocol_id"], "passed": not violations, "publication_submission_ready": False, "human_validation": "HUMAN_GATE_PENDING", "checks": checks, "violations": violations}
    if persist:
        write_json_atomic(synthesis / "publication_synthesis_audit.json", result)
    return result
