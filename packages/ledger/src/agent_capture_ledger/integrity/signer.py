"""Ed25519 signing for attestations.

V1 reads a PEM keypair from disk (path from settings). The ``Signer``
protocol makes KMS or HSM integration a drop-in v2 swap.
"""

from __future__ import annotations

from pathlib import Path
from typing import Protocol

from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric.ed25519 import (
    Ed25519PrivateKey,
    Ed25519PublicKey,
)


class Signer(Protocol):
    key_id: str

    def sign(self, payload: bytes) -> bytes: ...
    def public_key_bytes(self) -> bytes: ...


class FileEd25519Signer:
    def __init__(self, key_path: Path, key_id: str) -> None:
        self.key_id = key_id
        data = Path(key_path).read_bytes()
        loaded = serialization.load_pem_private_key(data, password=None)
        if not isinstance(loaded, Ed25519PrivateKey):
            raise ValueError(f"key at {key_path} is not Ed25519")
        self._priv: Ed25519PrivateKey = loaded

    def sign(self, payload: bytes) -> bytes:
        return self._priv.sign(payload)

    def public_key_bytes(self) -> bytes:
        return self._priv.public_key().public_bytes(
            encoding=serialization.Encoding.Raw,
            format=serialization.PublicFormat.Raw,
        )


class KmsEd25519Signer:
    """Attestation signer backed by a Cloud KMS Ed25519 (PureEdDSA) key version.

    The private key is generated and held in Cloud KMS and never leaves it —
    closing the provenance gap of a file key generated outside KMS. Implements
    the same :class:`Signer` protocol and returns the same *raw* Ed25519
    public-key bytes as :class:`FileEd25519Signer`, so the attestation and
    verification contract (published public key, ``verify_signature``) is
    unchanged.

    ``key_version`` is the full KMS resource name, e.g.
    ``projects/<p>/locations/<l>/keyRings/<kr>/cryptoKeys/<k>/cryptoKeyVersions/<v>``.
    """

    def __init__(self, key_version: str, key_id: str) -> None:
        # Lazy import so the SDK doesn't require google-cloud-kms unless KMS
        # signing is configured (install the `kms` extra).
        from google.cloud import kms

        self.key_id = key_id
        self._key_version = key_version
        self._client = kms.KeyManagementServiceClient()

    def sign(self, payload: bytes) -> bytes:
        # Ed25519 / PureEdDSA signs the raw message (no client-side digest).
        resp = self._client.asymmetric_sign(request={"name": self._key_version, "data": payload})
        return bytes(resp.signature)

    def public_key_bytes(self) -> bytes:
        pub = self._client.get_public_key(request={"name": self._key_version})
        loaded = serialization.load_pem_public_key(pub.pem.encode())
        if not isinstance(loaded, Ed25519PublicKey):
            raise ValueError("KMS key version is not Ed25519")
        return loaded.public_bytes(
            encoding=serialization.Encoding.Raw,
            format=serialization.PublicFormat.Raw,
        )


def load_signer() -> Signer:
    """Build the attestation signer from settings.

    ``LEDGER_SIGNING_KMS_KEY`` (a KMS key-version resource name) selects the
    KMS-backed signer; otherwise ``LEDGER_SIGNING_KEY_PATH`` selects the file
    signer. Shared by the anchor worker and the ``ledger attest`` CLI.
    """
    from agent_capture_ledger.config import get_settings

    settings = get_settings()
    if settings.signing_kms_key:
        return KmsEd25519Signer(settings.signing_kms_key, settings.signing_key_id)
    if settings.signing_key_path is None:
        raise RuntimeError("Set LEDGER_SIGNING_KMS_KEY or LEDGER_SIGNING_KEY_PATH for attestations")
    return FileEd25519Signer(settings.signing_key_path, settings.signing_key_id)


def generate_keypair(out_dir: Path, key_id: str) -> tuple[Path, Path]:
    """Generate a new keypair, write PEMs, return (priv_path, pub_path)."""
    out_dir.mkdir(parents=True, exist_ok=True)
    priv = Ed25519PrivateKey.generate()
    priv_pem = priv.private_bytes(
        encoding=serialization.Encoding.PEM,
        format=serialization.PrivateFormat.PKCS8,
        encryption_algorithm=serialization.NoEncryption(),
    )
    pub_pem = priv.public_key().public_bytes(
        encoding=serialization.Encoding.PEM,
        format=serialization.PublicFormat.SubjectPublicKeyInfo,
    )
    priv_path = out_dir / f"{key_id}.priv.pem"
    pub_path = out_dir / f"{key_id}.pub.pem"
    priv_path.write_bytes(priv_pem)
    pub_path.write_bytes(pub_pem)
    priv_path.chmod(0o600)
    return priv_path, pub_path


def verify_signature(public_pem: bytes, payload: bytes, signature: bytes) -> bool:
    from cryptography.exceptions import InvalidSignature

    pub = serialization.load_pem_public_key(public_pem)
    if not isinstance(pub, Ed25519PublicKey):
        raise ValueError("not an Ed25519 public key")
    try:
        pub.verify(signature, payload)
    except InvalidSignature:
        return False
    return True
