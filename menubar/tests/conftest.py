"""Platform shim — menubar uses ``rumps`` which is macOS-only.

When the workspace test suite runs on Linux or Windows CI, ``rumps``
isn't installed. Instead of skipping menubar tests entirely (which
would lose the cross-platform ``_FakeRumps`` unit coverage in
``test_main.py``), we inject a minimal stub into ``sys.modules``
BEFORE collection. The stub satisfies a bare ``import rumps`` so any
code path that performs the import doesn't ImportError; the menubar
tests don't actually touch the real rumps surface — they pass
``_FakeRumps`` into ``_build_app`` directly, and the only test that
hits ``main()`` monkey-patches ``sys.platform`` so the lazy
``import rumps`` inside ``main()`` is never reached.

On macOS dev machines with the ``[macos]`` extra installed, real
``rumps`` is already importable and this shim no-ops.
"""

from __future__ import annotations

import importlib.util
import sys
import types


def _install_rumps_stub() -> None:
    """Install a stub ``rumps`` module in ``sys.modules`` if rumps is absent.

    The stub exposes the attribute names the source code references
    (``App``, ``MenuItem``) as no-op classes so ``import rumps`` plus
    attribute access succeeds. Tests inject their own fakes via
    ``_build_app(rumps_mod=...)``; the stub only needs to make the
    import statement itself succeed.
    """
    if importlib.util.find_spec("rumps") is not None:
        return  # Real rumps installed (macOS dev / macOS CI). No shim needed.

    rumps_stub = types.ModuleType("rumps")

    class _StubApp:
        def __init__(self, *args: object, **kwargs: object) -> None:
            self.title: str = ""
            self.menu: list[object] = []

        def run(self) -> None:
            pass

    class _StubMenuItem:
        def __init__(self, *args: object, **kwargs: object) -> None:
            self.title: str = ""
            self.state: int = 0

        def set_callback(self, _cb: object) -> None:
            pass

    rumps_stub.App = _StubApp  # type: ignore[attr-defined]
    rumps_stub.MenuItem = _StubMenuItem  # type: ignore[attr-defined]

    sys.modules["rumps"] = rumps_stub


_install_rumps_stub()
