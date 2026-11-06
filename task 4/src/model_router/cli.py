import argparse
import logging

import uvicorn

from model_router.config import Settings
from model_router.gateway import create_app
from model_router.mock_provider import create_mock_app
from model_router.tokens import TokenCounter


def gateway_main():
    parser = argparse.ArgumentParser(description="SQLite token limiter and model fallback gateway")
    parser.add_argument(
        "--demo", action="store_true", help="Enable explicit local demo tenant keys"
    )
    parser.add_argument("--port", type=int, default=8004)
    parser.add_argument("--db", help="Path to the on-disk SQLite database")
    args = parser.parse_args()
    try:
        settings = Settings.from_env(demo=args.demo, database=args.db)
        counters = (
            TokenCounter(settings.primary.encoding),
            TokenCounter(settings.secondary.encoding),
        )
    except ValueError as exc:
        parser.error(str(exc))
    except Exception:
        parser.error("Tokenizer unavailable; run python -m model_router.prepare before startup")
    logging.basicConfig(level=logging.INFO)
    uvicorn.run(create_app(settings, counters=counters), host="127.0.0.1", port=args.port)


def mock_main():
    parser = argparse.ArgumentParser(description="Local mock completion provider")
    parser.add_argument("--role", choices=["primary", "secondary"], required=True)
    parser.add_argument("--port", type=int)
    args = parser.parse_args()
    uvicorn.run(
        create_mock_app(args.role),
        host="127.0.0.1",
        port=args.port or (8005 if args.role == "primary" else 8006),
    )
