from dataclasses import dataclass, field
from typing import Any, Optional


@dataclass
class Item:
    code: str
    name: str
    category: str
    source: str
    event_time: str
    title: str
    summary: str = ""
    url: Optional[str] = None
    payload: dict[str, Any] = field(default_factory=dict)
    subcategory: str = ""
