import asyncio
import base64
from pathlib import Path
from types import SimpleNamespace

import pytest

from astrbot.core.provider.sources.elevenlabs_tts_source import (
    ElevenLabsTTSAPIError,
    ProviderElevenLabsTTSAPI,
)
from astrbot.core.provider.sources.mimo_api_common import (
    MiMoAPIError,
    _validate_wav_payload,
    build_headers,
    prepare_audio_input,
)
from astrbot.core.provider.sources.mimo_stt_api_source import ProviderMiMoSTTAPI
from astrbot.core.provider.sources.mimo_tts_api_source import ProviderMiMoTTSAPI
from astrbot.core.provider.sources.minimax_tts_api_source import ProviderMiniMaxTTSAPI

MIMO_STT_TEST_WAV_HEADER = b"RIFF$\x00\x00\x00WAVEfmt "
MIMO_STT_TEST_AUDIO_BASE64 = base64.b64encode(MIMO_STT_TEST_WAV_HEADER).decode()
MIMO_STT_TEST_AUDIO_DATA_URL = f"data:audio/wav;base64,{MIMO_STT_TEST_AUDIO_BASE64}"


def _make_tts_provider(overrides: dict | None = None) -> ProviderMiMoTTSAPI:
    provider_config = {
        "id": "test-mimo-tts",
        "type": "mimo_tts_api",
        "model": "mimo-v2-tts",
        "api_key": "test-key",
        "mimo-tts-voice": "mimo_default",
        "mimo-tts-format": "wav",
        "mimo-tts-seed-text": "seed text",
    }
    if overrides:
        provider_config.update(overrides)
    return ProviderMiMoTTSAPI(provider_config=provider_config, provider_settings={})


def _make_stt_provider(overrides: dict | None = None) -> ProviderMiMoSTTAPI:
    provider_config = {
        "id": "test-mimo-stt",
        "type": "mimo_stt_api",
        "model": "mimo-v2.5-asr",
        "api_key": "test-key",
    }
    if overrides:
        provider_config.update(overrides)
    return ProviderMiMoSTTAPI(provider_config=provider_config, provider_settings={})


def _make_minimax_tts_provider(overrides: dict | None = None) -> ProviderMiniMaxTTSAPI:
    provider_config = {
        "id": "test-minimax-tts",
        "type": "minimax_tts_api",
        "model": "speech-02-hd",
        "api_key": "test-key",
        "minimax-group-id": "group-id",
        "minimax-is-timber-weight": True,
    }
    if overrides:
        provider_config.update(overrides)
    return ProviderMiniMaxTTSAPI(provider_config=provider_config, provider_settings={})


def _make_elevenlabs_tts_provider(
    overrides: dict | None = None,
) -> ProviderElevenLabsTTSAPI:
    provider_config = {
        "id": "test-elevenlabs-tts",
        "type": "elevenlabs_tts_api",
        "model": "eleven_multilingual_v2",
        "api_key": "test-key",
        "api_base": "https://api.elevenlabs.io/v1",
        "elevenlabs-tts-voice-id": "voice-id",
        "elevenlabs-tts-output-format": "mp3_44100_128",
        "elevenlabs-tts-use-speaker-boost": True,
        "timeout": "20",
    }
    if overrides:
        provider_config.update(overrides)
    return ProviderElevenLabsTTSAPI(
        provider_config=provider_config,
        provider_settings={},
    )


def test_mimo_tts_user_prompt_returns_seed_text():
    provider = _make_tts_provider()
    try:
        assert provider._build_user_prompt() == "seed text"
    finally:
        asyncio.run(provider.terminate())


