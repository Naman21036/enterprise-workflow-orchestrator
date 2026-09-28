from abc import ABC, abstractmethod
from typing import Dict, Any, Optional

class LLMClient(ABC):
    """Abstract client for LLM providers."""

    @abstractmethod
    async def generate_structured(
        self,
        system_prompt: str,
        user_prompt: str,
        response_schema: Optional[type] = None
    ) -> Dict[str, Any]:
        """Generate structured response from model."""
        pass
