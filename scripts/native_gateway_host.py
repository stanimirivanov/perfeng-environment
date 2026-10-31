"""Inspect private gateway permissions and certificate trust without installing trust."""

from __future__ import annotations

import hashlib
import os
import ssl
from pathlib import Path

if __package__:
    from .native_gateway_tool import GatewayError, safe_state
else:
    from native_gateway_tool import GatewayError, safe_state


def private_permissions(root: Path) -> None:
    """Require account-private gateway state; never change an existing ACL during diagnosis."""
    state = safe_state(root)
    if not state.exists():
        raise GatewayError(
            "STATE_MISSING",
            "Gateway state is missing",
            "run task native:setup after selecting a release",
        )
    if state.stat().st_uid != os.getuid() or state.stat().st_mode & 0o077:
        raise GatewayError(
            "STATE_PERMISSIONS",
            "Gateway state permits other users",
            "review gateway directory permissions",
        )


def secure_permissions(root: Path) -> None:
    """Explicitly restrict the gateway root permissions; retain existing bytes."""
    state = safe_state(root)
    state.mkdir(parents=True, exist_ok=True, mode=0o700)
    state.chmod(0o700)
    private_permissions(root)
    print(
        "PASS gateway root permissions restricted; ownership verified. CA and build files retained.",
        flush=True,
    )


def ca_file(root: Path) -> Path:
    """Locate only the retained gateway's public CA under validated local state."""
    return safe_state(root) / "caddy-data/pki/authorities/local/root.crt"


def fingerprint(root: Path) -> str | None:
    """Return SHA-256 of the CA certificate DER, not the PEM text, or None before first start."""
    path = ca_file(root)
    if not path.is_file():
        return None
    if path.stat().st_size > 64 * 1024:
        raise GatewayError(
            "CA_INVALID",
            "Gateway CA exceeds its limit",
            "inspect retained CA state; never rotate it implicitly",
        )
    try:
        der = ssl.PEM_cert_to_DER_cert(path.read_text(encoding="ascii"))
    except (ValueError, UnicodeError):
        raise GatewayError(
            "CA_INVALID", "Gateway public CA is not a PEM certificate", "inspect retained CA state"
        ) from None
    return hashlib.sha256(der).hexdigest()


def system_trust(root: Path) -> bool:
    """Read the configured Linux CA store without changing trust or bypassing TLS."""
    expected = fingerprint(root)
    if expected is None:
        return False
    return any(
        hashlib.sha256(cert).hexdigest() == expected
        for cert in ssl.create_default_context().get_ca_certs(binary_form=True)
    )


def node_trust(root: Path) -> bool:
    """Report NODE_EXTRA_CA_CERTS for this terminal only, not another worker process."""
    selected = os.environ.get("NODE_EXTRA_CA_CERTS")
    return selected is not None and Path(selected).resolve() == ca_file(root).resolve()
