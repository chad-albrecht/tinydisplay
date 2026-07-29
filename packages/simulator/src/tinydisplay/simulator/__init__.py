"""TinyDisplay simulator: a display driver that renders to a desktop window.

The simulator exists so that dashboards can be built, and driver bugs found,
with nothing plugged in. It ships before the first hardware driver on purpose:
once both exist, a disagreement between the window and the panel points at the
panel's driver, because the simulator has no wire protocol of its own to get
wrong.

Its one non-obvious property is that it previews the *encoded* frame. The
driver encodes a canvas exactly as a real panel would, and the window decodes
those bytes back; a byte-order or packing mistake is therefore as visible on
screen as it would be on hardware, and the RGB565 quantisation preview is a
consequence of the design rather than a feature bolted onto it.

Example:
    >>> import asyncio
    >>> from tinydisplay.simulator import NullPreviewWindow, SimulatorDriver
    >>> async def main() -> str:
    ...     window = NullPreviewWindow()
    ...     async with SimulatorDriver(16, 16, window=window) as driver:
    ...         canvas = driver.create_canvas()
    ...         canvas.clear(Color.from_hex("#123456"))
    ...         await driver.show(canvas)
    ...     shown = driver.last_preview
    ...     return "#{:02x}{:02x}{:02x}".format(*shown[0, 0])
    >>> asyncio.run(main())  # what a 16-bit panel actually shows
    '#103452'
"""

from __future__ import annotations

from tinydisplay.simulator.driver import DEFAULT_SCALE, SimulatorDriver
from tinydisplay.simulator.errors import (
    DashboardError,
    SimulatorError,
    WindowUnavailableError,
)
from tinydisplay.simulator.preview import (
    MAX_SCALE,
    decode_frame,
    frame_to_canvas,
    scale_nearest,
    validate_scale,
)
from tinydisplay.simulator.reload import RENDER_ATTRIBUTE, DashboardLoader
from tinydisplay.simulator.runner import DEFAULT_FPS, draw_error, run_dashboard
from tinydisplay.simulator.window import (
    NullPreviewWindow,
    PreviewWindow,
    TkPreviewWindow,
)

__version__ = "0.1.0"

__all__ = [
    "DEFAULT_FPS",
    "DEFAULT_SCALE",
    "MAX_SCALE",
    "RENDER_ATTRIBUTE",
    "DashboardError",
    "DashboardLoader",
    "NullPreviewWindow",
    "PreviewWindow",
    "SimulatorDriver",
    "SimulatorError",
    "TkPreviewWindow",
    "WindowUnavailableError",
    "__version__",
    "decode_frame",
    "draw_error",
    "frame_to_canvas",
    "run_dashboard",
    "scale_nearest",
    "validate_scale",
]
