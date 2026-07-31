from __future__ import annotations

import json
import shutil
from pathlib import Path

import matplotlib

matplotlib.use("Agg")
import matplotlib.pyplot as plt

from research_forge.paper_evaluation_transparency import (
    assess_stage4_evidence_sufficiency,
    build_evaluation_transparency_register,
)
from research_forge.storage import write_json_atomic, write_text_atomic


ROOT = Path(__file__).resolve().parents[1]
PROJECT = ROOT / "stage1_runs" / "research-agent-evidence-publication-v1"
OUTPUT = (
    ROOT
    / "output"
    / "pdf"
    / "research-agent-evidence-rev12-transparency"
)
FIGURES = OUTPUT / "figures"


def _load_evidence() -> dict:
    return json.loads(
        (PROJECT / "synthesis" / "publication_analysis.json").read_text(
            encoding="utf-8"
        )
    )


def _assert_frozen_values(evidence: dict) -> None:
    baseline = evidence["arm_metrics"]["baseline"]
    treatment = evidence["arm_metrics"]["treatment"]
    paired = evidence["paired_analysis"]
    human = evidence["human_audit"]["final_verdict_counts"]
    gate = evidence["manual_gate"]
    assert evidence["pair_count"] == 40
    assert baseline["eligible_claim_count"] == 116
    assert treatment["eligible_claim_count"] == 116
    assert baseline["unsupported_count"] == 4
    assert treatment["unsupported_count"] == 1
    assert paired["mean"] == -0.025
    assert paired["ci_95"] == [-0.06666666666666667, 0.0]
    assert human == {"abstain": 3, "supported": 120, "unsupported": 5}
    assert gate["audited_evaluator_unsupported"] == 5
    assert gate["audit_false_positive_count"] == 4
    assert evidence["context_repair_verification"]["supported_cases"] == 5


def _create_results_figure(evidence: dict) -> None:
    FIGURES.mkdir(parents=True, exist_ok=True)
    baseline = evidence["arm_metrics"]["baseline"]
    treatment = evidence["arm_metrics"]["treatment"]
    gate = evidence["manual_gate"]

    navy = "#17324d"
    blue = "#2a7faa"
    gray = "#64717a"
    orange = "#c44e38"
    pale = "#edf3f8"

    figure, axes = plt.subplots(
        1,
        3,
        figsize=(11.2, 3.15),
        gridspec_kw={"width_ratios": [0.9, 1.25, 0.9]},
    )

    rates = [
        baseline["unsupported_claim_rate"],
        treatment["unsupported_claim_rate"],
    ]
    counts = [baseline["unsupported_count"], treatment["unsupported_count"]]
    denominators = [
        baseline["eligible_claim_count"],
        treatment["eligible_claim_count"],
    ]
    axes[0].bar([0, 1], rates, color=[gray, blue], width=0.58)
    axes[0].set_xticks([0, 1], ["Baseline", "Claim gate"])
    axes[0].set_ylabel("Automated alert rate")
    axes[0].set_ylim(0, 0.042)
    for index, value in enumerate(rates):
        axes[0].text(
            index,
            value + 0.0015,
            f"{counts[index]}/{denominators[index]}\n({value:.3f})",
            ha="center",
            fontsize=8.5,
        )
    axes[0].set_title("A  Registered proxy", loc="left", weight="bold")

    matrix = [[2, 1, 2], [118, 4, 1]]
    axes[1].imshow(matrix, cmap="Blues", vmin=0, vmax=118)
    axes[1].set_xticks(
        range(3),
        ["Supported", "Unsupported", "Indeterminate"],
        rotation=22,
        ha="right",
    )
    axes[1].set_yticks(
        range(2), ["Automated alert", "No automated alert"]
    )
    axes[1].set_xlabel("Blinded human decision")
    for row in range(2):
        for column in range(3):
            axes[1].text(
                column,
                row,
                str(matrix[row][column]),
                ha="center",
                va="center",
                color="white" if matrix[row][column] > 60 else navy,
                weight="bold",
            )
    axes[1].set_title("B  Human validity check", loc="left", weight="bold")

    nonconfirmation = gate["audit_false_positive_rate"]
    axes[2].errorbar(
        [0],
        [nonconfirmation],
        yerr=[
            [nonconfirmation - 0.284],
            [0.995 - nonconfirmation],
        ],
        fmt="o",
        color=orange,
        capsize=5,
        lw=2,
    )
    axes[2].axhline(
        gate["false_positive_threshold"],
        color=navy,
        linestyle="--",
        linewidth=1.6,
        label="registered limit 0.15",
    )
    axes[2].set_xlim(-0.6, 0.6)
    axes[2].set_xticks([0], ["Protocol\nnon-confirmation"])
    axes[2].set_ylim(0, 1.05)
    axes[2].set_ylabel("Rate")
    axes[2].legend(frameon=False, fontsize=8, loc="lower right")
    axes[2].set_title("C  Interpretation gate", loc="left", weight="bold")

    for axis in axes:
        axis.spines[["top", "right"]].set_visible(False)
    figure.tight_layout()
    figure.savefig(FIGURES / "registered_results.pdf", bbox_inches="tight")
    figure.savefig(
        FIGURES / "registered_results.png",
        dpi=300,
        bbox_inches="tight",
    )
    plt.close(figure)


