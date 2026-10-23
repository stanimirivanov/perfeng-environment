"""Inspect private gateway permissions and certificate trust without installing trust."""

from __future__ import annotations

import hashlib
import os
import ssl
import subprocess
from pathlib import Path

if __package__:
    from .native_gateway_tool import GatewayError, safe_state
else:
    from native_gateway_tool import GatewayError, safe_state

POWERSHELL_TIMEOUT = 40  # Include cold startup, within the doctor's 45-second command budget.

ACL_SCRIPT = """
$ErrorActionPreference = 'Stop'
$path = $env:PERFENG_GATEWAY_STATE_PATH
$sid = [Security.Principal.WindowsIdentity]::GetCurrent().User.Value
$allowed = @($sid, 'S-1-5-18', 'S-1-5-32-544')
$items = @((Get-Item -LiteralPath $path -Force)) + @(Get-ChildItem -LiteralPath $path -Recurse -Force)
if ($items.Count -gt 4096) { throw 'state limit' }
foreach ($item in $items) {
  $acl = Get-Acl -LiteralPath $item.FullName
  if ($acl.GetOwner([Security.Principal.SecurityIdentifier]).Value -notin $allowed) { throw 'owner' }
  foreach ($rule in $acl.Access) {
    $identity = $rule.IdentityReference.Translate([Security.Principal.SecurityIdentifier]).Value
    # OWNER RIGHTS refers to this object's owner, already checked against the trusted SID list.
    if ($rule.AccessControlType -eq 'Allow' -and $identity -notin $allowed -and $identity -ne 'S-1-3-4') { throw 'broad access' }
  }
}
'private'
"""

SECURE_SCRIPT = """
$ErrorActionPreference = 'Stop'
$path = $env:PERFENG_GATEWAY_STATE_PATH
$sid = [Security.Principal.WindowsIdentity]::GetCurrent().User
$owner = [IO.Directory]::GetAccessControl($path, [Security.AccessControl.AccessControlSections]::Owner).GetOwner([Security.Principal.SecurityIdentifier]).Value
if ($owner -notin @($sid.Value, 'S-1-5-18', 'S-1-5-32-544')) { throw 'foreign owner' }
$acl = [Security.AccessControl.DirectorySecurity]::new()
$acl.SetAccessRuleProtection($true, $false)
foreach ($identity in @($sid, [Security.Principal.SecurityIdentifier]::new('S-1-5-18'), [Security.Principal.SecurityIdentifier]::new('S-1-5-32-544'))) {
  $rule = [Security.AccessControl.FileSystemAccessRule]::new($identity, [Security.AccessControl.FileSystemRights]::FullControl, [Security.AccessControl.InheritanceFlags]'ContainerInherit,ObjectInherit', [Security.AccessControl.PropagationFlags]::None, [Security.AccessControl.AccessControlType]::Allow)
  $acl.AddAccessRule($rule)
}
[IO.Directory]::SetAccessControl($path, $acl)
"""


def powershell(script: str, path: Path) -> str:
    """Run a fixed read/permission operation with a path in private process environment."""
    environment = {key: value for key, value in os.environ.items() if key.lower() != "psmodulepath"}
    system_root = os.environ.get("SystemRoot")
    if not system_root:
        raise GatewayError(
            "HOST_SECURITY",
            "Windows system directory is unavailable",
            "inspect this Windows process environment",
        )
    engine = Path(system_root) / "System32/WindowsPowerShell/v1.0/powershell.exe"
    try:
        result = subprocess.run(
            [str(engine), "-NoProfile", "-NonInteractive", "-Command", script],
            env={**environment, "PERFENG_GATEWAY_STATE_PATH": str(path)},
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            check=True,
            timeout=POWERSHELL_TIMEOUT,
        )
    except subprocess.TimeoutExpired:
        raise GatewayError(
            "HOST_SECURITY_TIMEOUT",
            f"Windows PowerShell security operation exceeded {POWERSHELL_TIMEOUT}s",
            "inspect Windows host load and gateway state before repeating permission changes; "
            "no vendor output was printed",
        ) from None
    except (OSError, subprocess.SubprocessError):
        raise GatewayError(
            "HOST_SECURITY",
            "Gateway permission/trust inspection failed",
            "inspect this account's gateway Security settings; no vendor output was printed",
        ) from None
    if len(result.stdout) > 4096:
        raise GatewayError(
            "HOST_SECURITY",
            "Host security response exceeds its limit",
            "inspect the gateway directory",
        )
    return result.stdout.strip()


def private_permissions(root: Path) -> None:
    """Require account-private gateway state; never change an existing ACL during diagnosis."""
    state = safe_state(root)
    if not state.exists():
        raise GatewayError(
            "STATE_MISSING",
            "Gateway state is missing",
            "run task native:setup after selecting a release",
        )
    if os.name == "nt":
        if powershell(ACL_SCRIPT, state) != "private":
            raise GatewayError(
                "STATE_PERMISSIONS",
                "Gateway state is not private",
                "review its Security settings; use task native:permissions only after review",
            )
    elif state.stat().st_mode & 0o077:
        raise GatewayError(
            "STATE_PERMISSIONS",
            "Gateway state permits other users",
            "review gateway directory permissions",
        )


def secure_permissions(root: Path) -> None:
    """Explicitly restrict the gateway root ACL; retain bytes and reject foreign child ACLs afterward."""
    state = safe_state(root)
    state.mkdir(parents=True, exist_ok=True, mode=0o700)
    if os.name == "nt":
        powershell(SECURE_SCRIPT, state)
    else:
        state.chmod(0o700)
    private_permissions(root)
    print(
        "PASS gateway root permissions restricted; child ACLs verified. CA and build files retained.",
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


def windows_trust(root: Path) -> bool:
    """Check current-user Windows roots for this exact public certificate; no trust-store writes."""
    path = ca_file(root)
    script = """
$ErrorActionPreference = 'Stop'
$certificate = [Security.Cryptography.X509Certificates.X509Certificate2]::new($env:PERFENG_GATEWAY_STATE_PATH)
if (Test-Path -LiteralPath ('Cert:\\CurrentUser\\Root\\' + $certificate.Thumbprint)) { 'trusted' } else { 'missing' }
"""
    return os.name == "nt" and powershell(script, path) == "trusted"


def node_trust(root: Path) -> bool:
    """Report NODE_EXTRA_CA_CERTS for this terminal only, not another worker process."""
    selected = os.environ.get("NODE_EXTRA_CA_CERTS")
    return selected is not None and Path(selected).resolve() == ca_file(root).resolve()
