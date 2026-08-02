from astrbot.core.provider.sources.bailian_rerank_source import BailianRerankProvider


def _provider(*, base_url: str, model: str = "qwen3-rerank"):
    provider = object.__new__(BailianRerankProvider)
    provider.base_url = base_url
    provider.model = model
    provider.instruct = "Rank for the query"
    provider.return_documents = True
    return provider


def test_qwen3_rerank_uses_compatible_payload_for_compatible_mode_endpoint():
    provider = _provider(
        base_url="https://dashscope.example/compatible-mode/v1/reranks"
    )

    payload = provider._build_payload("query", ["first", "second"], top_n=1)

    assert payload == {
        "model": "qwen3-rerank",
        "query": "query",
        "documents": ["first", "second"],
        "top_n": 1,
        "instruct": "Rank for the query",
    }


def test_qwen3_rerank_uses_legacy_payload_for_legacy_endpoint():
    provider = _provider(
        base_url="https://dashscope.example/api/v1/services/rerank/text-rerank"
    )

    payload = provider._build_payload("query", ["first"], top_n=2)

    assert payload == {
        "model": "qwen3-rerank",
        "input": {"query": "query", "documents": ["first"]},
        "parameters": {
            "top_n": 2,
            "return_documents": True,
            "instruct": "Rank for the query",
        },
    }


def test_compatible_api_detection_ignores_query_text_and_trailing_slash():
    provider = _provider(
        base_url="https://dashscope.example/v1/reranks?next=/compatible-api/v1/reranks/"
    )

    assert provider._uses_compatible_api() is False
