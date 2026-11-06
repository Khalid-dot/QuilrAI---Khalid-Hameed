"""Preload tokenizer data once, so request handling never needs a tokenizer download."""

from model_router.tokens import TokenCounter

if __name__ == "__main__":
    counter = TokenCounter()
    print(f"Tokenizer ready: {counter.encoding.name}; test tokens: {counter.count('Hello world')}")
