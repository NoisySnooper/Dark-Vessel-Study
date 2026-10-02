"""darkvessel: SAR vessel detection and AIS correlation for maritime domain awareness.

Every vessel product this package writes carries DARK_CAVEAT. "Dark" means only
that no AIS position was matched to a radar detection. It never means illegal.
"""

from darkvessel.config import DARK_CAVEAT

__all__ = ["DARK_CAVEAT"]
__version__ = "0.1.0"
