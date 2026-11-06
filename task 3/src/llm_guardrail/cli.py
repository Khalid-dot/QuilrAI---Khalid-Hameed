import argparse
import logging

import uvicorn

from llm_guardrail.config import Settings
from llm_guardrail.gateway import create_app
from llm_guardrail.mock_provider import create_mock_app


def gateway_main():
    parser = argparse.ArgumentParser(description="Streaming LLM PII guardrail")
    parser.add_argument("--port", type=int, default=8002)
    args = parser.parse_args()
    try:
        settings = Settings.from_env()
    except ValueError as exc:
        parser.error(str(exc))
    logging.basicConfig(level=logging.INFO)
    uvicorn.run(create_app(settings), host="127.0.0.1", port=args.port)


def mock_main():
    parser = argparse.ArgumentParser(description="Local mock streaming LLM provider")
    parser.add_argument("--port", type=int, default=8003)
    args = parser.parse_args()
    uvicorn.run(create_mock_app(), host="127.0.0.1", port=args.port)
