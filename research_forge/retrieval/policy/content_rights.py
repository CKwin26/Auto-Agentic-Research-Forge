"""Deterministic content-rights decisions.

This module does not infer legal permission. It converts explicit metadata or
user authorization into conservative machine-enforced access decisions.
"""

from __future__ import annotations

from ..domain.external_models import AccessDecision, AccessMode
from ..domain.models import retrieval_id


class ContentRightsPolicy:
    def decide(
        self,
        *,
        resource_id: str,
        access_mode: AccessMode,
        full_text_available: bool,
        license_id: str | None,
        user_authorized: bool = False,
        model_processing_explicitly_allowed: bool = False,
    ) -> AccessDecision:
        if access_mode is AccessMode.INSTITUTIONAL and not user_authorized:
            raise PermissionError(
                "institutional access requires current user authorization"
            )
        openly_licensed = bool(
            license_id
            and license_id.casefold()
            in {
                "cc0-1.0",
                "cc0",
                "cc-by",
                "cc-by-sa",
                "cc-by-nc",
                "cc-by-nc-sa",
                "cc-by-4.0",
                "cc-by-sa-4.0",
                "cc-by-nc-4.0",
                "cc-by-nc-sa-4.0",
                "mit",
                "apache-2.0",
                "public-domain",
            }
        )
        model_allowed = full_text_available and (
            openly_licensed or model_processing_explicitly_allowed
        )
        return AccessDecision(
            decision_id=retrieval_id(
                "access-decision",
                resource_id,
                access_mode.value,
                full_text_available,
                license_id or "unknown",
                user_authorized,
                model_allowed,
            ),
            resource_id=resource_id,
            access_mode=access_mode,
            full_text_available=full_text_available,
            persistent_storage_allowed=access_mode
            in {AccessMode.OPEN_ACCESS, AccessMode.USER_UPLOAD}
            or user_authorized,
            model_processing_allowed=model_allowed,
            cross_user_cache_allowed=False,
            redistribution_allowed=openly_licensed
            and access_mode is AccessMode.OPEN_ACCESS,
            training_allowed=False,
            decision_basis=(
                f"explicit access mode={access_mode.value}; "
                f"license={license_id or 'unknown'}; "
                f"user_authorized={user_authorized}"
            ),
        )
