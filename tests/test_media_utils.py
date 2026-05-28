import pytest

from astrbot.core.utils import media_utils


def test_get_audio_magic_type_detects_standard_silk(tmp_path):
    audio_path = tmp_path / "voice.silk"
    audio_path.write_bytes(b"#!SILK_V3" + b"\x00" * 16)

    assert media_utils._get_audio_magic_type(str(audio_path)) == "silk"


def test_get_audio_magic_type_detects_tencent_silk(tmp_path):
    audio_path = tmp_path / "voice.silk"
    audio_path.write_bytes(b"\x02#!SILK_V3" + b"\x00" * 16)

    assert media_utils._get_audio_magic_type(str(audio_path)) == "silk"


@pytest.mark.asyncio
async def test_ensure_wav_routes_silk_to_tencent_converter(monkeypatch, tmp_path):
    audio_path = tmp_path / "voice.silk"
    output_path = tmp_path / "voice.wav"
    audio_path.write_bytes(b"\x02#!SILK_V3" + b"\x00" * 16)
    captured = {}

    async def fake_tencent_silk_to_wav(source, target):
        captured["source"] = source
        captured["target"] = target
        return target

    monkeypatch.setattr(
        media_utils,
        "tencent_silk_to_wav",
        fake_tencent_silk_to_wav,
    )

    result = await media_utils.ensure_wav(str(audio_path), str(output_path))

    assert result == str(output_path)
    assert captured == {"source": str(audio_path), "target": str(output_path)}
