# ============================================================================
#  KinematiK — Formula SAE suspension & vehicle dynamics toolkit
#  Created by Frederik Thio. Copyright (c) 2026 Frederik Thio.
#  Open source. Original author: Frederik Thio, creator of KinematiK.
# ============================================================================
"""
Discipline myth rule-sets.

Importing this package imports every discipline module below, and each one
self-registers its rules into ``mythbuster.DEFAULT_ENGINE`` at import time. The
engine imports this package lazily (first time ``mythbuster.check`` is called),
so adding a discipline here costs nothing until the myth-buster is used.

To add rules for a discipline:
    1. create ``suspension/myth_rules/<discipline>.py``,
    2. write small ``Rule`` objects (see powertrain.py for the pattern),
    3. ``register(...)`` them at module bottom,
    4. add the import here.
No engine code changes.
"""
from __future__ import annotations

# Each import triggers that module's registration side effects.
from . import powertrain      # noqa: F401
from . import tires           # noqa: F401
from . import aerodynamics    # noqa: F401
from . import suspension_balance  # noqa: F401
from . import brakes          # noqa: F401
from . import cooling         # noqa: F401
from . import electrics       # noqa: F401
from . import structures      # noqa: F401
