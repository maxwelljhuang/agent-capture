"""Ed25519 sign/verify roundtrip."""

from __future__ import annotations

from pathlib import Path

import pytest

from agent_capture_ledger.integrity.signer import (
    FileEd25519Signer,
    generate_keypair,
    verify_signature,
)


def test_sign_then_verify(tmp_path: Path) -> None:
    priv_path, pub_path = generate_keypair(tmp_path, "test-key")
    signer = FileEd25519Signer(priv_path, "test-key")
    payload = b"hello-attest"
    sig = signer.sign(payload)
    assert verify_signature(pub_path.read_bytes(), payload, sig)


def test_verify_fails_on_tampered_payload(tmp_path: Path) -> None:
    priv_path, pub_path = generate_keypair(tmp_path, "test-key")
    signer = FileEd25519Signer(priv_path, "test-key")
    sig = signer.sign(b"original")
    assert not verify_signature(pub_path.read_bytes(), b"tampered", sig)


def test_rejects_non_ed25519_key(tmp_path: Path) -> None:
    from cryptography.hazmat.primitives import serialization
    from cryptography.hazmat.primitives.asymmetric import rsa

    rsa_key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
    pem = rsa_key.private_bytes(
        encoding=serialization.Encoding.PEM,
        format=serialization.PrivateFormat.PKCS8,
        encryption_algorithm=serialization.NoEncryption(),
    )
    p = tmp_path / "rsa.pem"
    p.write_bytes(pem)
    with pytest.raises(ValueError, match="not Ed25519"):
        FileEd25519Signer(p, "test")
