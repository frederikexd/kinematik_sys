# ============================================================================
#  KinematiK — Formula SAE suspension & vehicle dynamics toolkit
#  Created by Frederik Thio. Copyright (c) 2026 Frederik Thio.
#  Open source. Original author: Frederik Thio, creator of KinematiK.
# ============================================================================
"""Shared pytest bootstrap for the KinematiK suite.

Two jobs, both about *ordering*:

1. Put the repository root on ``sys.path`` so ``import suspension`` works no
   matter where pytest is invoked from.

2. Import the real (lazy, PEP 562) ``suspension`` package *before* any test
   module is collected.  Several script-style tests bootstrap submodules via
   ``sys.modules.setdefault("suspension", types.ModuleType(...))`` so they can
   run standalone (``python tests/test_x.py``) without the package.  Under
   pytest, whichever module was imported first used to decide whether the
   session saw the real package or a bare stub — the source of the
   order-dependent mythbuster / lazy-init failures.  Importing the real
   package here makes every later ``setdefault`` a no-op.
"""
import os
import sys

import pytest

_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if _ROOT not in sys.path:
    sys.path.insert(0, _ROOT)

import suspension  # noqa: E402,F401  (see docstring: must precede test imports)


@pytest.fixture(autouse=True)
def _cwd_guard():
    """Restore the working directory after every test.

    A single leaked ``os.chdir`` (e.g. into a since-deleted tmp_path) used to
    silently break every later test that spawns a subprocess or opens a file
    by relative path — the other half of the order-dependent flakiness.
    """
    cwd = os.getcwd()
    yield
    try:
        os.chdir(cwd)
    except OSError:
        os.chdir(_ROOT)
