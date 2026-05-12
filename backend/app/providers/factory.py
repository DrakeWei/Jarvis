from app.providers.base import BaseAdapter
from app.providers.openai_adapter import OpenAIAdapter


def create_client() -> BaseAdapter:
    return OpenAIAdapter()
