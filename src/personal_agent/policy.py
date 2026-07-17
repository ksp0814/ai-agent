from collections.abc import Callable
from typing import Optional


class Policy:
    """Central approval boundary for state-changing operations."""

    def __init__(self, approve: Optional[Callable[[str], bool]] = None):
        self.approve = approve or (lambda _description: False)

    def authorize(self, description: str) -> bool:
        return self.approve(description)
