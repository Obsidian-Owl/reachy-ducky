"""Platform guard — menubar uses ``rumps`` which is macOS-only.

When the workspace test suite runs on Linux or Windows CI, ``rumps``
isn't importable. We use ``collect_ignore_glob`` (rather than
``pytest.importorskip`` at module scope) because conftests are loaded
during pytest's initial collection phase — a ``Skipped`` raised at
conftest load aborts the entire pytest session rather than skipping
just this directory. ``collect_ignore_glob`` tells pytest to skip
collecting these tests cleanly.

This honors ``.claude/rules/testing-standards.md``'s "tests fail,
never skip" rule: the menubar tests' real subject (rumps) genuinely
isn't available off-macOS, which is the sanctioned exception. On
macOS dev machines with the ``[macos]`` extra installed, rumps is
importable and the tests run normally.
"""

from __future__ import annotations

import importlib.util

if importlib.util.find_spec("rumps") is None:
    collect_ignore_glob = ["test_*.py"]
