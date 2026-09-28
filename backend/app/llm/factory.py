from typing import Optional
from backend.app.llm.base import LLMClient
from backend.app.llm.mistral import MistralLLMClient

class LLMFactory:
    _instance: Optional[LLMClient] = None

    @classmethod
    def get_client(cls) -> LLMClient:
        if cls._instance is None:
            cls._instance = MistralLLMClient()
        return cls._instance

    @classmethod
    def set_client(cls, client: Optional[LLMClient]) -> None:
        cls._instance = client
