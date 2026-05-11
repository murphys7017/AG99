from __future__ import annotations

from pathlib import Path

from astrbot.core.config.astrbot_config import AstrBotConfig
from astrbot.core.config.default import DEFAULT_CONFIG
from astrbot.core.memory.config import (
    DEFAULT_MEMORY_ANALYZER_MODEL,
    DEFAULT_MEMORY_ANALYZER_PROMPTS,
    DEFAULT_MEMORY_ANALYZER_PROVIDER_ID,
    _build_default_memory_config,
    build_default_memory_config_payload,
    ensure_memory_config_file,
    get_memory_config,
    load_memory_config,
    reset_memory_config,
)


def test_ensure_memory_config_file_creates_default_yaml(temp_dir: Path):
    config_path = temp_dir / "memory" / "config.yaml"

    written_path = ensure_memory_config_file(config_path)

    assert written_path == config_path
    assert config_path.exists()
    content = config_path.read_text(encoding="utf-8")
    assert "storage:" in content
    assert "sqlite_path: data/memory/memory.db" in content
    assert "vector_index:" in content
    assert "analysis:" in content
    assert "prompts_root: data/memory/prompts" in content
    assert "keyword_extraction:" in content
    assert "topic_v1:" in content
    assert "focus_v1:" in content
    assert "summary_v1:" in content
    assert "session_insight_v1:" in content
    assert "experience_extract_v1:" in content
    assert "long_term_promote_v1:" in content
    assert "long_term_compose_v1:" in content
    assert "provider_id:" in content
    assert "model:" in content


def test_load_memory_config_creates_missing_file_and_uses_defaults(
    temp_dir: Path,
    monkeypatch,
):
    monkeypatch.setenv("ASTRBOT_ROOT", str(temp_dir / "runtime-root"))
    config_path = temp_dir / "runtime" / "config.yaml"

    config = load_memory_config(config_path)

    assert config_path.exists()
    assert config.enabled is True
    assert config.storage.sqlite_path == (
        temp_dir / "runtime-root" / "data/memory/memory.db"
    )
    assert config.storage.docs_root == (
        temp_dir / "runtime-root" / "data/memory/long_term"
    )
    assert config.storage.projections_root == (
        temp_dir / "runtime-root" / "data/memory/projections"
    )
    assert config.vector_index.root_dir == (
        temp_dir / "runtime-root" / "data/memory/vector_index"
    )
    assert config.storage.docs_root.exists()
    assert config.storage.projections_root.exists()
    assert config.analysis.prompts_root == (
        temp_dir / "runtime-root" / "data/memory/prompts"
    )
    assert config.analysis.prompts_root.exists()
    assert config.analysis.enabled is True
    for prompt_name in DEFAULT_MEMORY_ANALYZER_PROMPTS:
        assert (config.analysis.prompts_root / prompt_name).exists()
    assert config.vector_index.enabled is True
    assert config.keyword_extraction.enabled is True
    assert config.keyword_extraction.implementation == "jieba_tfidf"
    assert config.keyword_extraction.top_k == 12
    assert (
        config.analysis.analyzers["topic_v1"].provider_id
        == DEFAULT_MEMORY_ANALYZER_PROVIDER_ID
    )
    assert config.analysis.analyzers["topic_v1"].model == DEFAULT_MEMORY_ANALYZER_MODEL


def test_default_memory_analyzer_prompts_include_score_ranges():
    experience_prompt = DEFAULT_MEMORY_ANALYZER_PROMPTS["experience_extract_v1.md"]
    compose_prompt = DEFAULT_MEMORY_ANALYZER_PROMPTS["long_term_compose_v1.md"]

    assert "- importance: float between 0 and 1" in experience_prompt
    assert "- confidence: float between 0 and 1" in experience_prompt
    assert "- importance: float between 0 and 1" in compose_prompt
    assert "- confidence: float between 0 and 1" in compose_prompt


