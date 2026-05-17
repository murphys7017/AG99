DEFAULT_MEMORY_ANALYZER_PROVIDER_ID = ""
DEFAULT_MEMORY_STANDARD_ANALYZER_NAMES = ("topic_v1", "focus_v1", "summary_v1")
DEFAULT_MEMORY_ADVANCED_ANALYZER_NAMES = (
    "session_insight_v1",
    "experience_extract_v1",
    "long_term_promote_v1",
    "long_term_compose_v1",
)
DEFAULT_MEMORY_KEYWORD_EXTRACTOR_IMPLEMENTATION = "jieba_tfidf"
DEFAULT_MEMORY_ANALYZER_SPECS: dict[str, tuple[str, str]] = {
    "topic_v1": ("topic_v1.md", "TopicStateResult"),
    "focus_v1": ("focus_v1.md", "ShortTermFocusResult"),
    "summary_v1": ("summary_v1.md", "ShortTermSummaryResult"),
    "session_insight_v1": ("session_insight_v1.md", "SessionInsightResult"),
    "experience_extract_v1": ("experience_extract_v1.md", "ExperienceExtractResult"),
    "long_term_promote_v1": ("long_term_promote_v1.md", "LongTermPromoteResult"),
    "long_term_compose_v1": ("long_term_compose_v1.md", "LongTermComposeResult"),
}
DEFAULT_MEMORY_ANALYSIS_STAGES: dict[str, list[str]] = {
    "short_term_update": ["topic_v1", "focus_v1", "summary_v1"],
    "session_insight_update": ["session_insight_v1"],
    "experience_extract": ["experience_extract_v1"],
    "long_term_promote": ["long_term_promote_v1"],
    "long_term_compose": ["long_term_compose_v1"],
}


def build_default_memory_config_payload() -> dict:
    return {
        "enabled": True,
        "identity": {
            "enabled": True,
            "mappings_path": "data/memory/identity_mappings.yaml",
        },
        "storage": {
            "sqlite_path": "data/memory/memory.db",
            "docs_root": "data/memory/long_term",
            "projections_root": "data/memory/projections",
        },
        "short_term": {
            "enabled": True,
            "recent_turns_window": 8,
        },
        "consolidation": {
            "enabled": True,
            "min_short_term_updates": 12,
            "batch_window_hours": 6,
        },
        "long_term": {
            "enabled": True,
            "min_experience_importance": 0.7,
            "min_pending_experiences": 3,
        },
        "vector_index": {
            "enabled": True,
            "provider": "faiss",
            "provider_id": "",
            "model": "",
            "root_dir": "data/memory/vector_index",
            "experience_top_k": 5,
            "long_term_top_k": 5,
        },
        "keyword_extraction": {
            "enabled": True,
            "implementation": DEFAULT_MEMORY_KEYWORD_EXTRACTOR_IMPLEMENTATION,
            "top_k": 12,
        },
        "persona": {
            "enabled": False,
            "reflection_interval_hours": 24,
        },
        "jobs": {
            "consolidation_enabled": True,
            "long_term_enabled": True,
            "persona_reflection_enabled": False,
        },
        "analysis": {
            "enabled": True,
            "strict": True,
            "standard_provider_id": DEFAULT_MEMORY_ANALYZER_PROVIDER_ID,
            "advanced_provider_id": DEFAULT_MEMORY_ANALYZER_PROVIDER_ID,
            "prompts_root": "data/memory/prompts",
            "analyzers": {
                analyzer_name: {
                    "enabled": True,
                    "implementation": "prompt_json",
                    "provider_id": DEFAULT_MEMORY_ANALYZER_PROVIDER_ID,
                    "prompt_file": prompt_file,
                    "output_schema": output_schema,
                    "timeout_seconds": 20,
                    "temperature": 0.0,
                    "extra_body": None,
                }
                for analyzer_name, (
                    prompt_file,
                    output_schema,
                ) in DEFAULT_MEMORY_ANALYZER_SPECS.items()
            },
            "stages": {
                stage_name: {
                    "analyzers": list(analyzer_names),
                }
                for stage_name, analyzer_names in DEFAULT_MEMORY_ANALYSIS_STAGES.items()
            },
        },
    }
