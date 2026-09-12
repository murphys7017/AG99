import asyncio

import aiohttp
import pytest

from astrbot.core.utils import llm_metadata


class _Response:
    def __init__(self, payload=None, error=None):
        self.payload = payload
        self.error = error

    async def __aenter__(self):
        if self.error:
            raise self.error
        return self

    async def __aexit__(self, exc_type, exc, tb):
        return False

    def raise_for_status(self):
        return None

    async def json(self):
        return self.payload


class _Session:
    def __init__(self, responses):
        self.responses = iter(responses)

    async def __aenter__(self):
        return self

    async def __aexit__(self, exc_type, exc, tb):
        return False

    def get(self, _url):
        return next(self.responses)


@pytest.mark.asyncio
async def test_update_llm_metadata_falls_back_to_secondary_endpoint(monkeypatch):
    payload = {
        "provider": {
            "models": {
                "model-1": {
                    "id": "model-1",
                    "reasoning": True,
                }
            }
        }
    }
    session = _Session(
        [
            _Response(error=aiohttp.ClientError("primary unavailable")),
            _Response(payload=payload),
        ]
    )
    monkeypatch.setattr(llm_metadata.aiohttp, "ClientSession", lambda **_: session)
    monkeypatch.setattr(llm_metadata, "build_tls_connector", lambda: None)
    llm_metadata.LLM_METADATAS.clear()

    await llm_metadata.update_llm_metadata()

    assert llm_metadata.LLM_METADATAS["model-1"]["reasoning"] is True


@pytest.mark.asyncio
async def test_update_llm_metadata_keeps_cache_when_all_endpoints_fail(monkeypatch):
    session = _Session(
        [
            _Response(error=asyncio.TimeoutError()),
            _Response(error=aiohttp.ClientError("secondary unavailable")),
        ]
    )
    monkeypatch.setattr(llm_metadata.aiohttp, "ClientSession", lambda **_: session)
    monkeypatch.setattr(llm_metadata, "build_tls_connector", lambda: None)
    llm_metadata.LLM_METADATAS.clear()
    llm_metadata.LLM_METADATAS["existing"] = {
        "id": "existing",
        "reasoning": False,
        "tool_call": False,
        "knowledge": "none",
        "release_date": "",
        "modalities": {"input": [], "output": []},
        "open_weights": False,
        "limit": {"context": 0, "output": 0},
    }

    await llm_metadata.update_llm_metadata()

    assert set(llm_metadata.LLM_METADATAS) == {"existing"}


@pytest.mark.asyncio
async def test_update_llm_metadata_ignores_empty_payload_before_fallback(monkeypatch):
    payload = {
        "provider": {
            "models": {
                "model-2": {
                    "id": "model-2",
                    "tool_call": True,
                }
            }
        }
    }
    session = _Session([_Response(payload={}), _Response(payload=payload)])
    monkeypatch.setattr(llm_metadata.aiohttp, "ClientSession", lambda **_: session)
    monkeypatch.setattr(llm_metadata, "build_tls_connector", lambda: None)
    llm_metadata.LLM_METADATAS.clear()
    llm_metadata.LLM_METADATAS["existing"] = {
        "id": "existing",
        "reasoning": False,
        "tool_call": False,
        "knowledge": "none",
        "release_date": "",
        "modalities": {"input": [], "output": []},
        "open_weights": False,
        "limit": {"context": 0, "output": 0},
    }

    await llm_metadata.update_llm_metadata()

    assert set(llm_metadata.LLM_METADATAS) == {"model-2"}
