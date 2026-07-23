from pathlib import Path

import pytest

from astrbot.core.message import components


@pytest.mark.asyncio
async def test_file_component_download_sanitizes_remote_name(monkeypatch, tmp_path):
    temp_dir = tmp_path / "temp"
    downloaded_paths: list[Path] = []

    async def fake_download_file(url: str, path: str) -> None:
        target = Path(path)
        assert url == "https://example.com/report"
        assert target.parent == temp_dir
        assert target.parent.exists()
        assert "\x00" not in target.name
        assert "/" not in target.name
        assert "\\" not in target.name
        assert not any(char in target.name for char in ':*?"<>|')
        target.write_bytes(b"payload")
        downloaded_paths.append(target)

    monkeypatch.setattr(components, "download_file", fake_download_file)
    monkeypatch.setattr(components, "get_astrbot_temp_path", lambda: str(temp_dir))

    component = components.File(
        name='..\\nested/evil\\report:*?"<>|\x00.pdf',
        url="https://example.com/report",
    )

    path = Path(await component.get_file())

    assert path.parent == temp_dir
    assert path.exists()
    assert path.name.startswith("fileseg_report________")
    assert path.suffix == ".pdf"
    assert downloaded_paths == [path]


@pytest.mark.asyncio
async def test_local_media_components_use_standard_file_uris(tmp_path):
    media_path = tmp_path / "media file.jpg"
    media_path.write_bytes(b"media")
    media_path_str = str(media_path)

    media_components = (
        components.Image.fromFileSystem(media_path_str),
        components.Record.fromFileSystem(media_path_str),
        components.Video.fromFileSystem(media_path_str),
    )
    for component in media_components:
        assert component.file == media_path.resolve().as_uri()
        assert "\\" not in component.file
        assert Path(await component.convert_to_file_path()) == media_path.resolve()
