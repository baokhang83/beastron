import re

_PART = re.compile(r"(\d+)([hms])")


def parse_duration(text):
    """'1h30m' -> 5400 seconds. Units: h, m, s."""
    text = text.strip()
    if not text or not re.fullmatch(r"(\d+[hms])+", text):
        raise ValueError(f"invalid duration: {text!r}")
    seconds = 0
    for number, unit in _PART.findall(text):
        n = int(number)
        if unit == "h":
            seconds += n * 3600
        elif unit == "m":
            seconds += n * 60
        else:
            seconds += n
    return seconds
