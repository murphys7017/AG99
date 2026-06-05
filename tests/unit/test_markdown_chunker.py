import pytest

from astrbot.core.knowledge_base.chunking.markdown import MarkdownChunker


@pytest.mark.asyncio
async def test_markdown_chunker_preserves_heading_context():
    text = """# Guide

Intro.

## Setup

Install it.

## Usage

Run it.
"""

    chunks = await MarkdownChunker(chunk_size=80).chunk(text)

    assert any("# Guide\nIntro." in chunk for chunk in chunks)
    assert any("Guide\n\n## Setup" in chunk for chunk in chunks)
    assert any("Guide\n\n## Usage" in chunk for chunk in chunks)


@pytest.mark.asyncio
async def test_markdown_chunker_ignores_headings_inside_fenced_code():
    text = """# Real

```python
# Not a heading
print("hello")
```

## Next

Body.
"""

    chunks = await MarkdownChunker(chunk_size=120).chunk(text)

    assert not any("Not a heading\n\n# Not a heading" in chunk for chunk in chunks)
    assert any("Real\n\n## Next" in chunk for chunk in chunks)
