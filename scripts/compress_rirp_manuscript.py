from __future__ import annotations

import hashlib
import json
import re
import time
from collections import Counter
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
SUBMISSION = ROOT / "stage1_runs/research-agent-evidence-publication-v1/synthesis/submission_rirp"
SOURCE = SUBMISSION / "manuscript_rirp.md"
OUTPUT = SUBMISSION / "manuscript_rirp_compact_candidate.md"
AUDIT = SUBMISSION / "manuscript_rirp_compact_candidate.audit.json"
CHECKPOINT_DIR = SUBMISSION / "compact_section_checkpoints"

TARGETS = {
    "Background": 1100,
    "Methods": 1350,
    "Results": 1100,
    "Discussion": 650,
    "Limitations": 300,
    "Conclusions": 100,
}

BOUNDARIES = (
    "The preregistered human-validity gate failed and the historical automated treatment contrast remains non-confirmatory.",
    "AAAAA is a post-unblinding context-restored review by the project owner, not blinded validation.",
    "The 5/5 replay uses the same diagnosed cases and is regression evidence only, not generalization.",
    "Selective rollback is not established as fully automated.",
    "A fresh prospectively frozen successor experiment and independent audit are required.",
)


def sha256_text(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def read_env() -> dict[str, str]:
    result: dict[str, str] = {}
    for line in (ROOT / ".env.local").read_text(encoding="utf-8").splitlines():
        if line.strip() and not line.lstrip().startswith("#") and "=" in line:
            key, value = line.split("=", 1)
            result[key.strip()] = value.strip().strip('"').strip("'")
    return result


def split_document(text: str) -> tuple[str, dict[str, str], str]:
    # Declarations and References are submission metadata, not part of the
    # journal's encouraged main-text word count.  Use Declarations as the
    # terminal delimiter so the Conclusions compressor cannot rewrite or drop
    # the submission tail after the formatter normalizes its order.
    matches = list(re.finditer(
        r"(?m)^## (Abstract|Background|Methods|Results|Discussion|Limitations|Conclusions|List of abbreviations|Declarations)\s*$",
        text,
    ))
    terminal = next(
        match for match in matches
        if match.group(1) in {"List of abbreviations", "Declarations"}
    )
    sections: dict[str, str] = {}
    for index, match in enumerate(matches):
        name = match.group(1)
        if match.start() >= terminal.start():
            continue
        end = matches[index + 1].start() if index + 1 < len(matches) else len(text)
        sections[name] = text[match.start():end].rstrip() + "\n"
    prefix = text[: matches[0].start()]
    suffix = text[terminal.start():]
    return prefix, sections, suffix


def main_text(text: str) -> str:
    """Return the journal main text, excluding Declarations and References."""
    start = text.index("## Abstract")
    terminal_positions = [
        text.index(heading, start)
        for heading in ("## Declarations", "## References")
        if heading in text[start:]
    ]
    if not terminal_positions:
        raise ValueError("main-text terminal heading not found")
    return text[start:min(terminal_positions)]


def protect_layout(section: str) -> tuple[str, dict[str, str]]:
    protected: dict[str, str] = {}

    def hold(match: re.Match[str], kind: str) -> str:
        token = f"@@PROTECTED_{kind}_{len(protected):03d}@@"
        protected[token] = match.group(0)
        return token

    section = re.sub(
        r"(?m)^!\[[^\n]+\]\([^\n]+\)\n\n\*Figure [^\n]+\*\n?",
        lambda match: hold(match, "FIGURE"),
        section,
    )
    section = re.sub(
        r"(?m)(?:^\|[^\n]*\|\n){2,}",
        lambda match: hold(match, "TABLE"),
        section,
    )
    return section, protected


def restore_layout(section: str, protected: dict[str, str]) -> str:
    for token, value in protected.items():
        if section.count(token) != 1:
            raise ValueError(f"protected token {token} count is {section.count(token)}")
        section = section.replace(token, value.rstrip())
    return section.rstrip() + "\n"


def number_tokens(text: str) -> Counter[str]:
    raw = re.findall(r"(?<![A-Za-z])[-+]?\d[\dA-Za-z./:%_\-–]*", text)
    return Counter(token.rstrip(".,:;") for token in raw)


def citation_tokens(text: str) -> Counter[str]:
    return Counter(re.findall(r"\[(?:\d+(?:–\d+)?)(?:,\d+(?:–\d+)?)*\]", text))


def headings(text: str) -> list[str]:
    return re.findall(r"(?m)^#{2,4} .+$", text)


def word_count(text: str) -> int:
    without_layout, _ = protect_layout(text)
    return len(re.findall(r"\b[A-Za-z0-9][A-Za-z0-9'–.-]*\b", without_layout))


def validate_section(original: str, candidate: str, target: int) -> list[str]:
    issues: list[str] = []
    if headings(original) != headings(candidate):
        issues.append("heading sequence changed")
    if number_tokens(original) != number_tokens(candidate):
        missing = number_tokens(original) - number_tokens(candidate)
        added = number_tokens(candidate) - number_tokens(original)
        issues.append(f"numeric tokens changed; missing={dict(missing)} added={dict(added)}")
    if citation_tokens(original) != citation_tokens(candidate):
        issues.append("citation token multiset changed")
    words = word_count(candidate)
    if words > int(target * 1.12):
        issues.append(f"section remains too long: {words} > {int(target * 1.12)}")
    if "<!--" in candidate or "```" in candidate:
        issues.append("unexpected marker or code fence")
    return issues


def extract_output(data: dict) -> str:
    direct = data.get("output_text")
    if direct:
        return str(direct)
    chunks: list[str] = []
    for item in data.get("output", []):
        for content in item.get("content", []):
            if content.get("type") in {"output_text", "text"}:
                chunks.append(str(content.get("text", "")))
    return "".join(chunks)


def call_section(
    *, url: str, key: str, name: str, protected_text: str, target: int, previous_issues: list[str] | None = None
) -> tuple[str, dict]:
    import requests

    feedback = ""
    if previous_issues:
        feedback = "\nThe previous candidate failed these deterministic checks:\n- " + "\n- ".join(previous_issues)
    prompt = f"""Rewrite only the supplied {name} section to at most {target} words, excluding protected layout placeholders.

This is a conservative academic-humanizer compression pass, not a new analysis. Preserve the exact Markdown heading sequence and every @@PROTECTED_*@@ placeholder exactly once. Preserve every numeric token and citation token exactly, including repeats. Do not add, delete, round, normalize, or reformat any number, identifier, interval, ratio, threshold, seed, task, token count, cost, time, or citation. Preserve all empirical and methodological content, but remove repeated framing and repeated boundary explanations. Keep neutral Research Integrity and Peer Review prose. Remove promotional language, filler, clause stacking, and em dashes. Do not add claims, citations, lists, headings, comments, or code fences.

The complete manuscript must still state these boundaries somewhere; retain any of them already present in this section:
- {BOUNDARIES[0]}
- {BOUNDARIES[1]}
- {BOUNDARIES[2]}
- {BOUNDARIES[3]}
- {BOUNDARIES[4]}
{feedback}

Return strict JSON with one key, section_markdown.

SECTION:\n{protected_text}"""
    payload = {
        "model": "gpt-5.6-sol",
        "reasoning": {"effort": "medium"},
        "input": [
            {"role": "system", "content": [{"type": "input_text", "text": "You are a conservative academic editor. Compression must not change evidence."}]},
            {"role": "user", "content": [{"type": "input_text", "text": prompt}]},
        ],
        "text": {"format": {"type": "json_object"}},
    }
    response = requests.post(
        url,
        headers={"Authorization": f"Bearer {key}", "Content-Type": "application/json"},
        json=payload,
        timeout=300,
    )
    response.raise_for_status()
    data = response.json()
    value = json.loads(extract_output(data))
    return str(value["section_markdown"]).strip() + "\n", data.get("usage", {})


def main() -> None:
    env = read_env()
    base = env["OPENAI_BASE_URL"].rstrip("/")
    url = base + ("" if base.endswith("/v1") else "/v1") + "/responses"
    source = SOURCE.read_text(encoding="utf-8")
    prefix, sections, suffix = split_document(source)
    rewritten: dict[str, str] = {}
    records: list[dict] = []
    aggregate_usage = Counter()

    for name, target in TARGETS.items():
        original = sections[name]
        protected_text, protected = protect_layout(original)
        checkpoint_text = CHECKPOINT_DIR / f"{name}.md"
        checkpoint_meta = CHECKPOINT_DIR / f"{name}.json"
        if checkpoint_text.is_file() and checkpoint_meta.is_file():
            meta = json.loads(checkpoint_meta.read_text(encoding="utf-8"))
            candidate = checkpoint_text.read_text(encoding="utf-8")
            checkpoint_issues = validate_section(original, candidate, target)
            if meta.get("original_sha256") == sha256_text(original) and not checkpoint_issues:
                rewritten[name] = candidate
                records.append(meta)
                print(f"{name}: reused validated checkpoint {word_count(original)} -> {word_count(candidate)} words", flush=True)
                continue
        issues: list[str] | None = None
        for attempt in range(1, 4):
            print(f"{name}: attempt {attempt}", flush=True)
            candidate_protected, usage = call_section(
                url=url,
                key=env["OPENAI_API_KEY"],
                name=name,
                protected_text=protected_text,
                target=target,
                previous_issues=issues,
            )
            try:
                candidate = restore_layout(candidate_protected, protected)
                issues = validate_section(original, candidate, target)
            except ValueError as exc:
                candidate = ""
                issues = [str(exc)]
            for key_name in ("input_tokens", "output_tokens", "total_tokens"):
                aggregate_usage[key_name] += int(usage.get(key_name) or 0)
            if not issues:
                rewritten[name] = candidate
                records.append({
                    "section": name,
                    "attempts": attempt,
                    "target_words": target,
                    "original_words": word_count(original),
                    "candidate_words": word_count(candidate),
                    "original_sha256": sha256_text(original),
                    "candidate_sha256": sha256_text(candidate),
                    "numeric_tokens_preserved": True,
                    "citation_tokens_preserved": True,
                    "headings_preserved": True,
                })
                CHECKPOINT_DIR.mkdir(parents=True, exist_ok=True)
                checkpoint_text.write_text(candidate, encoding="utf-8", newline="\n")
                checkpoint_meta.write_text(json.dumps(records[-1], ensure_ascii=False, indent=2) + "\n", encoding="utf-8", newline="\n")
                print(f"{name}: accepted {word_count(original)} -> {word_count(candidate)} words", flush=True)
                break
            print(f"{name}: rejected: {'; '.join(issues)}", flush=True)
            time.sleep(1)
        else:
            raise RuntimeError(f"{name} failed validation after three attempts: {issues}")

    compact = prefix + sections["Abstract"]
    for name in TARGETS:
        compact += rewritten[name]
    compact += suffix
    main_segment = main_text(compact)
    total_words = word_count(main_segment)
    if not 4500 <= total_words <= 5000:
        raise RuntimeError(f"compact manuscript word count outside 4500-5000: {total_words}")
    if number_tokens(source) != number_tokens(compact):
        raise RuntimeError("document-level numeric token multiset changed")
    if citation_tokens(source) != citation_tokens(compact):
        raise RuntimeError("document-level citation token multiset changed")
    if headings(source) != headings(compact):
        raise RuntimeError("document-level heading sequence changed")

    OUTPUT.write_text(compact, encoding="utf-8", newline="\n")
    audit = {
        "schema_version": 1,
        "status": "DETERMINISTIC_COMPRESSION_CHECKS_PASSED_SEMANTIC_REVIEW_PENDING",
        "model": "gpt-5.6-sol",
        "reasoning_effort": "medium",
        "source": SOURCE.name,
        "source_sha256": sha256_text(source),
        "candidate": OUTPUT.name,
        "candidate_sha256": sha256_text(compact),
        "main_words_before": word_count(main_text(source)),
        "main_words_after": total_words,
        "numeric_token_multiset_preserved": True,
        "citation_token_multiset_preserved": True,
        "heading_sequence_preserved": True,
        "sections": records,
        "usage": dict(aggregate_usage),
    }
    AUDIT.write_text(json.dumps(audit, ensure_ascii=False, indent=2) + "\n", encoding="utf-8", newline="\n")
    print(json.dumps(audit, ensure_ascii=True, indent=2), flush=True)


if __name__ == "__main__":
    main()
