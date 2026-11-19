import json

from firox.quirks import TagRouter, parse_text_tool_call, safe_arguments


def route_all(chunks):
    router = TagRouter()
    out = []
    for chunk in chunks:
        out += router.feed(chunk)
    out += router.flush()
    merged = {}
    for channel, text in out:
        merged.setdefault(channel, []).append(text)
    return {k: "".join(v) if k != "tool_call" else v for k, v in merged.items()}


def test_think_tags_split_across_chunks():
    result = route_all(["Hel", "lo <thi", "nk>pondering", " more</th", "ink> world"])
    assert result == {"text": "Hello  world", "think": "pondering more"}


def test_text_tool_call_blocks():
    call = '{"name": "read_file", "arguments": {"path": "a.py"}}'
    result = route_all(["Let me look.\n<tool_", "call>", call[:10], call[10:], "</tool_call>"])
    assert result["text"] == "Let me look.\n"
    assert result["tool_call"] == [call]


def test_plain_text_with_angle_brackets_passes_through():
    assert route_all(["a < b and <div>x</div>"]) == {"text": "a < b and <div>x</div>"}


def test_parse_text_tool_call():
    call = parse_text_tool_call('{"name": "read_file", "arguments": {"path": "a.py"}}', 0)
    assert call.name == "read_file" and json.loads(call.arguments) == {"path": "a.py"}
    bad = parse_text_tool_call("not json", 1)
    assert bad.name == "invalid_tool_call"


def test_safe_arguments():
    assert safe_arguments('{"a": 1}') == '{"a": 1}'
    assert safe_arguments('{"a": ') == "{}"
    assert safe_arguments("") == "{}"
