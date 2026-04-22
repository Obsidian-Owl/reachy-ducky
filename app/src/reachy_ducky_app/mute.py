"""Hard mic-gate: last line of defence between the mic and the network."""

from __future__ import annotations

import numpy as np
import numpy.typing as npt


class MuteGate:
    """Hard mic-gate.

    When muted, :meth:`process` zeros out audio frames before they reach any
    downstream consumer (realtime session, wake detector). This is the last
    line of defence between the mic and the network. Tests must prove that
    ``process(chunk)`` returns an all-zero array with the same dtype+shape
    as the input when muted.
    """

    def __init__(self) -> None:
        self._muted = False

    @property
    def muted(self) -> bool:
        return self._muted

    def set_muted(self, value: bool) -> None:
        self._muted = value

    def toggle(self) -> None:
        self._muted = not self._muted

    def process(self, chunk: npt.NDArray[np.int16]) -> npt.NDArray[np.int16]:
        if self._muted:
            return np.zeros_like(chunk)
        return chunk
