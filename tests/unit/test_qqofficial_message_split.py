from astrbot.api.event import MessageChain
from astrbot.api.message_components import File, Image, Plain, Record, Video
from astrbot.core.platform.sources.qqofficial.qqofficial_message_event import (
    QQOfficialMessageEvent,
)


def test_split_message_chain_by_media_preserves_metadata():
    message = MessageChain(
        [
            Plain("before"),
            Image.fromURL("https://example.com/a.png"),
            Plain("middle"),
            Record.fromURL("https://example.com/a.wav"),
            Plain("after"),
        ],
        type="reasoning",
    )
    message.use_markdown(False)
    message.use_t2i(True)

    chunks = QQOfficialMessageEvent._split_message_chain_by_media(message)

    assert [[type(component) for component in chunk.chain] for chunk in chunks] == [
        [Plain, Image, Plain],
        [Record, Plain],
    ]
    assert all(chunk.use_markdown_ is False for chunk in chunks)
    assert all(chunk.use_t2i_ is True for chunk in chunks)
    assert all(chunk.type == "reasoning" for chunk in chunks)


def test_split_message_chain_by_media_limits_one_media_per_chunk():
    message = MessageChain(
        [
            Plain("a"),
            Image.fromURL("https://example.com/a.png"),
            Video.fromURL("https://example.com/a.mp4"),
            File(name="a.txt", file="https://example.com/a.txt"),
            Plain("b"),
        ],
    )

    chunks = QQOfficialMessageEvent._split_message_chain_by_media(message)
    media_types = (Image, Record, Video, File)

    assert len(chunks) == 3
    assert [sum(isinstance(component, media_types) for component in chunk.chain) for chunk in chunks] == [
        1,
        1,
        1,
    ]
