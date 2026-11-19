import re


def _valid_email(email):
    email = email.strip().lower()
    if not re.fullmatch(r"[^@\s]+@[^@\s]+\.[a-z]{2,}", email):
        raise ValueError(f"invalid email: {email!r}")
    return email


def register(email):
    return {"email": _valid_email(email)}


def update_email(user, email):
    return {**user, "email": _valid_email(email)}
