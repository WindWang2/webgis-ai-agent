import os

# Provide a stable test JWT secret so Settings doesn't warn on every import
os.environ.setdefault("JWT_SECRET_KEY", "test-secret-key-not-for-production")
os.environ.setdefault("USE_REDIS", "false")
