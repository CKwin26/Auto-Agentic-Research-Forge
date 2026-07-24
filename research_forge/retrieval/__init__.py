"""Forge Retrieval Gateway.

The gateway is horizontal Workflow v2 infrastructure, not a fifth research
phase. External access from workflow handlers must enter through
``RetrievalGateway``.
"""

from .interfaces.service import RetrievalGateway

__all__ = ["RetrievalGateway"]
