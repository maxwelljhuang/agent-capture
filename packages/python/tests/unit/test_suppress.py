"""Tests for the model_call suppression mechanism."""

from __future__ import annotations

from agent_capture.context.propagation import (
    model_call_suppressed,
    suppress_model_call_capture,
)


def test_default_is_not_suppressed() -> None:
    assert model_call_suppressed() is False


def test_suppress_scope_activates_and_releases() -> None:
    assert model_call_suppressed() is False
    with suppress_model_call_capture():
        assert model_call_suppressed() is True
    assert model_call_suppressed() is False


def test_suppress_scope_releases_on_exception() -> None:
    import pytest

    with pytest.raises(RuntimeError), suppress_model_call_capture():
        assert model_call_suppressed() is True
        raise RuntimeError("x")
    assert model_call_suppressed() is False


def test_nested_suppress_stays_active() -> None:
    with suppress_model_call_capture():
        with suppress_model_call_capture():
            assert model_call_suppressed() is True
        # Inner scope reset its own token; outer is still True.
        assert model_call_suppressed() is True
    assert model_call_suppressed() is False