def test_load_memory_config_reads_explicit_values(temp_dir: Path, monkeypatch):
    monkeypatch.setenv("ASTRBOT_ROOT", str(temp_dir / "astrbot-root"))
    config_path = temp_dir / "memory-config.yaml"
    config_path.write_text(
        "\n".join(
            [
                "enabled: false",
                "storage:",
                '  sqlite_path: "custom/memory.sqlite3"',
                '  docs_root: "custom/long_term"',
                '  projections_root: "custom/projections"',
                "short_term:",
                "  enabled: false",
                "  recent_turns_window: 16",
                "consolidation:",
                "  min_short_term_updates: 20",
                "vector_index:",
                "  enabled: false",
                '  provider_id: "embed-lite"',
                '  model: "embedding-model"',
                '  root_dir: "custom/vector_index"',
                "  experience_top_k: 9",
                "keyword_extraction:",
                "  enabled: false",
                '  implementation: "jieba_tfidf"',
                "  top_k: 5",
                "analysis:",
                "  enabled: true",
                "  strict: true",
                '  prompts_root: "custom/prompts"',
                "  analyzers:",
                "    emotion_v1:",
                '      implementation: "prompt_json"',
                '      provider_id: "memory-lite"',
                '      prompt_file: "emotion_v1.md"',
                '      output_schema: "EmotionResult"',
                "      timeout_seconds: 11",
                "      temperature: 0.3",
                "  stages:",
                "    short_term_update:",
                '      analyzers: ["emotion_v1"]',
            ]
        ),
        encoding="utf-8",
    )

    config = load_memory_config(config_path)

    assert config.enabled is False
    assert config.storage.sqlite_path == (
        temp_dir / "astrbot-root" / "custom/memory.sqlite3"
    )
    assert config.storage.docs_root == (temp_dir / "astrbot-root" / "custom/long_term")
    assert config.storage.projections_root == (
        temp_dir / "astrbot-root" / "custom/projections"
    )
    assert config.short_term.enabled is False
    assert config.short_term.recent_turns_window == 16
    assert config.consolidation.min_short_term_updates == 20
    assert config.vector_index.enabled is False
    assert config.vector_index.provider_id == "embed-lite"
    assert config.vector_index.model == "embedding-model"
    assert config.vector_index.root_dir == (
        temp_dir / "astrbot-root" / "custom/vector_index"
    )
    assert config.vector_index.experience_top_k == 9
    assert config.keyword_extraction.enabled is False
    assert config.keyword_extraction.implementation == "jieba_tfidf"
    assert config.keyword_extraction.top_k == 5
    assert config.analysis.enabled is True
    assert config.analysis.strict is True
    assert config.analysis.prompts_root == (
        temp_dir / "astrbot-root" / "custom/prompts"
    )
    assert "emotion_v1" in config.analysis.analyzers
    assert config.analysis.analyzers["emotion_v1"].provider_id == "memory-lite"
    assert config.analysis.analyzers["emotion_v1"].prompt_file == "emotion_v1.md"
    assert config.analysis.stages["short_term_update"].analyzers == ["emotion_v1"]


def test_build_default_memory_config_payload_contains_expected_sections():
    payload = build_default_memory_config_payload()
    default_config = _build_default_memory_config()

    assert set(payload) == {
        "enabled",
        "identity",
        "storage",
        "short_term",
        "consolidation",
        "long_term",
        "vector_index",
        "keyword_extraction",
        "persona",
        "jobs",
        "analysis",
    }
    assert payload["enabled"] is default_config.enabled
    assert payload["storage"]["sqlite_path"] == "data/memory/memory.db"
    assert payload["storage"]["docs_root"] == "data/memory/long_term"
    assert payload["storage"]["projections_root"] == "data/memory/projections"
    assert payload["identity"]["mappings_path"] == "data/memory/identity_mappings.yaml"
    assert payload["vector_index"]["root_dir"] == "data/memory/vector_index"
    assert payload["keyword_extraction"]["enabled"] is True
    assert payload["keyword_extraction"]["implementation"] == "jieba_tfidf"
    assert payload["keyword_extraction"]["top_k"] == 12
    assert payload["analysis"]["prompts_root"] == "data/memory/prompts"
    assert (
        payload["short_term"]["recent_turns_window"]
        == default_config.short_term.recent_turns_window
    )
    assert (
        payload["consolidation"]["min_short_term_updates"]
        == default_config.consolidation.min_short_term_updates
    )
    assert (
        payload["long_term"]["min_pending_experiences"]
        == default_config.long_term.min_pending_experiences
    )
    assert (
        payload["vector_index"]["experience_top_k"]
        == default_config.vector_index.experience_top_k
    )
    assert payload["analysis"]["analyzers"]["topic_v1"]["prompt_file"] == "topic_v1.md"
    assert (
        payload["analysis"]["analyzers"]["topic_v1"]["provider_id"]
        == DEFAULT_MEMORY_ANALYZER_PROVIDER_ID
    )
    assert (
        payload["analysis"]["analyzers"]["topic_v1"]["model"]
        == DEFAULT_MEMORY_ANALYZER_MODEL
    )
    assert payload["analysis"]["analyzers"]["focus_v1"]["prompt_file"] == "focus_v1.md"
    assert (
        payload["analysis"]["analyzers"]["summary_v1"]["prompt_file"] == "summary_v1.md"
    )
    assert (
        payload["analysis"]["analyzers"]["session_insight_v1"]["prompt_file"]
        == "session_insight_v1.md"
    )
    assert (
        payload["analysis"]["analyzers"]["experience_extract_v1"]["prompt_file"]
        == "experience_extract_v1.md"
    )
    assert (
        payload["analysis"]["analyzers"]["long_term_promote_v1"]["prompt_file"]
        == "long_term_promote_v1.md"
    )
    assert (
        payload["analysis"]["analyzers"]["long_term_compose_v1"]["prompt_file"]
        == "long_term_compose_v1.md"
    )
    assert payload["vector_index"]["enabled"] is True
    assert payload["vector_index"]["provider"] == "faiss"
    assert payload["vector_index"]["provider_id"] == ""
    assert payload["analysis"]["stages"]["short_term_update"]["analyzers"] == [
        "topic_v1",
        "focus_v1",
        "summary_v1",
    ]
    assert payload["analysis"]["stages"]["session_insight_update"]["analyzers"] == [
        "session_insight_v1",
    ]
    assert payload["analysis"]["stages"]["experience_extract"]["analyzers"] == [
        "experience_extract_v1",
    ]
    assert payload["analysis"]["stages"]["long_term_promote"]["analyzers"] == [
        "long_term_promote_v1",
    ]
    assert payload["analysis"]["stages"]["long_term_compose"]["analyzers"] == [
        "long_term_compose_v1",
    ]


