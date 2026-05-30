from __future__ import annotations

import ipaddress
import os
import socket
from datetime import datetime, timedelta, timezone


DEFAULT_TLS_CERT_DAYS = 3650


def _runtime_root() -> str:
    return os.path.abspath(
        os.path.join(
            os.path.dirname(os.path.abspath(__file__)),
            "..",
            "..",
        )
    )


def default_tls_paths(name: str = "rdk.local") -> tuple[str, str]:
    safe_name = "".join(
        ch.lower()
        for ch in str(name or "rdk.local").strip()
        if ch.isalnum() or ch in {".", "-"}
    ).strip(".-") or "rdk.local"
    cert_dir = os.path.join(_runtime_root(), "certs")
    return (
        os.path.join(cert_dir, f"{safe_name}.crt"),
        os.path.join(cert_dir, f"{safe_name}.key"),
    )


def _lan_ipv4() -> str | None:
    sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    try:
        sock.connect(("8.8.8.8", 80))
        return sock.getsockname()[0]
    except OSError:
        return None
    finally:
        sock.close()


def _subject_alt_names(hosts: list[str]):
    from cryptography import x509

    names = []
    seen = set()
    for host in hosts:
        value = str(host or "").strip()
        if not value or value in seen:
            continue
        seen.add(value)
        try:
            names.append(x509.IPAddress(ipaddress.ip_address(value)))
        except ValueError:
            names.append(x509.DNSName(value))
    return names


def ensure_self_signed_cert(
    cert_file: str | None = None,
    key_file: str | None = None,
    hosts: list[str] | None = None,
    valid_days: int = DEFAULT_TLS_CERT_DAYS,
) -> tuple[str, str]:
    cert_path, key_path = cert_file, key_file
    if not cert_path or not key_path:
        cert_path, key_path = default_tls_paths("rdk.local")

    cert_path = os.path.abspath(cert_path)
    key_path = os.path.abspath(key_path)
    if os.path.isfile(cert_path) and os.path.isfile(key_path):
        return cert_path, key_path

    try:
        from cryptography import x509
        from cryptography.hazmat.primitives import hashes, serialization
        from cryptography.hazmat.primitives.asymmetric import rsa
        from cryptography.x509.oid import NameOID
    except Exception as exc:
        raise RuntimeError(
            "HTTPS requires cryptography or explicit TLS cert/key files"
        ) from exc

    os.makedirs(os.path.dirname(cert_path) or ".", exist_ok=True)
    os.makedirs(os.path.dirname(key_path) or ".", exist_ok=True)

    cert_hosts = list(hosts or [])
    for default_host in ("rdk.local", "localhost", "127.0.0.1"):
        if default_host not in cert_hosts:
            cert_hosts.append(default_host)
    lan_ip = _lan_ipv4()
    if lan_ip and lan_ip not in cert_hosts:
        cert_hosts.append(lan_ip)

    key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
    subject = issuer = x509.Name(
        [
            x509.NameAttribute(NameOID.COMMON_NAME, cert_hosts[0]),
            x509.NameAttribute(NameOID.ORGANIZATION_NAME, "Open-RDK Local"),
        ]
    )
    now = datetime.now(timezone.utc)
    cert = (
        x509.CertificateBuilder()
        .subject_name(subject)
        .issuer_name(issuer)
        .public_key(key.public_key())
        .serial_number(x509.random_serial_number())
        .not_valid_before(now - timedelta(minutes=1))
        .not_valid_after(now + timedelta(days=max(1, int(valid_days))))
        .add_extension(
            x509.SubjectAlternativeName(_subject_alt_names(cert_hosts)),
            critical=False,
        )
        .sign(key, hashes.SHA256())
    )

    with open(key_path, "wb") as fp:
        fp.write(
            key.private_bytes(
                encoding=serialization.Encoding.PEM,
                format=serialization.PrivateFormat.TraditionalOpenSSL,
                encryption_algorithm=serialization.NoEncryption(),
            )
        )
    with open(cert_path, "wb") as fp:
        fp.write(cert.public_bytes(serialization.Encoding.PEM))

    return cert_path, key_path
