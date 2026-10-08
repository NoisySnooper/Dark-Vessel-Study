"""darkvessel: SAR vessel detection and AIS correlation for maritime domain awareness.

Every vessel product this package writes carries DARK_CAVEAT. "Dark" means only
that no AIS position was matched to a radar detection. It never means illegal.
"""

import os
import sys
from pathlib import Path

# Conda environments set PROJ_DATA and GDAL_DATA in their activation scripts. When the environment's python is
# called directly (no activation), GDAL cannot find proj.db; point it at the environment's own copy.
for _var, _sub, _probe in (("PROJ_DATA", "share/proj", "proj.db"), ("GDAL_DATA", "share/gdal", "gdalvrt.xsd")):
    _dir = Path(sys.prefix) / _sub
    if not os.environ.get(_var) and (_dir / _probe).exists():
        os.environ[_var] = str(_dir)

from darkvessel.config import DARK_CAVEAT  # noqa: E402

__all__ = ["DARK_CAVEAT"]
__version__ = "0.1.0"