def test_mimo_tts_assistant_content_prefixes_style_and_dialect():
    provider = _make_tts_provider(
        {
            "mimo-tts-style-prompt": "开心",
            "mimo-tts-dialect": "四川话",
            "mimo-tts-seed-text": "You are chatting with a close friend.",
        }
    )
    try:
        payload = provider._build_payload("hello")
        assert payload["messages"][0] == {
            "role": "user",
            "content": "You are chatting with a close friend.",
        }
        assert payload["messages"][1]["content"] == "<style>开心 四川话</style>hello"
    finally:
        asyncio.run(provider.terminate())


def test_mimo_tts_payload_omits_user_message_without_seed_text():
    provider = _make_tts_provider(
        {
            "mimo-tts-seed-text": "",
            "mimo-tts-style-prompt": "开心",
        }
    )
    try:
        payload = provider._build_payload("hello")
        assert payload["messages"] == [
            {
                "role": "assistant",
                "content": "<style>开心</style>hello",
            }
        ]
    finally:
        asyncio.run(provider.terminate())


def test_mimo_tts_singing_style_uses_single_style_tag():
    provider = _make_tts_provider(
        {
            "mimo-tts-style-prompt": "唱歌 开心",
            "mimo-tts-dialect": "粤语",
        }
    )
    try:
        payload = provider._build_payload("歌词")
        assert payload["messages"][1]["content"] == "<style>唱歌</style>歌词"
    finally:
        asyncio.run(provider.terminate())


def test_mimo_tts_plain_text_stays_in_assistant_message_when_no_style():
    provider = _make_tts_provider(
        {
            "mimo-tts-seed-text": "",
        }
    )
    try:
        payload = provider._build_payload("hello")
        assert payload["messages"] == [
            {
                "role": "assistant",
                "content": "hello",
            }
        ]
    finally:
        asyncio.run(provider.terminate())


def test_mimo_tts_seed_text_is_not_prepended_to_assistant_content():
    provider = _make_tts_provider(
        {
            "mimo-tts-style-prompt": "开心",
            "mimo-tts-seed-text": "reference text",
        }
    )
    try:
        payload = provider._build_payload("明天就是周五了")
        assert payload["messages"][0]["content"] == "reference text"
        assert payload["messages"][1]["content"] == "<style>开心</style>明天就是周五了"
        assert "reference text" not in payload["messages"][1]["content"]
    finally:
        asyncio.run(provider.terminate())


def test_mimo_tts_voicedesign_model_omits_voice_param():
    provider = _make_tts_provider(
        {
            "model": "mimo-v2.5-tts-voicedesign",
            "mimo-tts-seed-text": "",
        }
    )
    try:
        payload = provider._build_payload("hello")
        assert payload["audio"] == {"format": "wav"}
    finally:
        asyncio.run(provider.terminate())


def test_mimo_tts_regular_model_includes_voice_param():
    provider = _make_tts_provider(
        {
            "model": "mimo-v2.5-tts",
            "mimo-tts-voice": "custom_voice",
            "mimo-tts-seed-text": "",
        }
    )
    try:
        payload = provider._build_payload("hello")
        assert payload["audio"] == {"format": "wav", "voice": "custom_voice"}
    finally:
        asyncio.run(provider.terminate())


def test_mimo_headers_use_single_authorization_method():
    assert build_headers("test-key") == {
        "Content-Type": "application/json",
        "Authorization": "Bearer test-key",
    }


def test_minimax_tts_empty_timber_weight_uses_default():
    provider = _make_minimax_tts_provider({"minimax-timber-weight": ""})

    body = provider._build_tts_stream_body("hello")

    assert '"timber_weights": [{"voice_id": "Chinese (Mandarin)_Warm_Girl", "weight": 1}]' in body


def test_minimax_tts_invalid_timber_weight_uses_default():
    provider = _make_minimax_tts_provider({"minimax-timber-weight": "not-json"})

    body = provider._build_tts_stream_body("hello")

    assert '"timber_weights": [{"voice_id": "Chinese (Mandarin)_Warm_Girl", "weight": 1}]' in body


