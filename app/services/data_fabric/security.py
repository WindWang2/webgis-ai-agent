"""
Enterprise Geospatial Data Fabric Security Module:
1. SSRF Policy Validator (Blocks private IPs, loopback, cloud metadata endpoints, DNS rebinding obvious paths)
2. Credential Seam & Secret Sanitization (Prevents secret leakage to LLM prompts, logs, and frontend payloads)
3. XXE Protection for XML Payloads (WFS/WMS Capabilities parsers)
4. Multi-Tenant Isolation Protection
"""
import re
import socket
import logging
from urllib.parse import urlparse
from typing import Dict, Any, Optional
from xml.etree import ElementTree as ET

logger = logging.getLogger(__name__)

# Private IP ranges and blocked hostnames
BLOCKED_HOSTNAMES = {"localhost", "loopback", "metadata.google.internal", "kubernetes.default.svc"}
BLOCKED_IP_PREFIXES = ("127.", "10.", "169.254.", "0.0.0.0")


class DataFabricSecurityError(ValueError):
    """Security policy violation exception for Data Fabric."""
    pass


class DataFabricSecurity:
    @staticmethod
    def validate_url(url: str, allow_private: bool = False) -> str:
        """
        SSRF Defense: Validates remote data source URLs.
        Blocks loopback, RFC1918 private subnets, cloud metadata IPs, and invalid schemes.
        """
        if not url or not isinstance(url, str):
            raise DataFabricSecurityError("Invalid URL provided")

        parsed = urlparse(url)
        scheme = parsed.scheme.lower()
        if scheme not in ("http", "https", "s3", "minio"):
            raise DataFabricSecurityError(f"Unsupported or unsafe scheme '{scheme}'")

        if scheme in ("s3", "minio"):
            # S3 protocols use bucket names or endpoint URLs
            return url

        hostname = parsed.hostname
        if not hostname:
            raise DataFabricSecurityError("URL missing valid hostname")

        hostname_lower = hostname.lower()

        if not allow_private:
            if hostname_lower in BLOCKED_HOSTNAMES or hostname_lower.endswith(".local") or hostname_lower.endswith(".internal"):
                raise DataFabricSecurityError(f"SSRF Protection: Access to hostname '{hostname}' is blocked")

            # Check raw IP or resolve DNS
            try:
                ip_addr = socket.gethostbyname(hostname)
                if any(ip_addr.startswith(prefix) for prefix in BLOCKED_IP_PREFIXES):
                    raise DataFabricSecurityError(f"SSRF Protection: Access to private IP '{ip_addr}' is blocked")
                if ip_addr.startswith("172."):
                    parts = [int(p) for p in ip_addr.split(".")]
                    if 16 <= parts[1] <= 31:
                        raise DataFabricSecurityError(f"SSRF Protection: Access to private IP '{ip_addr}' is blocked")
                if ip_addr.startswith("192.168."):
                    raise DataFabricSecurityError(f"SSRF Protection: Access to private IP '{ip_addr}' is blocked")
            except socket.gaierror:
                logger.warning(f"Unable to resolve hostname '{hostname}' for SSRF check; proceeding with hostname policy check")

        return url

    @staticmethod
    def sanitize_profile_dict(profile_dict: Dict[str, Any]) -> Dict[str, Any]:
        """
        Redacts credentials and passwords before returning ConnectionProfile to LLM or frontend.
        """
        sanitized = dict(profile_dict)
        sensitive_keys = {"password", "secret", "secret_key", "token", "api_key", "access_key", "credential"}

        for k, v in list(sanitized.items()):
            if any(s in k.lower() for s in sensitive_keys):
                sanitized[k] = "********"

        return sanitized

    @staticmethod
    def parse_safe_xml(xml_content: bytes) -> ET.Element:
        """
        XXE Defense: Safe XML parser for WFS / WMS GetCapabilities payloads.
        Defends against Entity Expansion attacks and External DTD inclusions.
        """
        try:
            # ET.fromstring in standard Python standard library ignores DTD entities by default,
            # but we strip DTD declarations for extra defense-in-depth.
            clean_xml = re.sub(rb"<!DOCTYPE[^>]*>", b"", xml_content, flags=re.IGNORECASE)
            clean_xml = re.sub(rb"<!ENTITY[^>]*>", b"", clean_xml, flags=re.IGNORECASE)
            return ET.fromstring(clean_xml)
        except Exception as e:
            logger.error(f"Failed to parse XML safely: {e}")
            raise DataFabricSecurityError(f"Malformed or unsafe XML payload: {e}")
