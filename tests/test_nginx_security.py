"""Security: Nginx CORS must use allow-list, not echo any Origin."""
import re


def test_nginx_cors_not_wildcard_echo():
    """nginx.conf must NOT use $http_origin directly (echoes any Origin)."""
    with open("deploy/nginx/nginx.conf") as f:
        content = f.read()

    # Must not have: add_header Access-Control-Allow-Origin $http_origin
    assert "$http_origin" not in content or "map $http_origin" in content, (
        "nginx.conf echoes $http_origin directly — any website can make "
        "credentialed cross-origin requests. Use a map with explicit allow-list."
    )