def test_elevenlabs_tts_builds_payload_with_configured_voice_settings():
    provider = _make_elevenlabs_tts_provider(
        {
            "elevenlabs-tts-stability": "0.3",
            "elevenlabs-tts-similarity-boost": 0.7,
            "elevenlabs-tts-style": "",
        }
    )
    try:
        payload = provider._build_payload("hello")
        assert payload == {
            "text": "hello",
            "model_id": "eleven_multilingual_v2",
            "voice_settings": {
                "stability": 0.3,
                "similarity_boost": 0.7,
                "use_speaker_boost": True,
            },
        }
    finally:
        asyncio.run(provider.terminate())


def test_elevenlabs_tts_rejects_raw_audio_format():
    with pytest.raises(ValueError, match="raw audio"):
        provider = _make_elevenlabs_tts_provider(
            {"elevenlabs-tts-output-format": "pcm_44100"}
        )
        asyncio.run(provider.terminate())


def test_elevenlabs_tts_rejects_invalid_float_setting():
    with pytest.raises(ValueError, match="between 0 and 1"):
        provider = _make_elevenlabs_tts_provider(
            {"elevenlabs-tts-stability": "1.5"}
        )
        asyncio.run(provider.terminate())


@pytest.mark.asyncio
async def test_elevenlabs_tts_get_audio_writes_response_content():
    provider = _make_elevenlabs_tts_provider(
        {"elevenlabs-tts-output-format": "opus_48000_128"}
    )

    captured: dict = {}

    class _Response:
        status_code = 200
        content = b"audio-bytes"
        text = ""

    async def fake_post(url, headers=None, params=None, json=None):
        captured["url"] = url
        captured["headers"] = headers
        captured["params"] = params
        captured["json"] = json
        return _Response()

    await provider.client.aclose()
    provider.client = SimpleNamespace(post=fake_post, aclose=_fake_aclose())

    path = await provider.get_audio("hello")

    assert path.endswith(".opus")
    with open(path, "rb") as file:
        assert file.read() == b"audio-bytes"
    assert captured["url"] == "https://api.elevenlabs.io/v1/text-to-speech/voice-id"
    assert captured["headers"]["xi-api-key"] == "test-key"
    assert captured["params"] == {"output_format": "opus_48000_128"}
    assert captured["json"]["text"] == "hello"
    await provider.terminate()


@pytest.mark.asyncio
async def test_elevenlabs_tts_get_audio_raises_on_http_error():
    provider = _make_elevenlabs_tts_provider()

    class _Response:
        status_code = 401
        content = b""
        text = "unauthorized"

    await provider.client.aclose()
    provider.client = SimpleNamespace(post=_fake_post(_Response()), aclose=_fake_aclose())

    with pytest.raises(ElevenLabsTTSAPIError, match="HTTP 401"):
        await provider.get_audio("hello")
    await provider.terminate()


@pytest.mark.asyncio
async def test_mimo_tts_get_audio_handles_empty_choices():
    provider = _make_tts_provider()

    class _Response:
        status_code = 200
        text = '{"choices":[]}'

        def raise_for_status(self):
            return None

        def json(self):
            return {"choices": []}

    provider.client = SimpleNamespace(post=_fake_post(_Response()))

    with pytest.raises(MiMoAPIError, match="returned no audio payload"):
        await provider.get_audio("hello")


