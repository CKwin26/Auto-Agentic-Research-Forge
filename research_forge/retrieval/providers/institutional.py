"""Local institutional-access handoff.

The broker never receives or stores institutional credentials.  It can only
create a user-bound browser handoff and later register a user-authorized local
document as an institutional resource.
"""

from __future__ import annotations

import hashlib
import tempfile
from pathlib import Path
from typing import Any

from ..domain.models import ProviderErrorClass, QueryPlan, retrieval_id
from .base import ProviderAdapter, ProviderFailure, ProviderSearchResult


class InstitutionalAccessAdapter(ProviderAdapter):
    provider_id = "institutional_access"
    domains = {"institution-session.local"}
    http_method = "USER_HANDOFF"

    def __init__(self, *, session_root: str | Path | None = None) -> None:
        super().__init__()
        self.session_root = Path(session_root) if session_root else None

    def health_check(self) -> dict[str, str]:
        from ..institution import LocalInstitutionBrowserController

        browser_available = LocalInstitutionBrowserController(
            self.session_root
            or Path(tempfile.gettempdir()) / "research-forge-institution"
        ).available()
        return {
            "provider": self.provider_id,
            "status": "degraded" if browser_available else "unavailable",
            "credential_mode": "institution_session",
            "reason": (
                "local browser available; a user-authenticated, hash-bound "
                "single-document demonstration is still required"
                if browser_available
                else "no supported local Edge or Chrome browser was found"
            ),
        }

    def search(self, plan: QueryPlan) -> ProviderSearchResult:
        raise ProviderFailure(
            "institutional access requires an explicit local user handoff",
            ProviderErrorClass.POLICY_DENIED,
        )

    def create_handoff(
        self, *, user_id: str, study_id: str, target_url: str
    ) -> dict[str, Any]:
        if not target_url.startswith("https://"):
            raise ValueError("institutional handoff target must use HTTPS")
        return {
            "handoff_id": retrieval_id(
                "institution-handoff", user_id, study_id, target_url
            ),
            "user_id": user_id,
            "study_id": study_id,
            "target_url": target_url,
            "credential_capture": False,
            "mfa_bypass": False,
            "status": "waiting_for_user",
        }

    def register_authorized_document(
        self,
        *,
        user_id: str,
        study_id: str,
        file_path: str | Path,
        authorization_approved: bool,
    ) -> dict[str, Any]:
        if not authorization_approved:
            raise ProviderFailure(
                "institutional document registration requires user authorization",
                ProviderErrorClass.POLICY_DENIED,
            )
        path = Path(file_path).resolve()
        if not path.is_file():
            raise FileNotFoundError(path)
        digest = hashlib.sha256(path.read_bytes()).hexdigest()
        return {
            "resource_id": retrieval_id(
                "institution-document", user_id, study_id, digest
            ),
            "study_id": study_id,
            "owner_user_id": user_id,
            "content_hash": digest,
            "access_mode": "institutional",
            "persistent_storage_allowed": True,
            "cross_user_cache_allowed": False,
            "redistribution_allowed": False,
            "training_allowed": False,
        }
