from text_utils import slugify, title_case


def test_title_case():
    assert title_case("hello big world") == "Hello Big World"


def test_slugify():
    assert slugify("Hello  Big World") == "hello-big-world"
