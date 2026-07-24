"""Local, user-bound institutional session metadata.

Authentication happens in the institution's own browser page.  This module
never accepts password, cookie, MFA, or recovery-code fields.
"""

from __future__ import annotations

import hashlib
from datetime import datetime, timedelta, timezone
from enum import StrEnum
from pathlib import Path
from typing import Any

from pydantic import Field, model_validator

from ...models import StrictModel, utc_now
from ...storage import read_json, write_json_atomic
from ..domain.external_models import AccessMode
from ..domain.models import (
    ExternalResource,
    MetadataVerificationStatus,
    ResourceSnapshot,
    ResourceType,
    ResourceUseBinding,
    RetrievalPhase,
    retrieval_id,
)
from ..domain.repository import RetrievalRepository
from ..policy.content_rights import ContentRightsPolicy
from .browser_controller import BrowserLaunch, LocalInstitutionBrowserController


_FORBIDDEN_KEYS = {
    "password",
    "passcode",
    "cookie",
    "cookies",
    "mfa",
    "duo",
    "recovery_code",
    "secret",
    "token",
}


class InstitutionSessionStatus(StrEnum):
    WAITING_FOR_USER = "waiting_for_user"
    ACTIVE = "active"
    EXPIRED = "expired"
    REVOKED = "revoked"
    BLOCKED = "blocked"


class InstitutionSession(StrictModel):
    session_id: str
    owner_user_id: str
    project_id: str
    study_id: str
    institution_id: str
    target_url: str
    status: InstitutionSessionStatus = InstitutionSessionStatus.WAITING_FOR_USER
    browser_profile_id: str
    browser_process_id: int | None = None
    browser_name: str | None = None
    browser_opened_at: str | None = None
    session_storage_security: str = "browser_native_os_encryption"
    authentication_confirmed_by_user: bool = False
    credential_capture: bool = False
    mfa_bypass: bool = False
    created_at: str = Field(default_factory=utc_now)
    authenticated_at: str | None = None
    expires_at: str | None = None
    revoked_at: str | None = None

    @model_validator(mode="after")
    def security_invariants(self) -> "InstitutionSession":
        if self.credential_capture or self.mfa_bypass:
            raise ValueError("credential capture and MFA bypass are prohibited")
        if not self.target_url.startswith("https://"):
            raise ValueError("institution target URL must use HTTPS")
        return self


