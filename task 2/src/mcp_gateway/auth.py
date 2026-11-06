import hmac

from starlette.requests import Request


def authenticate(request: Request, tokens: dict[str, str]) -> str | None:
    headers = request.headers.getlist("authorization")
    if len(headers) != 1:
        return None
    parts = headers[0].split()
    if len(parts) != 2 or parts[0].lower() != "bearer" or not parts[1].isascii():
        return None
    for token, role in tokens.items():
        if hmac.compare_digest(parts[1], token):
            return role
    return None