@pytest.mark.asyncio
async def test_mimo_stt_asr_model_payload_includes_audio_only(monkeypatch):
    provider = _make_stt_provider(
        {
            "mimo-stt-system-prompt": "system prompt",
            "mimo-stt-user-prompt": "user prompt",
        }
    )

    captured: dict = {}

    async def fake_prepare_audio_input(_audio_source: str):
        return MIMO_STT_TEST_AUDIO_DATA_URL, []

    class _Response:
        status_code = 200
        text = '{"choices":[{"message":{"content":"transcribed text"}}]}'

        def raise_for_status(self):
            return None

        def json(self):
            return {"choices": [{"message": {"content": "transcribed text"}}]}

    async def fake_post(_url, headers=None, json=None):
        captured["headers"] = headers
        captured["json"] = json
        return _Response()

    monkeypatch.setattr(
        "astrbot.core.provider.sources.mimo_stt_api_source.prepare_audio_input",
        fake_prepare_audio_input,
    )
    provider.client = SimpleNamespace(post=fake_post)

    result = await provider.get_text("/tmp/test.wav")

    assert result == "transcribed text"
    assert captured["json"]["messages"] == [
        {
            "role": "user",
            "content": [
                {
                    "type": "input_audio",
                    "input_audio": {
                        "data": MIMO_STT_TEST_AUDIO_DATA_URL,
                    },
                },
            ],
        },
    ]


def test_mimo_stt_default_model_is_v25_asr():
    provider = ProviderMiMoSTTAPI(
        provider_config={
            "id": "test-mimo-stt",
            "type": "mimo_stt_api",
            "api_key": "test-key",
        },
        provider_settings={},
    )
    try:
        assert provider.model_name == "mimo-v2.5-asr"
    finally:
        asyncio.run(provider.terminate())


@pytest.mark.asyncio
async def test_mimo_stt_multimodal_model_payload_includes_transcription_prompts(
    monkeypatch,
):
    provider = _make_stt_provider({"model": "mimo-v2.5"})

    captured: dict = {}

    async def fake_prepare_audio_input(_audio_source: str):
        return MIMO_STT_TEST_AUDIO_DATA_URL, []

    class _Response:
        status_code = 200
        text = '{"choices":[{"message":{"content":"transcribed text"}}]}'

        def raise_for_status(self):
            return None

        def json(self):
            return {"choices": [{"message": {"content": "transcribed text"}}]}

    async def fake_post(_url, headers=None, json=None):
        captured["headers"] = headers
        captured["json"] = json
        return _Response()

    monkeypatch.setattr(
        "astrbot.core.provider.sources.mimo_stt_api_source.prepare_audio_input",
        fake_prepare_audio_input,
    )
    provider.client = SimpleNamespace(post=fake_post)

    result = await provider.get_text("/tmp/test.wav")

    assert result == "transcribed text"
    assert captured["json"]["messages"] == [
        {
            "role": "system",
            "content": (
                "You are a speech transcription assistant. "
                "Transcribe the spoken content from the audio exactly "
                "and return only the transcription text."
            ),
        },
        {
            "role": "user",
            "content": [
                {
                    "type": "input_audio",
                    "input_audio": {
                        "data": MIMO_STT_TEST_AUDIO_DATA_URL,
                    },
                },
                {
                    "type": "text",
                    "text": (
                        "Please transcribe the content of the audio "
                        "and return only the transcription text."
                    ),
                },
            ],
        },
    ]


@pytest.mark.asyncio
async def test_mimo_stt_prepare_audio_input_returns_data_url(tmp_path):
    audio_path = tmp_path / "test.wav"
    audio_path.write_bytes(b"RIFF$\x00\x00\x00WAVEfmt " + b"\x00" * 16)

    audio_data, cleanup_paths = await prepare_audio_input(str(audio_path))

    assert audio_data.startswith("data:audio/wav;base64,")
    assert cleanup_paths == []


@pytest.mark.asyncio
async def test_mimo_stt_prepare_audio_input_rejects_non_wav_payload(tmp_path):
    audio_path = tmp_path / "test.wav"
    audio_path.write_bytes(b"\x02#!SILK_V3" + b"\x00" * 16)

    with pytest.raises(MiMoAPIError, match="SILK"):
        await prepare_audio_input(str(audio_path))


