import math
import struct
import sys
import wave
from pathlib import Path

import pytest

from astrbot.core.utils import media_utils
from astrbot.core.utils.tencent_record_helper import wav_to_tencent_silk


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


def test_detect_image_mime_type_accepts_path(tmp_path):
    from PIL import Image as PILImage

    image_path = tmp_path / "image.png"
    PILImage.new("RGBA", (1, 1), (255, 0, 0, 255)).save(image_path)

    assert (
        media_utils.detect_image_mime_type(image_path, default_mime_type=None)
        == "image/png"
    )


@pytest.mark.asyncio
async def test_compress_image_preserves_alpha_png(tmp_path, monkeypatch):
    from PIL import Image as PILImage

    temp_dir = tmp_path / "temp"
    monkeypatch.setattr(media_utils, "get_astrbot_temp_path", lambda: str(temp_dir))
    image_path = tmp_path / "transparent.png"
    PILImage.new("RGBA", (8, 8), (255, 0, 0, 128)).save(image_path)

    compressed_path = Path(await media_utils.compress_image(str(image_path), max_size=2))

    try:
        assert compressed_path != image_path
        assert compressed_path.suffix == ".png"
        assert compressed_path.parent == temp_dir
        with PILImage.open(compressed_path) as compressed_img:
            assert compressed_img.format == "PNG"
            assert compressed_img.mode == "RGBA"
            assert max(compressed_img.size) <= 2
            assert compressed_img.getpixel((0, 0))[3] == 128
    finally:
        compressed_path.unlink(missing_ok=True)


@pytest.mark.asyncio
async def test_compress_image_keeps_animated_gif(tmp_path, monkeypatch):
    from PIL import Image as PILImage

    temp_dir = tmp_path / "temp"
    monkeypatch.setattr(media_utils, "get_astrbot_temp_path", lambda: str(temp_dir))
    image_path = tmp_path / "animated.gif"
    PILImage.new("RGB", (8, 8), (255, 0, 0)).save(
        image_path,
        format="GIF",
        save_all=True,
        append_images=[PILImage.new("RGB", (8, 8), (0, 0, 255))],
        duration=100,
        loop=0,
    )

    compressed_path = await media_utils.compress_image(str(image_path), max_size=2)

    assert compressed_path == str(image_path)
    assert list(temp_dir.iterdir()) == []


def _make_wav(path, rate, channels=1, secs=0.2, freq=440):
    nframes = int(rate * secs)
    with wave.open(str(path), "wb") as wav:
        wav.setnchannels(channels)
        wav.setsampwidth(2)
        wav.setframerate(rate)
        for i in range(nframes):
            sample = int(0.2 * 32767 * math.sin(2 * math.pi * freq * i / rate))
            for _ in range(channels):
                wav.writeframesraw(struct.pack("<h", sample))


class _FakePysilk:
    def __init__(self):
        self.calls = []

    def encode(self, input_io, output_io, sample_rate, bit_rate, tencent=True):
        self.calls.append(
            {
                "payload": input_io.read(),
                "sample_rate": sample_rate,
                "bit_rate": bit_rate,
                "tencent": tencent,
            }
        )
        output_io.write(b"\x02#!SILK_V3")


@pytest.mark.asyncio
async def test_wav_to_tencent_silk_resamples_unsupported_rate(tmp_path, monkeypatch):
    fake = _FakePysilk()
    monkeypatch.setitem(sys.modules, "pysilk", fake)
    wav_path = tmp_path / "tts_44100.wav"
    _make_wav(wav_path, 44100)

    silk_path = tmp_path / "out.silk"
    await wav_to_tencent_silk(str(wav_path), str(silk_path))

    assert len(fake.calls) == 1
    assert fake.calls[0]["sample_rate"] == 24000
    assert fake.calls[0]["bit_rate"] == 24000
    assert fake.calls[0]["tencent"] is True
    assert silk_path.read_bytes().startswith(b"\x02#!SILK_V3")


@pytest.mark.asyncio
async def test_wav_to_tencent_silk_downmixes_stereo(tmp_path, monkeypatch):
    fake = _FakePysilk()
    monkeypatch.setitem(sys.modules, "pysilk", fake)
    wav_path = tmp_path / "stereo_48k.wav"
    _make_wav(wav_path, 48000, channels=2)

    await wav_to_tencent_silk(str(wav_path), str(tmp_path / "out.silk"))

    assert len(fake.calls) == 1
    assert fake.calls[0]["sample_rate"] == 48000
