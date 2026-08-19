from .base import Result, SyndicationError, Target
from .devto import DevTo
from .hashnode import Hashnode
from .medium import Medium
from .youtube import YouTube

REGISTRY = {
    t.name: t
    for t in (DevTo(), Hashnode(), Medium(), YouTube())
}

__all__ = ["REGISTRY", "Result", "SyndicationError", "Target"]