class InstitutionSessionBroker:
    def __init__(
        self,
        workflow_root: str | Path,
        *,
        browser_controller: LocalInstitutionBrowserController | None = None,
    ) -> None:
        self.workflow_root = Path(workflow_root).resolve()
        self.retrieval = RetrievalRepository(self.workflow_root)
        self.root = self.workflow_root / "retrieval" / "institution_sessions"
        self.root.mkdir(parents=True, exist_ok=True)
        self.browser_controller = (
            browser_controller or LocalInstitutionBrowserController(self.root)
        )

    def _path(self, session_id: str) -> Path:
        return self.root / f"{session_id}.json"

    def create(
        self,
        *,
        owner_user_id: str,
        project_id: str,
        study_id: str,
        institution_id: str,
        target_url: str,
        extra: dict[str, Any] | None = None,
    ) -> InstitutionSession:
        forbidden = _find_forbidden_keys(extra or {})
        if forbidden:
            raise ValueError(
                "institution session payload contains prohibited credential fields"
            )
        session_id = retrieval_id(
            "institution-session",
            owner_user_id,
            project_id,
            study_id,
            institution_id,
            target_url,
        )
        session = InstitutionSession(
            session_id=session_id,
            owner_user_id=owner_user_id,
            project_id=project_id,
            study_id=study_id,
            institution_id=institution_id,
            target_url=target_url,
            browser_profile_id=retrieval_id(
                "browser-profile", owner_user_id, session_id
            ),
        )
        path = self._path(session_id)
        if path.is_file():
            return InstitutionSession.model_validate(read_json(path))
        write_json_atomic(path, session)
        return session

    def load(self, session_id: str, *, owner_user_id: str) -> InstitutionSession:
        session = InstitutionSession.model_validate(read_json(self._path(session_id)))
        if session.owner_user_id != owner_user_id:
            raise PermissionError("institution session belongs to another user")
        if (
            session.status is InstitutionSessionStatus.ACTIVE
            and session.expires_at
            and _parse_time(session.expires_at) <= datetime.now(timezone.utc)
        ):
            session = session.model_copy(
                update={"status": InstitutionSessionStatus.EXPIRED}
            )
            write_json_atomic(self._path(session_id), session)
        return session

    def list_for_study(self, study_id: str) -> list[InstitutionSession]:
        sessions = [
            InstitutionSession.model_validate(read_json(path))
            for path in sorted(self.root.glob("institution-session-*.json"))
        ]
        refreshed = []
        for session in sessions:
            if session.study_id != study_id:
                continue
            refreshed.append(
                self.load(
                    session.session_id,
                    owner_user_id=session.owner_user_id,
                )
            )
        return refreshed

    def open_browser(
        self, session_id: str, *, owner_user_id: str
    ) -> tuple[InstitutionSession, BrowserLaunch]:
        session = self.load(session_id, owner_user_id=owner_user_id)
        if session.status is InstitutionSessionStatus.REVOKED:
            raise ValueError("revoked institution session cannot be opened")
        launch = self.browser_controller.launch(
            browser_profile_id=session.browser_profile_id,
            target_url=session.target_url,
        )
        updated = session.model_copy(
            update={
                "status": InstitutionSessionStatus.WAITING_FOR_USER,
                "browser_process_id": launch.process_id,
                "browser_name": launch.browser,
                "browser_opened_at": utc_now(),
                "authentication_confirmed_by_user": False,
            }
        )
        write_json_atomic(self._path(session_id), updated)
        return updated, launch

    def confirm_authenticated(
        self,
        session_id: str,
        *,
        owner_user_id: str,
        user_confirmation: bool,
        duration_minutes: int = 60,
    ) -> InstitutionSession:
        if not user_confirmation:
            raise ValueError("authentication requires explicit user confirmation")
        if not 5 <= duration_minutes <= 480:
            raise ValueError("institution session duration must be 5-480 minutes")
        session = self.load(session_id, owner_user_id=owner_user_id)
        if session.status is InstitutionSessionStatus.REVOKED:
            raise ValueError("revoked institution session cannot be authenticated")
        now = datetime.now(timezone.utc)
        updated = session.model_copy(
            update={
                "status": InstitutionSessionStatus.ACTIVE,
                "authentication_confirmed_by_user": True,
                "authenticated_at": now.isoformat(),
                "expires_at": (now + timedelta(minutes=duration_minutes)).isoformat(),
            }
        )
        write_json_atomic(self._path(session_id), updated)
        return updated

    def reauthenticate(
        self, session_id: str, *, owner_user_id: str
    ) -> InstitutionSession:
        session = self.load(session_id, owner_user_id=owner_user_id)
        if session.status is InstitutionSessionStatus.REVOKED:
            raise ValueError("revoked institution session cannot be reauthenticated")
        updated = session.model_copy(
            update={
                "status": InstitutionSessionStatus.WAITING_FOR_USER,
                "authentication_confirmed_by_user": False,
                "authenticated_at": None,
                "expires_at": None,
            }
        )
        write_json_atomic(self._path(session_id), updated)
        return updated

    def register_authorized_document(
        self,
        session_id: str,
        *,
        owner_user_id: str,
        file_path: str | Path,
        step_instance_id: str,
        model_processing_approved: bool,
        resource_id: str | None = None,
    ) -> dict[str, Any]:
        session = self.load(session_id, owner_user_id=owner_user_id)
        if session.status is not InstitutionSessionStatus.ACTIVE:
            raise PermissionError(
                "institution session is not active; reauthenticate to resume"
            )
        path = Path(file_path).resolve()
        if not path.is_file():
            raise FileNotFoundError(path)
        content = path.read_bytes()
        if not content.startswith(b"%PDF-"):
            raise ValueError("authorized institutional document must be a PDF")
        digest = hashlib.sha256(content).hexdigest()
        identity = resource_id or retrieval_id(
            "institution-document",
            session.owner_user_id,
            session.study_id,
            digest,
        )
        try:
            resource = self.retrieval.load_resource(identity)
        except FileNotFoundError:
            metadata = {
                "access_status": "access_blocked",
                "owner_user_digest": hashlib.sha256(
                    session.owner_user_id.encode("utf-8")
                ).hexdigest(),
                "institution_id": session.institution_id,
                "cross_user_cache_allowed": False,
            }
            resource = ExternalResource(
                resource_id=identity,
                resource_type=ResourceType.INSTITUTIONAL_DOCUMENT,
                canonical_identifier=f"institutional:{digest}",
                title=path.stem,
                url=None,
                providers=["institutional_access"],
                metadata_verification_status=MetadataVerificationStatus.VERIFIED,
                canonical_metadata_hash=hashlib.sha256(
                    json_bytes(metadata)
                ).hexdigest(),
                metadata=metadata,
            )
            self.retrieval.save_resource(resource)
        artifact = self.retrieval.write_binary_artifact(
            project_id=session.project_id,
            study_id=session.study_id,
            step_instance_id=step_instance_id,
            kind="institutional_full_text",
            content=content,
            producer="institutional_user_handoff",
            extension="pdf",
        )
        decision = ContentRightsPolicy().decide(
            resource_id=resource.resource_id,
            access_mode=AccessMode.INSTITUTIONAL,
            full_text_available=True,
            license_id=resource.license,
            user_authorized=True,
            model_processing_explicitly_allowed=model_processing_approved,
        )
        self.retrieval.save_access_decision(decision)
        snapshot = ResourceSnapshot(
            snapshot_id=retrieval_id(
                "snapshot",
                resource.resource_id,
                digest,
                decision.decision_id,
            ),
            resource_id=resource.resource_id,
            content_level="full_text",
            raw_response_artifact_id=artifact.artifact_id,
            normalized_content_artifact_id=artifact.artifact_id,
            provider="institutional_access",
            content_hash=digest,
            mime_type="application/pdf",
            byte_size=len(content),
            license_status=resource.license or "subscription_authorized",
            access_status="retrieved",
            access_mode="institutional",
            model_processing_allowed=decision.model_processing_allowed,
        )
        self.retrieval.save_snapshot(snapshot)
        binding = ResourceUseBinding(
            binding_id=retrieval_id(
                "binding",
                resource.resource_id,
                snapshot.snapshot_id,
                session.study_id,
                step_instance_id,
                "institutional_full_text",
            ),
            resource_id=resource.resource_id,
            snapshot_id=snapshot.snapshot_id,
            project_id=session.project_id,
            study_id=session.study_id,
            phase=RetrievalPhase.DISCOVERY,
            step_instance_id=step_instance_id,
            purpose="related_work_search",
            usage_role="background_source",
            target_type="study",
            target_id=session.study_id,
            target_field="institutional_documents",
            relation="contextualizes",
            verification_status=MetadataVerificationStatus.VERIFIED,
            verdict_eligible=False,
        )
        self.retrieval.save_binding(binding)
        return {
            "resource": resource.model_dump(mode="json"),
            "snapshot": snapshot.model_dump(mode="json"),
            "access_decision": decision.model_dump(mode="json"),
            "binding": binding.model_dump(mode="json"),
            "source_path_persisted": False,
            "cookie_or_credential_persisted": False,
        }

    def revoke(self, session_id: str, *, owner_user_id: str) -> InstitutionSession:
        session = self.load(session_id, owner_user_id=owner_user_id)
        updated = session.model_copy(
            update={
                "status": InstitutionSessionStatus.REVOKED,
                "revoked_at": utc_now(),
            }
        )
        write_json_atomic(self._path(session_id), updated)
        return updated


def _parse_time(value: str) -> datetime:
    parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    return parsed if parsed.tzinfo else parsed.replace(tzinfo=timezone.utc)


def json_bytes(value: dict[str, Any]) -> bytes:
    import json

    return json.dumps(
        value, ensure_ascii=False, sort_keys=True, separators=(",", ":")
    ).encode("utf-8")


def _find_forbidden_keys(value: Any) -> set[str]:
    found: set[str] = set()
    if isinstance(value, dict):
        for key, item in value.items():
            normalized = str(key).casefold()
            if normalized in _FORBIDDEN_KEYS:
                found.add(normalized)
            found.update(_find_forbidden_keys(item))
    elif isinstance(value, (list, tuple, set)):
        for item in value:
            found.update(_find_forbidden_keys(item))
    return found