@pytest.mark.asyncio
async def test_mimo_stt_prepare_audio_input_converts_non_wav_file(
    monkeypatch,
    tmp_path,
):
    audio_path = tmp_path / "test.mp3"
    audio_path.write_bytes(b"ID3" + b"\x00" * 16)

    async def fake_convert_to_pcm_wav(input_path: str, output_path: str):
        assert input_path == str(audio_path)
        Path(output_path).write_bytes(MIMO_STT_TEST_WAV_HEADER + b"\x00" * 16)
        return output_path

    monkeypatch.setattr(
        "astrbot.core.provider.sources.mimo_api_common.convert_to_pcm_wav",
        fake_convert_to_pcm_wav,
    )

    audio_data, cleanup_paths = await prepare_audio_input(str(audio_path))

    assert audio_data.startswith("data:audio/wav;base64,")
    encoded = audio_data.removeprefix("data:audio/wav;base64,")
    assert base64.b64decode(encoded)[: len(MIMO_STT_TEST_WAV_HEADER)] == (
        MIMO_STT_TEST_WAV_HEADER
    )
    assert cleanup_paths
    assert cleanup_paths[0].suffix == ".wav"


def test_mimo_stt_wav_validation_accepts_unpadded_base64_header():
    wav_base64 = base64.b64encode(MIMO_STT_TEST_WAV_HEADER).decode().rstrip("=")

    _validate_wav_payload(wav_base64, "/tmp/test.wav")


@pytest.mark.asyncio
async def test_mimo_stt_get_text_uses_reasoning_content(monkeypatch):
    provider = _make_stt_provider()

    async def fake_prepare_audio_input(_audio_source: str):
        return MIMO_STT_TEST_AUDIO_DATA_URL, []

    class _Response:
        status_code = 200
        text = '{"choices":[{"message":{"content":"","reasoning_content":"转写结果"}}]}'

        def raise_for_status(self):
            return None

        def json(self):
            return {
                "choices": [
                    {"message": {"content": "", "reasoning_content": "转写结果"}}
                ]
            }

    monkeypatch.setattr(
        "astrbot.core.provider.sources.mimo_stt_api_source.prepare_audio_input",
        fake_prepare_audio_input,
    )
    provider.client = SimpleNamespace(post=_fake_post(_Response()))

    assert await provider.get_text("/tmp/test.wav") == "转写结果"


@pytest.mark.asyncio
async def test_mimo_stt_get_text_handles_empty_choices(monkeypatch):
    provider = _make_stt_provider()

    async def fake_prepare_audio_input(_audio_source: str):
        return MIMO_STT_TEST_AUDIO_DATA_URL, []

    class _Response:
        status_code = 200
        text = '{"choices":[]}'

        def raise_for_status(self):
            return None

        def json(self):
            return {"choices": []}

    monkeypatch.setattr(
        "astrbot.core.provider.sources.mimo_stt_api_source.prepare_audio_input",
        fake_prepare_audio_input,
    )
    provider.client = SimpleNamespace(post=_fake_post(_Response()))

    with pytest.raises(MiMoAPIError, match="returned empty transcription"):
        await provider.get_text("/tmp/test.wav")


@pytest.mark.asyncio
async def test_mimo_stt_get_text_handles_null_message(monkeypatch):
    provider = _make_stt_provider()

    async def fake_prepare_audio_input(_audio_source: str):
        return MIMO_STT_TEST_AUDIO_DATA_URL, []

    class _Response:
        status_code = 200
        text = '{"choices":[{"message":null}]}'

        def raise_for_status(self):
            return None

        def json(self):
            return {"choices": [{"message": None}]}

    monkeypatch.setattr(
        "astrbot.core.provider.sources.mimo_stt_api_source.prepare_audio_input",
        fake_prepare_audio_input,
    )
    provider.client = SimpleNamespace(post=_fake_post(_Response()))

    with pytest.raises(MiMoAPIError, match="returned empty transcription"):
        await provider.get_text("/tmp/test.wav")


def _fake_post(response):
    async def _post(*_args, **_kwargs):
        return response

    return _post


def _fake_aclose():
    async def _aclose():
        return None

    return _aclose
