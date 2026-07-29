"""Drive the widget dashboard on a real HT32 panel.

    python examples/ht32_widget_dashboard.py
    python examples/ht32_widget_dashboard.py --dry-run

Deliberately almost empty. The dashboard is
:mod:`examples.widget_dashboard`, unchanged, and the loop is
:func:`tinydisplay.ht32.run_panel`, unchanged -- the whole file is the
seventeen lines needed to introduce them.

That is the layering working. The same ``render`` runs under the simulator with
no edits, because a widget tree only ever sees a canvas, and the panel's
heartbeat and reconnection are the driver's business rather than the
dashboard's.
"""

from __future__ import annotations

import argparse
import asyncio
import sys
from pathlib import Path

from tinydisplay.ht32 import HT32Driver, HT32Error, RecordingHidTransport, run_panel

sys.path.insert(0, str(Path(__file__).parent))

from widget_dashboard import render


async def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--fps", type=float, default=5, help="Frame rate (default: 5).")
    parser.add_argument(
        "--seconds",
        type=float,
        default=None,
        help="Stop after this long. Runs until interrupted by default.",
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Drive a recorder instead of a panel.",
    )
    args = parser.parse_args()

    transport = RecordingHidTransport(max_packets=1) if args.dry_run else None
    driver = HT32Driver(transport=transport)
    max_frames = None if args.seconds is None else max(1, int(args.seconds * args.fps))

    try:
        frames = await run_panel(driver, render, fps=args.fps, max_frames=max_frames)
    except HT32Error as exc:
        print(f"error: {exc}")
        return 1
    except KeyboardInterrupt:
        frames = driver.frame_count

    print(f"drew {frames} frames, sent {driver.heartbeat_count} keep-alives")
    return 0


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))
