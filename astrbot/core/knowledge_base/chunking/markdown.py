"""Markdown-aware document chunker.

Split Markdown by heading hierarchy while keeping section context. Very large
sections fall back to recursive character splitting.
"""

import re
from dataclasses import dataclass

from .base import BaseChunker
from .recursive import RecursiveCharacterChunker


@dataclass
class _Section:
    heading_path: list[str]
    text: str
    has_body: bool


class MarkdownChunker(BaseChunker):
    """Split Markdown documents by heading hierarchy."""

    def __init__(
        self,
        chunk_size: int = 1024,
        chunk_overlap: int = 50,
        include_heading_context: bool = True,
        max_heading_depth: int = 4,
        min_chunk_size: int = 0,
        continuation_prefix: str = "...",
    ) -> None:
        self.chunk_size = chunk_size
        self.chunk_overlap = chunk_overlap
        self.include_heading_context = include_heading_context
        self.max_heading_depth = max(1, min(int(max_heading_depth), 6))
        self.min_chunk_size = min_chunk_size
        self.continuation_prefix = continuation_prefix
        self._fallback_chunker = RecursiveCharacterChunker(
            chunk_size=chunk_size,
            chunk_overlap=chunk_overlap,
        )

    async def chunk(self, text: str, **kwargs) -> list[str]:
        if not text or not text.strip():
            return []

        chunk_size = kwargs.get("chunk_size", self.chunk_size)
        chunk_overlap = kwargs.get("chunk_overlap", self.chunk_overlap)
        sections = self._parse_sections(text)

        if not sections:
            return await self._fallback_chunker.chunk(
                text,
                chunk_size=chunk_size,
                chunk_overlap=chunk_overlap,
            )

        raw_chunks = await self._sections_to_chunks(sections, chunk_size, chunk_overlap)
        merged = self._merge_heading_only_chunks(raw_chunks, chunk_size)
        return self._merge_short_chunks(merged, chunk_size)

    def _estimate_prefix_length(self, heading_path: list[str]) -> int:
        if not self.include_heading_context or not heading_path:
            return 0
        title = " > ".join(heading_path)
        return len(f"{self.continuation_prefix} {title}\n\n")

    async def _sections_to_chunks(
        self,
        sections: list[_Section],
        chunk_size: int,
        chunk_overlap: int,
    ) -> list[tuple[str, bool]]:
        raw_chunks: list[tuple[str, bool]] = []

        for section in sections:
            section_text = section.text
            heading_path = section.heading_path
            full_text = self._build_context_prefix(heading_path) + section_text

            if len(full_text) <= chunk_size:
                raw_chunks.append((full_text.strip(), section.has_body))
                continue

            prefix_len = self._estimate_prefix_length(heading_path)
            effective_chunk_size = max(chunk_size // 4, chunk_size - prefix_len)
            sub_chunks = await self._fallback_chunker.chunk(
                section_text,
                chunk_size=effective_chunk_size,
                chunk_overlap=chunk_overlap,
            )
            for index, sub_chunk in enumerate(sub_chunks):
                raw_chunks.append(
                    (
                        self._apply_heading_context(
                            heading_path,
                            sub_chunk,
                            is_continuation=index > 0,
                        ),
                        True,
                    )
                )

        return raw_chunks

    def _build_context_prefix(self, heading_path: list[str]) -> str:
        if self.include_heading_context and heading_path:
            return " > ".join(heading_path) + "\n\n"
        return ""

    def _apply_heading_context(
        self,
        heading_path: list[str],
        content: str,
        is_continuation: bool,
    ) -> str:
        if not self.include_heading_context or not heading_path:
            return content.strip()

        title = " > ".join(heading_path)
        if is_continuation:
            return f"{self.continuation_prefix} {title}\n\n{content}".strip()
        return f"{title}\n\n{content}".strip()

    def _merge_heading_only_chunks(
        self,
        raw_chunks: list[tuple[str, bool]],
        chunk_size: int,
    ) -> list[str]:
        merged: list[str] = []
        pending = ""

        for chunk_text, has_body in raw_chunks:
            if not chunk_text:
                continue
            if not has_body:
                if pending and len(pending) + len(chunk_text) + 2 > chunk_size:
                    merged.append(pending.strip())
                    pending = ""
                pending += chunk_text + "\n\n"
                continue

            if pending:
                combined = pending + chunk_text
                if len(combined) <= chunk_size:
                    merged.append(combined.strip())
                else:
                    merged.append(pending.strip())
                    merged.append(chunk_text.strip())
                pending = ""
            else:
                merged.append(chunk_text.strip())

        if pending:
            pending_text = pending.strip()
            if merged and len(merged[-1] + "\n\n" + pending_text) <= chunk_size:
                merged[-1] = merged[-1] + "\n\n" + pending_text
            else:
                merged.append(pending_text)

        return [chunk for chunk in merged if chunk.strip()]

    def _merge_short_chunks(self, chunks: list[str], chunk_size: int) -> list[str]:
        if self.min_chunk_size <= 0 or len(chunks) <= 1:
            return chunks

        final: list[str] = []
        buffer = ""

        for chunk in chunks:
            if buffer:
                combined = buffer + "\n\n" + chunk
                if len(combined) <= chunk_size:
                    buffer = combined
                    continue
                final.append(buffer)
                buffer = chunk if len(chunk) < self.min_chunk_size else ""
                if len(chunk) >= self.min_chunk_size:
                    final.append(chunk)
            elif len(chunk) < self.min_chunk_size:
                buffer = chunk
            else:
                final.append(chunk)

        if buffer:
            if final and len(final[-1] + "\n\n" + buffer) <= chunk_size:
                final[-1] = final[-1] + "\n\n" + buffer
            else:
                final.append(buffer)

        return final

    def _parse_sections(self, text: str) -> list[_Section]:
        fenced_ranges = self._find_fenced_code_ranges(text)
        heading_pattern = re.compile(
            r"^(#{1," + str(self.max_heading_depth) + r"})\s*(.+)$",
            re.MULTILINE,
        )

        headings = []
        for match in heading_pattern.finditer(text):
            if self._is_in_fenced_block(match.start(), fenced_ranges):
                continue
            headings.append(
                {
                    "level": len(match.group(1)),
                    "title": match.group(2).strip(),
                    "start": match.start(),
                    "end": match.end(),
                }
            )

        if not headings:
            return []

        sections: list[_Section] = []
        preamble = text[: headings[0]["start"]].strip()
        if preamble:
            sections.append(_Section(heading_path=[], text=preamble, has_body=True))

        heading_stack: list[dict] = []
        for index, heading in enumerate(headings):
            while heading_stack and heading_stack[-1]["level"] >= heading["level"]:
                heading_stack.pop()
            heading_stack.append({"level": heading["level"], "title": heading["title"]})

            content_start = heading["end"]
            content_end = (
                headings[index + 1]["start"] if index + 1 < len(headings) else len(text)
            )
            heading_line = text[heading["start"] : heading["end"]]
            body = text[content_start:content_end].strip()
            section_text = heading_line + ("\n" + body if body else "")
            heading_path = [item["title"] for item in heading_stack[:-1]]

            sections.append(
                _Section(
                    heading_path=heading_path,
                    text=section_text,
                    has_body=bool(body),
                )
            )

        return sections

    @staticmethod
    def _find_fenced_code_ranges(text: str) -> list[tuple[int, int]]:
        ranges: list[tuple[int, int]] = []
        fence_pattern = re.compile(r"^(`{3,}|~{3,})", re.MULTILINE)
        matches = list(fence_pattern.finditer(text))

        index = 0
        while index < len(matches):
            open_match = matches[index]
            open_fence = open_match.group(1)
            fence_char = open_fence[0]
            fence_len = len(open_fence)

            for close_index in range(index + 1, len(matches)):
                close_match = matches[close_index]
                close_fence = close_match.group(1)
                if close_fence[0] == fence_char and len(close_fence) >= fence_len:
                    ranges.append((open_match.start(), close_match.end()))
                    index = close_index + 1
                    break
            else:
                ranges.append((open_match.start(), len(text)))
                break

        return ranges

    @staticmethod
    def _is_in_fenced_block(pos: int, ranges: list[tuple[int, int]]) -> bool:
        return any(start <= pos < end for start, end in ranges)
