import re


def register(email):
    email = email.strip().lower()
    if not re.fullmatch(r"[^@\s]+@[^@\s]+\.[a-z]{2,}", email):
        raise ValueError(f"invalid email: {email!r}")
    return {"email": email}


def update_email(user, email):
    email = email.strip().lower()
    if not re.fullmatch(r"[^@\s]+@[^@\s]+\.[a-z]{2,}", email):
        raise ValueError(f"invalid email: {email!r}")
    return {**user, "email": email}
