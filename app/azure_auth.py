"""Entra ID (RBAC) credentials - API keys are never used."""

from __future__ import annotations

from functools import lru_cache

from azure.identity import DefaultAzureCredential, get_bearer_token_provider

from app.config import settings

COGNITIVE_SCOPE = "https://cognitiveservices.azure.com/.default"


@lru_cache(maxsize=1)
def credential() -> DefaultAzureCredential:
    kwargs = {"exclude_interactive_browser_credential": True}
    if settings.tenant_id:
        kwargs["additionally_allowed_tenants"] = ["*"]
        kwargs["shared_cache_tenant_id"] = settings.tenant_id
        kwargs["visual_studio_code_tenant_id"] = settings.tenant_id
    return DefaultAzureCredential(**kwargs)


@lru_cache(maxsize=1)
def aoai_token_provider():
    """Bearer-token provider for Azure OpenAI (needs 'Cognitive Services OpenAI User')."""
    return get_bearer_token_provider(credential(), COGNITIVE_SCOPE)
