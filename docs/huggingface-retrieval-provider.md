# Hugging Face retrieval provider

The provider uses the official `huggingface_hub` client. It searches models,
datasets, and Spaces, records Cards, files, tags, licenses and relationships,
and pins every usable result to a full revision SHA.

Public metadata needs no user key. Gated or private files require explicit
authorization and a provider-local token. Downloads must name an approved file
and pinned revision. Likes and downloads remain adoption signals only.
