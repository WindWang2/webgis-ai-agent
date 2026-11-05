"""Create a private, local TLS scaffold for the secure Compose stack.

This runs once in the application image, which already includes cryptography.
Nginx only reads the resulting named volume; no package install, network access,
host-side key transfer, or private key in Compose/config source is needed.
"""

from datetime import UTC, datetime, timedelta
import logging
import os
from pathlib import Path
import tempfile

from cryptography import x509
from cryptography.hazmat.primitives import hashes, serialization
from cryptography.hazmat.primitives.asymmetric import rsa
from cryptography.x509.oid import NameOID


def generate_tls_scaffold(directory: Path) -> None:
    os.umask(0o077)
    directory.mkdir(parents=True, exist_ok=True)
    key_path = directory / "server.key"
    cert_path = directory / "server.crt"
    if key_path.is_symlink() or cert_path.is_symlink():
        raise RuntimeError("TLS scaffold must not use symbolic links")
    if key_path.exists() or cert_path.exists():
        if not key_path.is_file() or not cert_path.is_file():
            raise RuntimeError("TLS scaffold is incomplete; refusing to overwrite an existing file")
        key = serialization.load_pem_private_key(key_path.read_bytes(), password=None)
        cert = x509.load_pem_x509_certificate(cert_path.read_bytes())
        public_format = serialization.PublicFormat.SubjectPublicKeyInfo
        if key.public_key().public_bytes(serialization.Encoding.DER, public_format) != (
            cert.public_key().public_bytes(serialization.Encoding.DER, public_format)
        ):
            raise RuntimeError("TLS scaffold key and certificate do not match")
        return

    key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
    name = x509.Name([x509.NameAttribute(NameOID.COMMON_NAME, "webgis-deploy-scaffold")])
    now = datetime.now(UTC)
    cert = (
        x509.CertificateBuilder()
        .subject_name(name)
        .issuer_name(name)
        .public_key(key.public_key())
        .serial_number(x509.random_serial_number())
        .not_valid_before(now - timedelta(minutes=1))
        .not_valid_after(now + timedelta(days=730))
        .add_extension(x509.SubjectAlternativeName([
            x509.DNSName("localhost"), x509.DNSName("webgis-deploy-scaffold"),
        ]), critical=False)
        .sign(key, hashes.SHA256())
    )
    # Write both complete files privately before publishing them. If a crash
    # interrupts the two renames, a partial pair fails closed on the next run.
    with tempfile.TemporaryDirectory(prefix=".tls-", dir=directory) as temporary:
        temp_key = Path(temporary) / "server.key"
        temp_cert = Path(temporary) / "server.crt"
        temp_key.write_bytes(key.private_bytes(
            serialization.Encoding.PEM,
            serialization.PrivateFormat.PKCS8,
            serialization.NoEncryption(),
        ))
        temp_key.chmod(0o600)
        temp_cert.write_bytes(cert.public_bytes(serialization.Encoding.PEM))
        temp_cert.chmod(0o644)
        temp_key.replace(key_path)
        temp_cert.replace(cert_path)


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO, format="[tls-scaffold] %(message)s")
    target = Path(os.environ.get("TLS_DIR", "/app/data"))
    generate_tls_scaffold(target)
    logging.info("TLS scaffold ready in %s", target)