def test_astrbot_config_migrates_legacy_memory_yaml_values(
    temp_dir: Path,
    monkeypatch,
):
    monkeypatch.setenv("ASTRBOT_ROOT", str(temp_dir))
    legacy_config_path = temp_dir / "data" / "memory" / "config.yaml"
    legacy_config_path.parent.mkdir(parents=True)
    legacy_config_path.write_text(
        "\n".join(
            [
                "enabled: true",
                "analysis:",
                "  enabled: true",
                "  strict: true",
                "  prompts_root: data/memory/prompts",
                "  analyzers:",
                "    topic_v1:",
                "      enabled: true",
                "      implementation: prompt_json",
                "      provider_id: volcengine_ark/Doubao-Seed-2.0-lite",
                "      model: ep-20260307170657-rq64x",
                "      prompt_file: topic_v1.md",
                "      output_schema: TopicStateResult",
                "      timeout_seconds: 20",
                "      temperature: 0.0",
                "      extra_body:",
                "        thinking:",
                "          type: disabled",
                "  stages:",
                "    short_term_update:",
                "      analyzers:",
                "        - topic_v1",
            ]
        ),
        encoding="utf-8",
    )
    main_config_path = temp_dir / "data" / "cmd_config.json"
    main_config_path.parent.mkdir(parents=True, exist_ok=True)
    main_config = {
        key: value for key, value in DEFAULT_CONFIG.items() if key != "memory"
    }
    main_config_path.write_text(
        __import__("json").dumps(main_config, ensure_ascii=False),
        encoding="utf-8",
    )

    config = AstrBotConfig(config_path=str(main_config_path))

    analyzer_config = config["memory"]["analysis"]["analyzers"]["topic_v1"]
    assert analyzer_config["provider_id"] == "volcengine_ark/Doubao-Seed-2.0-lite"
    assert analyzer_config["model"] == "ep-20260307170657-rq64x"
    assert analyzer_config["extra_body"]["thinking"]["type"] == "disabled"


def test_astrbot_config_migrates_legacy_memory_yaml_when_main_config_is_created(
    temp_dir: Path,
    monkeypatch,
):
    monkeypatch.setenv("ASTRBOT_ROOT", str(temp_dir))
    legacy_config_path = temp_dir / "data" / "memory" / "config.yaml"
    legacy_config_path.parent.mkdir(parents=True)
    legacy_config_path.write_text(
        "\n".join(
            [
                "enabled: true",
                "analysis:",
                "  analyzers:",
                "    topic_v1:",
                "      provider_id: memory-lite",
                "      model: memory-model",
            ]
        ),
        encoding="utf-8",
    )
    main_config_path = temp_dir / "data" / "cmd_config.json"

    config = AstrBotConfig(config_path=str(main_config_path))

    analyzer_config = config["memory"]["analysis"]["analyzers"]["topic_v1"]
    assert analyzer_config["provider_id"] == "memory-lite"
    assert analyzer_config["model"] == "memory-model"


def test_astrbot_config_does_not_migrate_legacy_memory_into_schema_config(
    temp_dir: Path,
    monkeypatch,
):
    monkeypatch.setenv("ASTRBOT_ROOT", str(temp_dir))
    legacy_config_path = temp_dir / "data" / "memory" / "config.yaml"
    legacy_config_path.parent.mkdir(parents=True)
    legacy_config_path.write_text("enabled: true\n", encoding="utf-8")
    schema_config_path = temp_dir / "data" / "plugin_config.json"
    schema_config_path.parent.mkdir(parents=True, exist_ok=True)

    config = AstrBotConfig(
        config_path=str(schema_config_path),
        schema={
            "enabled": {
                "type": "bool",
                "default": True,
            },
        },
    )

    assert config["enabled"] is True
    assert "memory" not in config


def test_get_memory_config_uses_current_json_config_payload(temp_dir: Path):
    reset_memory_config()
    try:
        current_config = build_default_memory_config_payload()
        current_config["storage"]["sqlite_path"] = str(temp_dir / "current.db")
        current_config["enabled"] = False

        loaded = get_memory_config({"memory": current_config})

        assert loaded.enabled is False
        assert loaded.storage.sqlite_path == temp_dir / "current.db"
    finally:
        reset_memory_config()
