"""ARCH-16: route get_registry/get_engine delegate to the services holder.

``api/routes/chat.py`` kept module globals while ``engine_instance`` held the
same singletons. The accessors now prefer the services-layer holder and only
fall back to the module global (legacy direct test injection / pre-lifespan).
"""
from __future__ import annotations

import pytest
from fastapi import HTTPException

import app.api.routes.chat as chat_mod
from app.services.chat import engine_instance


def test_get_registry_prefers_engine_instance_holder(monkeypatch):
    sentinel = object()
    monkeypatch.setattr(engine_instance, "_registry", sentinel)
    monkeypatch.setattr(chat_mod, "registry", object())

    assert chat_mod.get_registry() is sentinel


def test_get_registry_falls_back_to_module_global(monkeypatch):
    sentinel = object()
    monkeypatch.setattr(engine_instance, "_registry", None)
    monkeypatch.setattr(chat_mod, "registry", sentinel)

    assert chat_mod.get_registry() is sentinel


def test_get_registry_503_when_unset(monkeypatch):
    monkeypatch.setattr(engine_instance, "_registry", None)
    monkeypatch.setattr(chat_mod, "registry", None)

    with pytest.raises(HTTPException) as exc_info:
        chat_mod.get_registry()

    assert exc_info.value.status_code == 503


def test_get_engine_prefers_engine_instance_holder(monkeypatch):
    sentinel = object()
    monkeypatch.setattr(engine_instance, "_engine", sentinel)
    monkeypatch.setattr(chat_mod, "engine", object())

    assert chat_mod.get_engine() is sentinel


def test_get_engine_falls_back_to_module_global(monkeypatch):
    sentinel = object()
    monkeypatch.setattr(engine_instance, "_engine", None)
    monkeypatch.setattr(chat_mod, "engine", sentinel)

    assert chat_mod.get_engine() is sentinel


def test_get_engine_503_when_unset(monkeypatch):
    monkeypatch.setattr(engine_instance, "_engine", None)
    monkeypatch.setattr(chat_mod, "engine", None)

    with pytest.raises(HTTPException) as exc_info:
        chat_mod.get_engine()

    assert exc_info.value.status_code == 503
