"""Compatibility facade for domain-owned Pydantic contracts.

New code should import from ``app.contracts`` modules when it needs a narrower
domain boundary. Existing imports from ``app.models`` remain supported.
"""

from .contracts import *
from .contracts import __all__
