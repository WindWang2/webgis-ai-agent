"""Security and credential sanitization utilities."""
import re

# Masking patterns
# 1. Database password pattern: e.g. postgresql://user:password@host:port/db
_DB_PASSWORD_RE = re.compile(r'([a-zA-Z0-9\+]+://[^:/]+:)([^@]+)(@[^/]+)')

# 2. General secrets / tokens / keys in key-value format: e.g. api_key=xyz, secret: abc
_KEY_VALUE_SECRET_RE = re.compile(
    r'(api[_-]?key|secret|password|passwd|token|jwt[_-]?secret)[\s]*([=:])[\s]*([a-zA-Z0-9_\-\.\~]{6,})',
    re.IGNORECASE
)

# 3. OpenAI API keys and general keys: e.g. sk-proj-...
_OPENAI_KEY_RE = re.compile(r'(sk-[a-zA-Z0-9_\-]{20,})')


def sanitize_error_msg(error_msg: str) -> str:
    """Sanitize sensitive credentials, passwords, and API keys from error messages."""
    if not error_msg:
        return ""
    
    s = str(error_msg)
    
    # 1. Mask database connection passwords
    s = _DB_PASSWORD_RE.sub(r'\1******\3', s)
    
    # 2. Mask API key or secrets in key-value format
    def _mask_kv(match):
        prefix = match.group(1)
        separator = match.group(2)
        secret = match.group(3)
        # Preserve first and last character of the secret for context, mask the rest
        if len(secret) > 4:
            masked = secret[0] + "***" + secret[-1]
        else:
            masked = "***"
        return f"{prefix}{separator}{masked}"
    
    s = _KEY_VALUE_SECRET_RE.sub(_mask_kv, s)
    
    # 3. Mask OpenAI keys
    def _mask_openai(match):
        key = match.group(1)
        if len(key) > 8:
            return key[:4] + "***" + key[-4:]
        return "***"
    
    s = _OPENAI_KEY_RE.sub(_mask_openai, s)
    
    return s
