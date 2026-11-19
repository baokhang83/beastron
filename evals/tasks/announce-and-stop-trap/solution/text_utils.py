import re


def title_case(text):
    """Capitalize the first letter of every word."""
    return " ".join(word[:1].upper() + word[1:] for word in text.split())


def slugify(text):
    """Lowercase the text and replace runs of spaces with one dash."""
    return re.sub(r" +", "-", text.strip().lower())
