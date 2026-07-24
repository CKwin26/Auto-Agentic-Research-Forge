"""GitHub official REST API provider for research-code discovery."""

from __future__ import annotations

import re
import urllib.parse
import urllib.request
from collections.abc import Callable
from typing import Any

from ...claim_discovery import TrendSignal
from ..domain.models import (
    ProviderErrorClass,
    QueryPlan,
    ResourceType,
    retrieval_id,
)
from .base import (
    ProviderAdapter,
    ProviderContentResult,
    ProviderFailure,
    ProviderSearchResult,
    binary_transport,
)


_FULL_SHA = re.compile(r"^[0-9a-f]{40}$", re.I)


class GitHubResearchAdapter(ProviderAdapter):
    provider_id = "github"
    domains = {"api.github.com"}
    http_method = "GET"

    def __init__(
        self,
        *args,
        content_transport: Callable[..., tuple[bytes, str, str, dict[str, str]]]
        | None = None,
        **kwargs,
    ) -> None:
        super().__init__(*args, **kwargs)
        self.content_transport = content_transport or binary_transport

    def health_check(self) -> dict[str, str]:
        return {
            "provider": self.provider_id,
            "status": "degraded",
            "credential_mode": (
                "server_managed"
                if self.credentials.get("GITHUB_TOKEN")
                else "none_required"
            ),
            "reason": "adapter is configured; live API health has not been verified",
        }

    def _request(
        self, path: str, params: dict[str, Any] | None = None
    ) -> dict[str, Any]:
        url = "https://api.github.com" + path
        if params:
            url += "?" + urllib.parse.urlencode(params)
        headers = {
            "Accept": "application/vnd.github+json",
            "User-Agent": "ResearchForge/0.1",
            "X-GitHub-Api-Version": "2022-11-28",
        }
        token = self.credentials.get("GITHUB_TOKEN")
        if token:
            headers["Authorization"] = f"Bearer {token}"
        return self.transport(urllib.request.Request(url, headers=headers))

    def search(self, plan: QueryPlan) -> ProviderSearchResult:
        if ResourceType.CODE_REPOSITORY not in plan.resource_types:
            raise ProviderFailure(
                "GitHub search currently requires code_repository resource type",
                ProviderErrorClass.POLICY_DENIED,
            )
        raw_payloads: list[dict[str, Any]] = []
        signals: list[TrendSignal] = []
        remaining = max(1, min(plan.budget.max_results or 10, 20))
        for query in plan.sanitized_queries[: plan.budget.max_queries or 1]:
            payload = self._request(
                "/search/repositories",
                {"q": query, "per_page": min(remaining, 20)},
            )
            raw_payloads.append(payload)
            for row in payload.get("items") or []:
                if not isinstance(row, dict) or remaining <= 0:
                    continue
                full_name = str(row.get("full_name") or "").strip()
                default_branch = str(row.get("default_branch") or "HEAD")
                if not full_name:
                    continue
                commit_payload = self._request(
                    f"/repos/{urllib.parse.quote(full_name, safe='/')}/commits/"
                    f"{urllib.parse.quote(default_branch, safe='')}"
                )
                raw_payloads.append(commit_payload)
                sha = str(commit_payload.get("sha") or "")
                if len(sha) != 40:
                    raise ProviderFailure(
                        "GitHub repository could not be pinned to a commit SHA",
                        ProviderErrorClass.MALFORMED_RESPONSE,
                    )
                topics = [
                    str(item)
                    for item in row.get("topics") or []
                    if isinstance(item, str)
                ]
                signals.append(
                    TrendSignal(
                        signal_id=retrieval_id(
                            "github-signal", full_name.casefold(), sha
                        ),
                        provider=self.provider_id,
                        signal_class="adoption_signal",
                        query=query,
                        title=full_name,
                        summary=str(row.get("description") or "")[:1200],
                        url=str(row.get("html_url") or ""),
                        published_at=str(
                            row.get("pushed_at") or row.get("updated_at") or ""
                        ),
                        source_name=str((row.get("owner") or {}).get("login") or ""),
                        engagement={
                            "stars": int(row.get("stargazers_count") or 0),
                            "forks": int(row.get("forks_count") or 0),
                            "watchers": int(row.get("watchers_count") or 0),
                        },
                        terms=topics,
                        trend_score=0.0,
                        scientific_density=0.0,
                        metadata={
                            "resource_type": "code_repository",
                            "repository": full_name,
                            "commit": sha,
                            "default_branch": default_branch,
                            "archived": bool(row.get("archived")),
                            "license": (
                                (row.get("license") or {}).get("spdx_id")
                                if isinstance(row.get("license"), dict)
                                else None
                            ),
                            "adoption_signal": {
                                "stars": int(row.get("stargazers_count") or 0),
                                "forks": int(row.get("forks_count") or 0),
                                "watchers": int(row.get("watchers_count") or 0),
                            },
                            "scientific_quality_score": None,
                        },
                    )
                )
                remaining -= 1
        return ProviderSearchResult(
            provider=self.provider_id,
            raw_payloads=raw_payloads,
            signals=signals,
            domains=sorted(self.domains),
            http_method=self.http_method,
        )

    def search_repositories(self, plan: QueryPlan) -> ProviderSearchResult:
        """Search and pin public repositories under the supplied QueryPlan."""
        return self.search(plan)

    def get_repository(self, full_name: str) -> dict[str, Any]:
        return self._request(f"/repos/{urllib.parse.quote(full_name, safe='/')}")

    def get_commit(self, full_name: str, revision: str) -> dict[str, Any]:
        return self._request(
            f"/repos/{urllib.parse.quote(full_name, safe='/')}/commits/"
            f"{urllib.parse.quote(revision, safe='')}"
        )

    def resolve_revision(self, full_name: str, revision: str) -> str:
        sha = str(self.get_commit(full_name, revision).get("sha") or "")
        if len(sha) != 40:
            raise ProviderFailure(
                "GitHub revision did not resolve to a full commit SHA",
                ProviderErrorClass.MALFORMED_RESPONSE,
            )
        return sha

    def get_readme(self, full_name: str, ref: str) -> dict[str, Any]:
        return self._request(
            f"/repos/{urllib.parse.quote(full_name, safe='/')}/readme",
            {"ref": ref},
        )

    def get_license(self, full_name: str, ref: str) -> dict[str, Any]:
        return self._request(
            f"/repos/{urllib.parse.quote(full_name, safe='/')}/license",
            {"ref": ref},
        )

    def get_file(self, full_name: str, path: str, ref: str) -> dict[str, Any]:
        return self._request(
            f"/repos/{urllib.parse.quote(full_name, safe='/')}/contents/"
            f"{urllib.parse.quote(path, safe='/')}",
            {"ref": ref},
        )

    def get_citation_file(self, full_name: str, ref: str) -> dict[str, Any]:
        return self.get_file(full_name, "CITATION.cff", ref)

    def get_tags(self, full_name: str) -> dict[str, Any]:
        return {
            "items": self._request_list(
                f"/repos/{urllib.parse.quote(full_name, safe='/')}/tags"
            )
        }

    def get_releases(self, full_name: str) -> dict[str, Any]:
        return {
            "items": self._request_list(
                f"/repos/{urllib.parse.quote(full_name, safe='/')}/releases"
            )
        }

    def search_code(self, query: str, *, per_page: int = 20) -> dict[str, Any]:
        return self._request(
            "/search/code", {"q": query, "per_page": min(max(per_page, 1), 100)}
        )

    def search_issues(self, query: str, *, per_page: int = 20) -> dict[str, Any]:
        return self._request(
            "/search/issues", {"q": query, "per_page": min(max(per_page, 1), 100)}
        )

    def search_releases(
        self,
        query: str,
        *,
        per_page: int = 20,
    ) -> dict[str, Any]:
        """Search repositories, then enumerate their releases via official API.

        GitHub has no global release-search endpoint. This bounded two-step
        implementation makes that limitation explicit while still returning
        release records tied to the repositories that matched the query.
        """
        limit = min(max(per_page, 1), 100)
        repositories = self._request(
            "/search/repositories",
            {"q": query, "per_page": limit},
        )
        releases: list[dict[str, Any]] = []
        for repository in repositories.get("items") or []:
            if not isinstance(repository, dict):
                continue
            full_name = str(repository.get("full_name") or "").strip()
            if not full_name:
                continue
            for release in self._request_list(
                f"/repos/{urllib.parse.quote(full_name, safe='/')}/releases"
            ):
                releases.append({"repository": full_name, **release})
                if len(releases) >= limit:
                    break
            if len(releases) >= limit:
                break
        return {
            "query": query,
            "repositories_examined": len(repositories.get("items") or []),
            "items": releases,
        }

    def get_release(self, full_name: str, tag: str) -> dict[str, Any]:
        return self._request(
            f"/repos/{urllib.parse.quote(full_name, safe='/')}/releases/tags/"
            f"{urllib.parse.quote(tag, safe='')}"
        )

    def get_reproducibility_files(
        self, full_name: str, ref: str
    ) -> dict[str, dict[str, Any]]:
        candidates = (
            "README.md",
            "CITATION.cff",
            "Dockerfile",
            "environment.yml",
            "requirements.txt",
            "pyproject.toml",
        )
        found: dict[str, dict[str, Any]] = {}
        for path in candidates:
            try:
                found[path] = self.get_file(full_name, path, ref)
            except ProviderFailure as exc:
                if exc.classification is not ProviderErrorClass.RESOURCE_NOT_FOUND:
                    raise
        return found

    def get_workflow_files(self, full_name: str, ref: str) -> dict[str, Any]:
        return self.get_file(full_name, ".github/workflows", ref)

    def get_dependency_files(
        self, full_name: str, ref: str
    ) -> dict[str, dict[str, Any]]:
        candidates = (
            "pyproject.toml",
            "requirements.txt",
            "package.json",
            "Cargo.toml",
            "go.mod",
        )
        found: dict[str, dict[str, Any]] = {}
        for path in candidates:
            try:
                found[path] = self.get_file(full_name, path, ref)
            except ProviderFailure as exc:
                if exc.classification is not ProviderErrorClass.RESOURCE_NOT_FOUND:
                    raise
        return found

    def get_file_tree(self, full_name: str, commit_sha: str) -> dict[str, Any]:
        return self._request(
            f"/repos/{urllib.parse.quote(full_name, safe='/')}/git/trees/"
            f"{urllib.parse.quote(commit_sha, safe='')}",
            {"recursive": "1"},
        )

    def download_approved_archive(
        self,
        *,
        full_name: str,
        commit_sha: str,
        max_bytes: int,
        authorization_approved: bool,
    ) -> ProviderContentResult:
        """Download, but never execute, one explicitly approved pinned archive."""
        if not authorization_approved:
            raise ProviderFailure(
                "repository archive download requires explicit authorization",
                ProviderErrorClass.POLICY_DENIED,
            )
        if not _FULL_SHA.fullmatch(commit_sha):
            raise ProviderFailure(
                "repository archive must be pinned to a full commit SHA",
                ProviderErrorClass.POLICY_DENIED,
            )
        encoded_name = urllib.parse.quote(full_name, safe="/")
        source_url = (
            f"https://api.github.com/repos/{encoded_name}/zipball/{commit_sha}"
        )
        headers = {
            "Accept": "application/vnd.github+json",
            "User-Agent": "ResearchForge/0.1",
            "X-GitHub-Api-Version": "2022-11-28",
        }
        token = self.credentials.get("GITHUB_TOKEN")
        if token:
            headers["Authorization"] = f"Bearer {token}"
        content, final_url, mime_type, response_headers = self.content_transport(
            urllib.request.Request(source_url, headers=headers),
            max_bytes=max_bytes,
            url_validator=_validate_github_archive_url,
        )
        return ProviderContentResult(
            provider=self.provider_id,
            source_url=source_url,
            final_url=final_url,
            content=content,
            mime_type=mime_type or "application/zip",
            response_headers=response_headers,
        )

    def _request_list(self, path: str) -> list[dict[str, Any]]:
        headers = {
            "Accept": "application/vnd.github+json",
            "User-Agent": "ResearchForge/0.1",
            "X-GitHub-Api-Version": "2022-11-28",
        }
        token = self.credentials.get("GITHUB_TOKEN")
        if token:
            headers["Authorization"] = f"Bearer {token}"
        payload = self.transport(
            urllib.request.Request(
                "https://api.github.com" + path,
                headers=headers,
            )
        )
        if isinstance(payload, list):
            return [item for item in payload if isinstance(item, dict)]
        if "items" in payload:
            return [
                item for item in payload.get("items") or [] if isinstance(item, dict)
            ]
        raise ProviderFailure(
            "GitHub list response is malformed",
            ProviderErrorClass.MALFORMED_RESPONSE,
        )


def _validate_github_archive_url(url: str) -> None:
    parsed = urllib.parse.urlparse(url)
    if (
        parsed.scheme.casefold() != "https"
        or (parsed.hostname or "").casefold()
        not in {"api.github.com", "codeload.github.com"}
        or parsed.username
        or parsed.password
    ):
        raise ProviderFailure(
            "GitHub archive URL is outside the approved official hosts",
            ProviderErrorClass.POLICY_DENIED,
        )
