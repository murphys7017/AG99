from astrbot.core.provider.sources.openai_embedding_source import _normalize_api_base


def test_normalize_api_base_appends_v1_when_missing():
    assert _normalize_api_base("https://example.com/openai") == (
        "https://example.com/openai/v1"
    )


def test_normalize_api_base_preserves_version_suffixes():
    assert _normalize_api_base("https://example.com/v1beta") == (
        "https://example.com/v1beta"
    )
    assert _normalize_api_base("https://example.com/v1alpha1/") == (
        "https://example.com/v1alpha1"
    )


def test_normalize_api_base_trims_embeddings_suffix_before_normalizing():
    assert _normalize_api_base("https://example.com/custom/embeddings") == (
        "https://example.com/custom/v1"
    )
