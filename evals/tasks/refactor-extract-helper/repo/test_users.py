import pytest

from users import register, update_email


def test_register_lowercases():
    assert register(" Ada@Example.org ") == {"email": "ada@example.org"}


def test_update_email():
    assert update_email({"email": "a@b.co"}, "new@b.co") == {"email": "new@b.co"}


@pytest.mark.parametrize("bad", ["", "no-at-sign", "a@b", "a b@c.de"])
def test_invalid_emails_are_rejected(bad):
    with pytest.raises(ValueError):
        register(bad)
    with pytest.raises(ValueError):
        update_email({"email": "a@b.co"}, bad)
