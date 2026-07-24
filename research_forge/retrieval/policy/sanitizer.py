from __future__ import annotations

import hashlib
import re
from dataclasses import dataclass

from ..domain.models import RedactionFinding, RedactionReport, retrieval_id


_RULES: tuple[tuple[str, re.Pattern[str], str], ...] = (
    (
        "private_key",
        re.compile(
            r"-----BEGIN [A-Z ]*PRIVATE KEY-----.*?-----END [A-Z ]*PRIVATE KEY-----",
            re.S,
        ),
        "[REDACTED_PRIVATE_KEY]",
    ),
    (
        "api_key_or_token",
        re.compile(
            r"(?i)\b(?:api[_-]?key|access[_-]?token|bearer|password|passwd)\b\s*[:=]\s*[\"']?[A-Za-z0-9_./+\-=]{6,}"
        ),
        "[REDACTED_SECRET]",
    ),
    (
        "openai_style_key",
        re.compile(r"\bsk-(?:proj-)?[A-Za-z0-9_-]{10,}\b"),
        "[REDACTED_SECRET]",
    ),
    (
        "windows_path",
        re.compile(r"(?i)\b[A-Z]:\\(?:[^\\/:*?\"<>|\s]+\\)*[^\\/:*?\"<>|\s]*"),
        "[LOCAL_PATH]",
    ),
    (
        "unix_home_path",
        re.compile(r"(?<!\w)/(?:home|Users|root)/[^\s,;]+"),
        "[LOCAL_PATH]",
    ),
    (
        "email",
        re.compile(r"\b[A-Z0-9._%+-]+@[A-Z0-9.-]+\.[A-Z]{2,}\b", re.I),
        "[REDACTED_EMAIL]",
    ),
    (
        "repository_credential",
        re.compile(r"https?://[^/\s:@]+:[^@\s/]+@"),
        "https://[REDACTED_CREDENTIAL]@",
    ),
)


@dataclass(frozen=True)
class SanitizationResult:
    queries: list[str]
    report: RedactionReport


class QuerySanitizer:
    def sanitize(
        self,
        queries: list[str],
        *,
        internal_identifiers: list[str] | None = None,
    ) -> SanitizationResult:
        raw_digest = hashlib.sha256("\x1e".join(queries).encode("utf-8")).hexdigest()
        findings: list[RedactionFinding] = []
        cleaned: list[str] = []
        identifiers = sorted(
            {
                item.strip()
                for item in (internal_identifiers or [])
                if len(item.strip()) >= 3
            },
            key=len,
            reverse=True,
        )
        for index, raw in enumerate(queries):
            value = raw
            for category, pattern, replacement in _RULES:
                value, count = pattern.subn(replacement, value)
                if count:
                    findings.append(
                        RedactionFinding(
                            category=category,
                            query_index=index,
                            replacement=replacement,
                            count=count,
                        )
                    )
            for identifier in identifiers:
                value, count = re.subn(
                    re.escape(identifier),
                    "[INTERNAL_IDENTIFIER]",
                    value,
                    flags=re.I,
                )
                if count:
                    findings.append(
                        RedactionFinding(
                            category="internal_identifier",
                            query_index=index,
                            replacement="[INTERNAL_IDENTIFIER]",
                            count=count,
                        )
                    )
            value = re.sub(r"\s+", " ", value).strip()
            if value:
                cleaned.append(value[:500])
        report = RedactionReport(
            report_id=retrieval_id("redaction", raw_digest, *cleaned),
            raw_query_digest=raw_digest,
            findings=findings,
        )
        return SanitizationResult(cleaned, report)
