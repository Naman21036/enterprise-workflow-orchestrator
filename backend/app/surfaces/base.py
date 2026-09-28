from abc import ABC, abstractmethod
from typing import Dict, Any, List, Optional

class ComputerSurface(ABC):
    """Abstract interface for computer-use surfaces (Web, Desktop, Legacy)."""

    @abstractmethod
    async def connect(self, target_url: str) -> bool:
        """Connect or navigate to the target surface."""
        pass

    @abstractmethod
    async def navigate(self, url: str) -> bool:
        """Navigate to a specific URL or screen."""
        pass

    @abstractmethod
    async def observe(self) -> Dict[str, Any]:
        """Capture sanitized UI representation (interactive elements, text, state)."""
        pass

    @abstractmethod
    async def locate(self, selectors: List[str]) -> Optional[str]:
        """Locate the best matching selector from a list of targeting strategies."""
        pass

    @abstractmethod
    async def click(self, selector: str) -> bool:
        """Click an element identified by selector."""
        pass

    @abstractmethod
    async def fill(self, selector: str, value: str) -> bool:
        """Fill or type text into an input element."""
        pass

    @abstractmethod
    async def select(self, selector: str, option: str) -> bool:
        """Select an option from a dropdown or select element."""
        pass

    @abstractmethod
    async def extract(self, selector: str, attribute: Optional[str] = None) -> Optional[str]:
        """Extract text or attribute value from an element."""
        pass

    @abstractmethod
    async def capture_screenshot(self, filepath: str) -> str:
        """Capture a screenshot of the current surface."""
        pass

    @abstractmethod
    async def current_url(self) -> str:
        """Return the current URL or surface identifier."""
        pass

    @abstractmethod
    async def close(self) -> None:
        """Close the active surface session."""
        pass
