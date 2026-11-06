"""Small JSON-RPC envelope checks; forwarding retains the original request bytes."""

import json

from starlette.responses import JSONResponse


class InvalidEnvelope(ValueError):
    pass


def _unique_object(pairs):
    result = {}
    for key, value in pairs:
        if key in result:
            raise ValueError("Duplicate JSON member")
        result[key] = value
    return result


def _reject_constant(value):
    raise ValueError("Non-finite JSON number")


def decode_json(body: bytes):
    return json.loads(body, object_pairs_hook=_unique_object, parse_constant=_reject_constant)


def valid_id(value) -> bool:
    return value is None or type(value) in {str, int}


def validate_request(payload) -> None:
    if not isinstance(payload, dict) or payload.get("jsonrpc") != "2.0":
        raise InvalidEnvelope("Invalid Request")
    if not isinstance(payload.get("method"), str) or not payload["method"]:
        raise InvalidEnvelope("Invalid Request")
    if "id" in payload and not valid_id(payload["id"]):
        raise InvalidEnvelope("Invalid Request")
    if "params" in payload and not isinstance(payload["params"], dict):
        raise InvalidEnvelope("MCP params must be an object")
    # MCP tools/list and tools/call are requests, not notifications.
    if payload["method"] in {"tools/list", "tools/call"} and "id" not in payload:
        raise InvalidEnvelope("Tool requests require an id")


def rpc_error(request_id, code: int, message: str, status: int, *, headers=None):
    return JSONResponse(
        {"jsonrpc": "2.0", "id": request_id, "error": {"code": code, "message": message}},
        status_code=status,
        headers=headers,
    )


def valid_response(payload, request_id) -> bool:
    if not isinstance(payload, dict) or payload.get("jsonrpc") != "2.0":
        return False
    if "id" not in payload or type(payload["id"]) is not type(request_id):
        return False
    if payload["id"] != request_id or ("result" in payload) == ("error" in payload):
        return False
    if "error" in payload:
        error = payload["error"]
        return (
            isinstance(error, dict)
            and type(error.get("code")) is int
            and isinstance(error.get("message"), str)
        )
    return True