def _copy_concept_figure() -> None:
    source = ROOT / "output" / "pdf" / "submission_rirp" / "figures"
    for suffix in ("drawio", "pdf", "png", "svg"):
        shutil.copy2(
            source / f"figure1_governed_workflow.{suffix}",
            FIGURES / f"evidence_bound_workflow.{suffix}",
        )


MANUSCRIPT = r"""
\documentclass[11pt]{article}
\usepackage[margin=2.45cm]{geometry}
\usepackage[T1]{fontenc}
\usepackage{lmodern}
\usepackage{graphicx}
\usepackage{booktabs}
\usepackage{tabularx}
\usepackage{array}
\usepackage{ragged2e}
\usepackage{placeins}
\usepackage{float}
\usepackage[font=small,labelfont=bf]{caption}
\usepackage[hidelinks]{hyperref}
\newcolumntype{Y}{>{\RaggedRight\arraybackslash\hspace{0pt}}X}
\newcolumntype{L}[1]{>{\RaggedRight\arraybackslash}p{#1}}
\setlength{\parindent}{1.7em}
\setlength{\parskip}{0.28em}
\setlength{\emergencystretch}{2em}
\title{Auditing Evidence Boundaries in Autonomous Research Agents:\\A Same-Backbone Evaluation Study}
\author{Anonymous submission}
\date{}

\begin{document}
\maketitle

\begin{abstract}
Autonomous research agents increasingly rely on learned evaluators to decide whether generated claims are supported, yet an evaluator score is meaningful only relative to the evidence packet and decision rule that produced it. We introduce an evidence-bound evaluation framework that separates intervention measurement, evaluator validity, and post-hoc fault diagnosis. The framework is instantiated in a preregistered same-backbone study of a pre-delivery claim gate across eight tasks and five seeds. Forty shared upstream artifacts were each processed by matched baseline and gated branches, yielding 80 downstream runs. The frozen automated evaluator reported unsupported-claim rates of 4/116 (0.034) for baseline and 1/116 (0.009) for treatment, with a paired mean difference of -0.025 and a 95\% hierarchical-bootstrap interval of [-0.067, 0.000]. Mean task-native performance was 0.337 in both arms. A blinded human assessment of 128 claims did not satisfy the registered validity condition for interpreting this proxy as a treatment benefit: among five automated alerts, two claims were judged supported, one unsupported, and two indeterminate. Preserved provenance then identified two boundary defects. Three comparative claims lacked task targets and metric direction in their evidence packets, while two exact-metric claims were affected by a decision rule that allowed probabilistic natural-language inference to override deterministic equality. A repaired packet schema and precedence rule supported all five historical cases on regression replay. The study does not establish that claim gating improves scientific correctness. It shows how an agent evaluation can detect that its own proxy is not authorized for confirmatory interpretation, localize the implicated boundary, and constrain repair without rewriting the historical result.
\end{abstract}

\noindent\textbf{Keywords:} autonomous research agents; evaluation infrastructure; claim verification; evidence provenance; validity assessment; fault localization.

\section{Introduction}

Autonomous research agents combine literature retrieval, planning, code execution, result interpretation, and manuscript generation. These systems also need a way to decide whether their own claims are supported. A common solution is to place a learned evaluator inside the workflow. The evaluator receives a claim and an evidence packet, then assigns a support label or score. This arrangement scales, but it creates a second scientific problem: the measurement system can fail even when the underlying claim and result record are correct.

Three boundaries determine the meaning of a learned support judgment. First, the evidence packet must contain the task state required to interpret the claim. A statement that a score ``did not reach the target'' cannot be checked from the observed score alone. Second, deterministic relations should retain authority when they are available. Exact equality between a reported metric and a frozen run record should not be overturned by a lower-confidence semantic classifier. Third, an automated proxy should not become a scientific conclusion unless its validity has been assessed for the application distribution. Public benchmark calibration addresses model behavior on a benchmark; it does not establish that project-specific packets contain the information needed for the same labels.

We study these boundaries through an evidence-bound evaluation framework. The framework treats automated claim checking, independent validity assessment, and fault diagnosis as different evidence classes. An automated contrast is recorded even when it later fails its validity condition, but the failed condition prevents that contrast from being reported as a treatment benefit. Provenance is then used to identify the earliest boundary that could have prevented the disagreement. A repair creates a successor implementation and a new analysis record; it does not modify the original run or its registered interpretation.

The empirical case is a pre-delivery claim gate. Each experimental unit begins with one frozen upstream artifact. A baseline branch follows the ordinary revision path, while a treatment branch applies a claim-evidence gate before delivery. Both branches use the same upstream artifact, task, seed, and evaluator. This paired design isolates the downstream controller from stochastic upstream generation. The study asks:

\begin{enumerate}
    \item Does the claim gate change the frozen automated unsupported-claim rate without changing task-native performance?
    \item Does the automated endpoint satisfy its registered human-validity condition?
    \item If the validity condition is not met, can the preserved evidence path distinguish packet insufficiency from decision-rule error and support a bounded repair?
\end{enumerate}

The first question concerns a protected proxy. The second controls whether that proxy may be interpreted as a treatment effect on claim support. The third is explicitly diagnostic because it begins after unblinding. Keeping these questions separate changes the narrative of a negative validity result. The key observation is not that an experiment ``failed.'' It is that the evaluation system refused to convert a favorable proxy contrast into a scientific benefit claim and retained enough state to explain why.

The study makes three contributions. First, it defines an evidence-bound measurement architecture in which support labels remain conditional on packet completeness and decision authority. Second, it provides a same-backbone paired design with an independent validity gate, equal eligible denominators, and preserved task-native performance. Third, it presents a bounded diagnostic case that separates three packet-context omissions from two decision-precedence errors. The five repaired cases are regression fixtures, not an out-of-sample estimate of evaluator accuracy or fault-localization performance.

\begin{figure}[tbp]
\centering
\includegraphics[width=0.94\linewidth]{figures/evidence_bound_workflow.pdf}
\caption{Evidence-bound evaluation separates the registered run, independent validity condition, and traceable outcome.}
\label{fig:framework}
\end{figure}

\section{Methods}

Figure~\ref{fig:framework} summarizes the framework. A read-only project boundary supplies the registered run. The run produces experimental outputs and the exact evidence shown to the automated evaluator. A separate validity assessment determines which interpretation is permitted. When the validity condition is not met, the historical measurements remain available as proxy observations, while diagnosis and repair proceed through appended artifacts.

\subsection{Evidence classes and authority}

The framework distinguishes four evidence classes. \emph{Experimental evidence} includes branch outputs, metrics, denominators, and pair bindings. \emph{Automated evaluation evidence} includes claim-level labels and calibrated scores. \emph{Validity evidence} consists of judgments produced under a separately frozen assessment protocol. \emph{Diagnostic evidence} is generated after outcome exposure to explain a discrepancy or test a repair.

These classes are not interchangeable. A successful run does not validate its evaluator. A public evaluator benchmark does not prove that a project packet is complete. A post-hoc explanation cannot retroactively authorize a primary endpoint. The authority order is therefore structural and deterministic checks, frozen experimental outputs, frozen statistical rules, independent adjudication, and finally auxiliary learned alerts. Natural-language inference (NLI) is useful for relations that cannot be resolved deterministically, but it does not own state transitions or scientific verdicts.

This separation prevents two common upgrades. First, an automated label cannot be described as scientific ground truth merely because it was produced at scale. Second, a repair that succeeds on the same cases used to design it cannot be called independent confirmation. The framework records both observations while assigning each a bounded inference.

\subsection{Packet completeness and decision precedence}

An evidence packet is an interface contract, not a bag of nearby text. For an experiment claim, the packet must bind the immutable claim text to the task specification, run identifier, metric name and value, optimization direction, target or baseline when referenced, artifact identifiers, and integrity checks. For a literature claim, the packet instead binds the statement to resolvable sources and passages. Missing state produces an abstention or a risk alert; it should not be silently inferred from project knowledge that was not supplied to the evaluator.

Decision precedence follows the strength of the available relation. Structural failures such as missing identifiers, broken hashes, or unresolved artifacts stop evaluation. Exact numerical relations are checked next. A claim reporting metric \(m=v\) is supported when the frozen run reports the same metric and value within the registered tolerance. A comparative claim additionally requires the referenced target and optimization direction. NLI is used only for residual semantic relations. This ordering does not make deterministic rules universally superior; it prevents a probabilistic semantic score from overriding a relation the system can verify exactly.

\subsection{Validity, diagnosis, and repair}

The validity condition is frozen before the protected outcome is interpreted. If it is not met, the automated endpoint remains a historical proxy but cannot support the planned confirmatory claim. Diagnosis may then restore authorized context, inspect lineage, and assign a candidate fault to the earliest preventable boundary. The diagnosis is append-only and explicitly post-unblinding.

A repair contract names the affected packet builder, evaluator, or aggregation rule. Same-case replay tests whether the implementation now handles the diagnosed examples. A fresh successor run is required to estimate generalization or a treatment effect under the repaired system. This distinction is central: regression replay answers whether a known defect was addressed, whereas prospective reevaluation asks whether the revised measurement system works on unseen cases.

\subsection{Study design}

Table~\ref{tab:design} gives the experimental design at a glance.

\begin{table}[H]
\centering
\caption{Registered design and interpretation boundary}
\label{tab:design}
\small
\renewcommand{\arraystretch}{1.12}
\begin{tabularx}{\linewidth}{L{0.27\linewidth}Y}
\toprule
Element & Frozen specification \\
\midrule
Experimental unit & One task-seed upstream artifact processed by both branches. \\
Matrix & Eight task families, five seeds, 40 pairs, and 80 downstream branches. \\
Intervention & Pre-delivery claim-evidence gate versus ordinary revision. \\
Primary proxy & Unsupported automated judgments divided by eligible claims. \\
Primary contrast & Treatment rate minus baseline rate; negative values favor treatment on the proxy. \\
Safeguard & Mean task-native score, claim counts, eligibility, abstention, and semantic-change traces. \\
Validity condition & Automated alerts must satisfy the frozen blinded human-assessment rule; registered non-confirmation limit 0.15. \\
Diagnostic boundary & Context restoration and repair occur only after the blinded result is closed. \\
\bottomrule
\end{tabularx}
\end{table}

\subsection{Paired intervention and estimand}

The protocol combined eight heterogeneous benchmark task families with five seeds. Each task-seed pair produced one upstream artifact before random assignment of branch order. The baseline and treatment branches then consumed that same frozen artifact. The branch-specific controller could revise claims, but it could not change the task, seed, upstream output, evaluator thresholds, metric definitions, or validity rule.

For pair \(i\), let \(U_{i,T}\) and \(U_{i,B}\) denote the fraction of eligible claims labelled unsupported in treatment and baseline. The registered pair effect is \(D_i=U_{i,T}-U_{i,B}\). The primary summary is the mean of the 40 pair effects. A hierarchical bootstrap sampled eight task identifiers with replacement and then five seed-pairs within each sampled task, repeated 10,000 times with frozen seed 20260719. The 2.5th and 97.5th percentiles form the descriptive interval. With only eight task clusters, the interval is not treated as a population-wide guarantee or evidence of broad superiority.

The task-native score was a safeguard against a trivial reduction in alerts obtained by degrading or emptying the output. Claim totals, eligible denominators, abstentions, retention or deletion, and semantic-change traces were preserved for the same reason. These fields do not constitute a fully validated utility scale, but they make several simple forms of proxy gaming observable.

\subsection{Claim evaluation}

Claims were extracted from final conclusion registries. Baseline produced 156 claims and treatment 152. The eligibility rule excluded novelty claims because absence from a bounded source set cannot establish novelty: 40 baseline and 36 treatment claims were excluded for this reason. No other exclusion reason was used. The remaining 116 claims in each arm entered the automated support denominator. An eligible experimental claim required a frozen run identifier, a matching metric, an in-bound artifact reference, and successful structural validation. Within the eligible sets, baseline produced 110 supported, four unsupported, and two abstaining decisions; treatment produced 114 supported, one unsupported, and one abstaining decision.

The protected evaluator was a cross-family DeBERTa NLI model \cite{He2020}. Thresholds were selected before project application on the public SciFact training split \cite{Wadden2020}. A SHA-256 rule assigned 501 examples to threshold selection and 456 to locked evaluation. The prespecified grid ranged from 0.50 to 0.95 in increments of 0.05; maximizing selection macro-F1 chose entailment and contradiction thresholds of 0.55. The untouched locked split yielded macro-F1 0.7798 and coverage 0.9868. There was no project-specific threshold tuning. These values characterize benchmark calibration, not agreement on controller-generated claims. During the formal run, the evaluator received blinded registry identifiers and frozen packets. Arm labels were joined only after all 80 evaluations were complete.

\subsection{Blinded validity assessment and reporting}

The human assessment contained 128 claims, sampled across task and arm cells. All five available automated alerts were included; remaining slots were filled from claims without alerts. Two independent auditors received claim-evidence packets without task identity, arm, evaluator label, or treatment summary. They used supported, unsupported, and indeterminate labels. A separately attested adjudicator resolved disagreements under the same blinding.

The registered rule counted an automated alert as confirmed only when the final human decision was unsupported. Supported and indeterminate decisions therefore both counted as non-confirmations for the stopping rule. The original protocol called this quantity a false-positive rate. Because an indeterminate decision does not establish that an alert was false, we use the more precise term \emph{protocol non-confirmation rate} in the manuscript while preserving the registered calculation. The 0.15 stopping limit was frozen before review, but the frozen record contains no power analysis, risk model, or external calibration justifying that value. It is therefore an operational validity limit, not an estimated natural constant.

Context restoration began only after the blinded assessment closed. It returned the task target, metric direction, and frozen task specification to the five alerted cases. The resulting judgments were used to diagnose the measurement path; they do not replace the blinded labels or recompute the paired effect.

\section{Results}

\subsection{Registered proxy contrast}

All 40 pairs and 80 branches completed the registered execution and pair-integrity checks. Baseline produced 156 total claims and treatment 152. Each arm contributed 116 eligible claims. The evaluator issued four alerts in baseline and one in treatment, corresponding to rates of 0.034 and 0.009.

\begin{table}[H]
\centering
\caption{Claim flow through the frozen automated evaluator}
\label{tab:flow}
\small
\renewcommand{\arraystretch}{1.10}
\begin{tabularx}{\linewidth}{L{0.20\linewidth}rrrrr}
\toprule
Arm & Total & Novelty excluded & Eligible & Support / alert / abstain & Alert rate \\
\midrule
Baseline & 156 & 40 & 116 & 110 / 4 / 2 & 0.034 \\
Claim gate & 152 & 36 & 116 & 114 / 1 / 1 & 0.009 \\
\bottomrule
\end{tabularx}
\end{table}

The mean paired difference was -0.025. The hierarchical-bootstrap interval was [-0.067, 0.000], and the median pair effect was 0. The frozen aggregate task-native values were exactly equal at full stored precision, 0.3370035177485372 in each arm; 0.337004 is the six-decimal display value, not an equality produced by rounding. Thus the frozen automated proxy moved in the favorable direction without an aggregate change in the task safeguard, but the interval includes zero and the validity condition still controls interpretation.

\begin{table}[H]
\centering
\caption{Registered experiment and validity results}
\label{tab:results}
\small
\renewcommand{\arraystretch}{1.12}
\begin{tabularx}{\linewidth}{L{0.20\linewidth}L{0.15\linewidth}L{0.15\linewidth}L{0.25\linewidth}Y}
\toprule
Comparison & Control mean & Treatment mean & Effect (95\% CI) & Status \\
\midrule
Automated alert rate & 0.034 (4/116) & 0.009 (1/116) & -0.025 [-0.067, 0.000] & Proxy; non-confirmatory \\
Task-native safeguard & 0.337004 & 0.337004 & 0.000 & No aggregate change \\
\bottomrule
\end{tabularx}
\end{table}

The task-level pattern was sparse. Five of eight task families had a mean effect of zero; FinQA Accuracy, Yelp Review Full Accuracy, and SICK Accuracy each had a mean of -0.066667 across their five seeds. Leave-one-task-out means ranged from -0.019048 to -0.028571. No omitted family reversed the sign, but concentration in three families limits any heterogeneity or generalization claim.

\clearpage

\subsection{Human validity condition}

The two auditors agreed on 123/128 claims before adjudication, an agreement rate of 0.961 with Cohen's \(\kappa=0.602\). Final decisions were 120 supported, five unsupported, and three indeterminate.

\begin{table}[H]
\centering
\caption{Automated alerts and blinded human decisions}
\label{tab:human}
\small
\renewcommand{\arraystretch}{1.12}
\begin{tabularx}{\linewidth}{L{0.29\linewidth}rrrr}
\toprule
Automated decision & Supported & Unsupported & Indeterminate & Total \\
\midrule
Alert & 2 & 1 & 2 & 5 \\
No alert & 118 & 4 & 1 & 123 \\
\midrule
Total & 120 & 5 & 3 & 128 \\
\bottomrule
\end{tabularx}
\end{table}

Only one of the five automated alerts was confirmed as unsupported under the blinded protocol. Treating the human unsupported label as the application reference, alert precision was 1/5 (0.20) and recall was 1/5 (0.20); these estimates are descriptive because only five human-unsupported cases were observed. The protocol non-confirmation rate was 4/5 (0.800), with an exact 95\% binomial interval of [0.284, 0.995]. This exceeded the registered limit of 0.15. Consequently, the automated proxy contrast was not authorized for confirmatory interpretation as improved claim support.

This result should not be summarized as five NLI mistakes. Two alerted claims were supported under the packets shown to blinded reviewers, one was unsupported, and two were indeterminate. The latter judgments are consistent with insufficient packet context. The validity assessment establishes that the automated endpoint did not meet its registered interpretation condition; the cause requires the separate context-restored analysis.

\clearpage

\begin{figure}[tbp]
\centering
\includegraphics[width=\linewidth]{figures/registered_results.pdf}
\caption{The automated proxy, blinded human decisions, and registered interpretation gate.}
\label{fig:results}
\end{figure}

\subsection{Boundary diagnosis and bounded repair}

After unblinding, the five automated alerts were re-examined with the registered task specifications restored. All five claims were then judged supported. Provenance separated the cases into two operational fault classes.

Three comparative claims stated that a score was below a target or non-improving. Their packets included the observed run result but omitted the referenced target, optimization direction, and task-specification binding. With the incomplete packet, neither a model nor a blinded human necessarily had enough information to establish the comparison. These cases locate the earliest preventable fault at evidence packaging.

Two exact-metric claims had sufficient evidence: the metric name and value matched the valid isolated run record. The aggregation layer nevertheless allowed probabilistic NLI to override deterministic equality. These cases locate the earliest preventable fault at decision precedence rather than packet completeness.

\clearpage

\begin{table}[H]
\centering
\caption{Diagnosis of the five automated alerts}
\label{tab:cases}
\footnotesize
\renewcommand{\arraystretch}{1.12}
\begin{tabularx}{\linewidth}{L{0.08\linewidth}L{0.33\linewidth}L{0.26\linewidth}Y}
\toprule
Case & Claim form & Blinded decision & Earliest preventable boundary \\
\midrule
C1 & Score 0.2 did not reach target 0.778 & Indeterminate & Missing target and direction in packet \\
C2 & Score 0.8145 was below target 0.905 & Indeterminate & Missing target and direction in packet \\
C3 & Verified run achieved score 0.5687 & Supported & NLI overrode exact metric equality \\
C4 & Valid run did not reach target 0.7803 & Unsupported & Missing target and direction in packet \\
C5 & Valid run achieved score 0.8145 & Supported & NLI overrode exact metric equality \\
\bottomrule
\end{tabularx}
\end{table}

Table~\ref{tab:packet} makes the two repair operations concrete. The first row is a packet-completeness repair: the run score was present, but the comparison operands were not. The second is an authority repair: the exact value was already bound, but the learned verdict was allowed to dominate it.

\clearpage

\begin{table}[H]
\centering
\caption{Minimal packet and decision-rule changes used in regression replay}
\label{tab:packet}
\footnotesize
\renewcommand{\arraystretch}{1.10}
\begin{tabularx}{\linewidth}{L{0.19\linewidth}L{0.34\linewidth}Y}
\toprule
Case type & Before repair & After repair \\
\midrule
Comparative claim & \texttt{metric=Accuracy}, \texttt{value=0.2}; target 0.778 and direction absent & Adds \texttt{target=0.778}, \texttt{direction=maximize}, task ID, and task-specification digest \\
Exact metric claim & Frozen metric and claim value match, but NLI owns the final decision & Exact numeric binding is evaluated before residual NLI and cannot be overwritten \\
\bottomrule
\end{tabularx}
\end{table}

The repaired packet schema requires the task identifier, metric, direction, baseline or target when referenced, and task-specification digest. The repaired decision rule evaluates structural integrity first, exact metric and target relations second, and residual semantic relations with NLI last. Regression replay returned supported for all five cases. Because the cases selected the repair, this 5/5 result verifies handling of known defects only. It is not an estimate of performance on unseen claims and does not change the historical validity result.

\section{Discussion}

\subsection{What the study establishes}

The study establishes a bounded systems result: a favorable automated proxy contrast can coexist with an invalid interpretation path, and an evidence-bound framework can keep those facts separate. The paired design controlled stochastic upstream generation. The validity gate then prevented the proxy from becoming a claim that the treatment improved scientific correctness. Provenance distinguished missing information from an unsafe aggregation rule and supported a targeted implementation repair.

This reframing matters for agent evaluation. A learned evaluator is neither useless nor self-authenticating. In the present case, the alert layer identified a small set that warranted review, but its output did not distinguish a genuinely unsupported claim from a claim whose evidence packet was incomplete. NLI is therefore best interpreted here as a conservative risk signal. Packet-completeness checks identify missing state, deterministic rules handle exact bindings, and human assessment resolves remaining application-level ambiguity.

The unchanged task-native mean reduces one simple concern: the gated branch did not improve its proxy merely by lowering the registered aggregate task score. It does not prove equal information value. The treatment changed total claim counts slightly, and the retention, deletion, semantic-change, and informativeness fields have not been validated as human utility measures. A stronger follow-up should assess these dimensions directly.

\subsection{Implications for research-agent infrastructure}

First, evaluator input is part of the measurement instrument. Logging a model identifier and threshold is insufficient when task targets, directions, source passages, or artifact bindings can disappear upstream. A reproducible evaluator record should therefore include the packet schema and content digest.

Second, decision systems should encode authority explicitly. Deterministic checks may disqualify evidence or resolve exact relations; learned scores should not silently overwrite them. Conversely, deterministic equality does not settle narrative, causal, or novelty claims, which still require semantic evidence and calibrated abstention.

Third, validity failure and implementation failure should be represented separately. The blinded assessment governs the historical endpoint. The context-restored review explains the disagreement. The regression replay verifies a known-case repair. Collapsing these records would allow a polished successor implementation to erase an unfavorable original result.

Finally, fault ownership is a claim that itself needs evidence. The present lineage supports two specific assignments, but the study has no comparator against ordinary logs, unit tests, or unaided debugging. It therefore does not establish general fault-localization accuracy or efficiency.

These observations motivate a publication-stage evidence-sufficiency gate. Before prose generation, the platform now freezes an operational transparency register covering eligibility flow, abstention, threshold provenance, sensitivity analyses, packet examples, repair validation, and release assets. Missing descriptive analyses become mandatory disclosures. A missing item that can change scientific interpretation---including an unaccounted eligibility denominator, an untraceable threshold, or the absence of prospective evidence after a repair---blocks publication, appends a diagnostic and scientific-successor request, and returns the Study to Stage 3. The historical Run and Verdict remain immutable; new evidence enters through an owner-approved Research Contract revision.

\subsection{Next experiment}

The appropriate successor is prospective. It should freeze the context-bound packet schema and deterministic precedence before viewing new outcomes, execute the same eight-task by five-seed matrix on fresh artifacts, and repeat the two-auditor blinded assessment. The five historical cases should remain regression fixtures and be excluded from the new validity sample.

A stronger measurement study should also compare verifier designs: NLI-only, deterministic-only, and a hybrid verifier using deterministic checks before NLI. A second evaluator family would show whether alerts depend on one NLI model. The primary analysis should report alert precision and abstention separately, preserve the full human decision matrix, and avoid treating indeterminate decisions as demonstrated false positives.

\section{Related Work}

Autonomous research systems organize search, experimentation, and manuscript production into increasingly long workflows \cite{Lu2024}. As these systems add self-review, their evaluators become part of the scientific measurement chain rather than an external quality check. The present work focuses on that chain rather than on general research autonomy.

Scientific claim verification formalizes support as a relation between a claim and supplied evidence. SciFact provides a benchmark for scientific claims and evidence \cite{Wadden2020}; SciClaimHunt expands evidence-based verification data \cite{Kumar2025}, and SciVer tests multimodal scientific claims \cite{SciVer2025}. Retrieval-augmented verification extends the relation to evidence selection in applied settings \cite{Liu2024}. OpenScholar studies literature synthesis with retrieval-augmented language models \cite{Asai2025}. Our case concerns a downstream systems dependency: even a correct run record is insufficient when the evaluation packet omits task state needed to interpret a comparison.

Recent systems move verification closer to scientific review. FactReview combines evidence grounding with code execution for empirical-claim auditing \cite{Yue2026}, while Peerispect retrieves manuscript evidence and applies NLI to check claims made in reviews \cite{Ghorbanpour2026}. MedRAGChecker combines evidence-grounded NLI with biomedical knowledge-graph signals and reports claim-level failure modes \cite{Ji2026}. These systems motivate modular and hybrid verification. The present study contributes a paired intervention design, an application-level validity stop, and immutable separation between historical endpoint, diagnosis, and successor evidence; it does not empirically outperform those verifiers.

The protected evaluator uses DeBERTa \cite{He2020}, but the contribution is not a new NLI model. It is the separation of semantic scoring from structural integrity, deterministic binding, and application-specific validity. GUIDE similarly treats evaluation as a hierarchical diagnostic process for GUI agents \cite{Zhai2026}; our fault boundary is instead claim--evidence measurement in research workflows. Transparent artifacts and independent checks address distinct reproducibility failures \cite{Munafo2017,Pineau2021}.

Provenance models represent relations among entities, activities, and agents \cite{Moreau2013}. The present implementation uses a project-specific lineage to connect claims, packets, task specifications, automated decisions, human judgments, and repairs. It does not claim formal PROV conformance. Explicit boundaries also parallel work on hidden technical debt in machine-learning systems, where locally reasonable components can create system-level failure through entanglement and unstable interfaces \cite{Sculley2015}.

Unlike benchmark-only verification, the present framework treats an evaluator decision as one versioned event in a larger experimental system. Its object of study is therefore not only classification quality, but whether the supplied packet, authority order, validity rule, and later repair preserve the inference boundary of the underlying experiment.

\section{Limitations}

The empirical scope is narrow. The task suite contains eight benchmark families rather than live scientific projects, and there are only eight independent task clusters. The hierarchical-bootstrap interval reaches zero, no prospective power target was registered, and five task families show no mean proxy change. These facts preclude a superiority or broad-generalization claim.

The automated endpoint is a claim-evidence proxy, not scientific truth. Public-gold calibration does not eliminate distribution shift to controller-generated claims. The human validity assessment is stronger for application interpretation, but only five automated alerts were available, producing a wide exact interval. The registered stopping rule was still crossed, yet the alert-level proportions remain imprecise.

The 0.15 validity limit was preregistered but lacks a frozen power or risk derivation. The study also lacks a threshold-sensitivity analysis, a second evaluator family, and a direct NLI-only versus deterministic-only versus hybrid-verifier ablation. The present data therefore cannot separate model-family dependence from packet and aggregation effects.

Diagnosis used the same five cases that motivated the repair. Context restoration and 5/5 replay therefore provide mechanistic evidence about known examples, not an unbiased accuracy estimate. No fresh prospective successor has yet been executed, and fault localization was not compared with ordinary logs, unit tests, or unaided debugging. The same project designed, executed, diagnosed, and reported the study. External reproduction and prospective testing are required.

The task-native safeguard rules out an aggregate score decrease at stored precision, but it is not a validated measure of claim informativeness or semantic preservation. Claim extraction and atomicity were governed by the registry rules, yet no separate human study quantified extraction recall or utility. These missing analyses are explicit evidence gaps rather than implied negative findings.

The treatment used more calls, tokens, and recorded cost: 184 versus 80 model calls, 5,185,957 versus 3,586,080 tokens, and 47.773230 versus 28.183812 recorded USD. Wall-clock fields cannot be compared because their start and stop boundaries differ. No deployment recommendation or cost-effectiveness conclusion follows.

\section{Conclusion}

Autonomous research agents require measurement systems whose evidence boundaries are inspectable. In a same-backbone paired study, a pre-delivery claim gate reduced a frozen automated alert rate without changing mean task-native score, but the automated endpoint did not satisfy its registered human-validity condition. The framework therefore retained the proxy observation while withholding a treatment-benefit claim.

Post-unblinding provenance separated three incomplete evidence packets from two unsafe decision-precedence cases. A bounded repair handled those five historical examples, but only a fresh successor can test generalization. The broader design principle is simple: evaluator inputs, deterministic authority, validity conditions, and repair boundaries should be first-class experimental artifacts.

\section*{Reproducibility availability}

The protocol, pair audit, evaluator manifest, blinded assessment, unblinding record, diagnosis, repair schema, and replay outputs are content-addressed in the project record. An anonymized review package will include the analysis code, task-pack manifests, frozen configuration, claim packets, and artifact hashes. The manuscript does not claim independent replication; the current package records author-side reproducibility and the planned successor protocol.

\section*{Ethics statement}

No research participants were enrolled as study subjects. Two independent auditors and a blinded adjudicator reviewed the frozen claim-evidence sample. Their identifiers are represented by hashes in publication artifacts.

\section*{Author contributions}

The authors designed and directed the study, interpreted the evidence, and approved the manuscript. Research Forge executed the registered workflow and produced traceable artifacts.

\section*{Conflicts of interest and funding}

No external funding or conflicts are declared in the current project record.

\section*{Use of artificial intelligence}

Codex was used to operate the workflow and assist with manuscript drafting and revision. It was not treated as a human auditor, an independent replication, or a source of scientific authority.

\begin{thebibliography}{99}
\RaggedRight
\footnotesize

\bibitem{Lu2024}
Lu, C., Lu, C., Lange, R. T., Foerster, J., Clune, J., and Ha, D. 2024.
The AI Scientist: Towards Fully Automated Open-Ended Scientific Discovery.
\textit{arXiv:2408.06292}.

\bibitem{Wadden2020}
Wadden, D., Lin, S., Lo, K., Wang, L. L., van Zuylen, M., Cohan, A., and Hajishirzi, H. 2020.
Fact or Fiction: Verifying Scientific Claims.
\textit{Proceedings of EMNLP 2020}, 7534--7550.
DOI: 10.18653/v1/2020.emnlp-main.609.

\bibitem{Liu2024}
Liu, Y., et al. 2024.
Retrieval augmented scientific claim verification.
\textit{JAMIA Open}.
DOI: 10.1093/jamiaopen/ooae021.

\bibitem{Kumar2025}
Kumar, S., Sharma, A., Khincha, S. H., Shroff, G., Singh, S. R., and Mishra, R. 2025.
SciClaimHunt: A Large Dataset for Evidence-based Scientific Claim Verification.
\textit{arXiv:2502.10003}.

\bibitem{SciVer2025}
Wang, C., Shen, Y., Kuang, Z., Cohan, A., and Zhao, Y. 2025.
SciVer: Evaluating Foundation Models for Multimodal Scientific Claim Verification.
\textit{arXiv:2506.15569}.

\bibitem{Asai2025}
Asai, A., He, J., Shao, R., et al. 2026.
Synthesizing scientific literature with retrieval-augmented language models.
\textit{Nature} 650, 857--863.
DOI: 10.1038/s41586-025-10072-4.

\bibitem{Yue2026}
Yue, L., Ouyang, C., Xu, H., et al. 2026.
FactReview: Evidence-Grounded Peer Review with Execution-Based Claim Verification.
\textit{arXiv:2604.04074}.

\bibitem{Ghorbanpour2026}
Ghorbanpour, A., Sadeghian, S., Daghighfarsoodeh, A., et al. 2026.
Peerispect: Claim Verification in Scientific Peer Reviews.
\textit{arXiv:2604.17667}.

\bibitem{Ji2026}
Ji, Y., Kwak, M. G., Zhang, H., Wu, X., Li, C., and Wang, Y. 2026.
MedRAGChecker: Claim-Level Verification for Biomedical Retrieval-Augmented Generation.
\textit{arXiv:2601.06519}.

\bibitem{Zhai2026}
Zhai, Y., Li, R., Wang, L., et al. 2026.
GUIDE: Interpretable GUI Agent Evaluation via Hierarchical Diagnosis.
\textit{arXiv:2604.04399}.

\bibitem{He2020}
He, P., Liu, X., Gao, J., and Chen, W. 2020.
DeBERTa: Decoding-enhanced BERT with Disentangled Attention.
\textit{arXiv:2006.03654}.

\bibitem{Munafo2017}
Munafò, M. R., et al. 2017.
A manifesto for reproducible science.
\textit{Nature Human Behaviour} 1, 0021.
DOI: 10.1038/s41562-016-0021.

\bibitem{Pineau2021}
Pineau, J., et al. 2021.
Improving reproducibility in machine learning research: A report from the NeurIPS 2019 reproducibility program.
\textit{Journal of Machine Learning Research} 22(164), 1--20.

\bibitem{Moreau2013}
Moreau, L., Missier, P., and the W3C Provenance Working Group. 2013.
PROV-DM: The PROV Data Model.
\textit{W3C Recommendation}.

\bibitem{Sculley2015}
Sculley, D., et al. 2015.
Hidden technical debt in machine learning systems.
\textit{Advances in Neural Information Processing Systems} 28.

\end{thebibliography}

\end{document}
""".lstrip()


