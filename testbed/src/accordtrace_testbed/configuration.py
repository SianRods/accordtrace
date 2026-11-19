"""Secret-safe, offline configuration readiness, distinct from provider verification."""
from .config import Settings


def inspect(settings: Settings):
    judge = {
        'api_key_present': bool(settings.gemini_api_key.get_secret_value()),
        'model': settings.gemini_model or None,
        'free_tier_confirmed': settings.gemini_free_tier_confirmed,
    }
    return {
        'backend': {'url': settings.backend_url, 'token_present': bool(settings.backend_api_token.get_secret_value())},
        'sources_present': settings.sources.is_dir(),
        'ollama': {'url': settings.ollama_url, 'model': settings.ollama_model},
        'gemini_judge': judge,
        'judge_configured': all(judge.values()),
        'judge_budget': {'requests_per_minute': settings.judge_requests_per_minute, 'max_calls': settings.judge_max_calls},
        'timeouts_seconds': {'request': settings.request_timeout_seconds, 'chat_wait': settings.chat_wait_seconds},
        'live_providers_checked': False,
        'pdf': {'automatic': True, 'filename': 'report.pdf', 'network_required': False},
    }
