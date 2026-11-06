"""Public errors carry fixed messages, never upstream exception strings."""


class GatewayError(Exception):
    def __init__(self, code: str, message: str, status: int, *, retry_after: int | None = None):
        super().__init__(message)
        self.code = code
        self.message = message
        self.status = status
        self.retry_after = retry_after