def main() -> None:
    evidence = _load_evidence()
    _assert_frozen_values(evidence)
    OUTPUT.mkdir(parents=True, exist_ok=True)
    FIGURES.mkdir(parents=True, exist_ok=True)
    transparency = build_evaluation_transparency_register(
        study_id="research-agent-evidence-publication-v1",
        source_claim_envelope_id="publication-analysis-v1",
        evaluation_path=(
            "synthesis/publication_analysis.json"
        ),
        contract_path=(
            "design_revisions/independent_calibration_contract.json"
        ),
        evaluator_policy={
            "type": "DeBERTa NLI",
            "entailment_threshold": 0.55,
            "contradiction_threshold": 0.55,
            "threshold_selection": {
                "source": "allenai/scifact train",
                "selection_count": 501,
                "locked_evaluation_count": 456,
                "grid": [
                    0.50,
                    0.55,
                    0.60,
                    0.65,
                    0.70,
                    0.75,
                    0.80,
                    0.85,
                    0.90,
                    0.95,
                ],
            },
            "numeric_tolerance": 0.0,
            "packet_schema": (
                "task, metric, direction, target, run and artifact bindings"
            ),
        },
        total_records=308,
        eligible_records=232,
        excluded_record_ids=[
            f"novelty-{index:03d}" for index in range(76)
        ],
        exclusion_reasons={"novelty_claim": 76},
        abstention_count=3,
        arm_estimates={
            "baseline_task_native": 0.3370035177485372,
            "treatment_task_native": 0.3370035177485372,
        },
        repair_present=True,
        prospective_successor_present=False,
        available_artifact_kinds={
            "packet_schema",
            "evaluator_manifest",
            "regression_fixtures",
        },
    )
    backfill = assess_stage4_evidence_sufficiency(transparency)
    write_json_atomic(
        OUTPUT / "evaluation_transparency_register.json",
        transparency,
    )
    write_json_atomic(
        OUTPUT / "stage4_evidence_backfill_request.json",
        backfill,
    )
    _copy_concept_figure()
    _create_results_figure(evidence)
    write_text_atomic(OUTPUT / "manuscript.tex", MANUSCRIPT)
    print(OUTPUT / "manuscript.tex")


if __name__ == "__main__":
    main()
