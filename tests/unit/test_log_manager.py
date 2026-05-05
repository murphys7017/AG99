from types import SimpleNamespace

from astrbot.core.log import _format_console_record, _format_file_record


def _make_loguru_record():
    return {
        "extra": {},
        "level": SimpleNamespace(name="INFO", no=20),
        "file": SimpleNamespace(path=__file__),
        "line": 12,
    }


def test_loguru_console_formatter_enriches_missing_astrbot_fields():
    record = _make_loguru_record()

    template = _format_console_record(record)

    assert "{extra[plugin_tag]}" in template
    assert record["extra"]["plugin_tag"] == "[Core]"
    assert record["extra"]["short_levelname"] == "INFO"
    assert record["extra"]["source_line"] == 12


def test_loguru_file_formatter_enriches_missing_astrbot_fields():
    record = _make_loguru_record()

    template = _format_file_record(record)

    assert "{extra[plugin_tag]}" in template
    assert record["extra"]["plugin_tag"] == "[Core]"
    assert record["extra"]["short_levelname"] == "INFO"
    assert record["extra"]["source_line"] == 12
