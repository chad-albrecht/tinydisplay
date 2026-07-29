"""Finding HT32 panels on the USB bus.

Discovery is kept apart from the write path so that "is anything plugged in?"
can be answered -- by a CLI, a test, or a Home Assistant config flow -- without
opening the device or holding it open.

``hidapi`` is imported lazily, inside the functions that need it. It is an
optional dependency carrying a compiled extension, and importing
:mod:`tinydisplay.ht32` on a machine without it should work: the protocol layer
is useful on its own, and a missing USB backend ought to surface when somebody
asks for hardware, not when they import the package.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import TYPE_CHECKING, Any

from tinydisplay.ht32.errors import DeviceNotFoundError
from tinydisplay.ht32.protocol import LCD_INTERFACE, PRODUCT_ID, VENDOR_ID

if TYPE_CHECKING:
    from collections.abc import Sequence

__all__ = [
    "HT32DeviceInfo",
    "enumerate_panels",
    "find_panel",
    "import_hid",
    "is_hid_available",
    "select_display_interface",
]


@dataclass(frozen=True, slots=True)
class HT32DeviceInfo:
    """One HID interface belonging to an attached HT32 panel.

    Attributes:
        path: The OS handle used to open this interface. Opaque and
            platform-specific -- a hidraw node on Linux, a device instance path
            on Windows.
        vendor_id: USB vendor ID.
        product_id: USB product ID.
        interface_number: Which HID interface this entry describes. The panel
            publishes several; only :data:`~tinydisplay.ht32.protocol.LCD_INTERFACE`
            takes display data.
        serial_number: The device serial, when the panel reports one. Panels
            frequently report nothing, so this is often empty.
        product_string: Human-readable product name, when reported.
    """

    path: bytes
    vendor_id: int
    product_id: int
    interface_number: int
    serial_number: str = ""
    product_string: str = ""

    @property
    def is_display_interface(self) -> bool:
        """Whether this is the interface that accepts frames."""
        return self.interface_number == LCD_INTERFACE

    def __str__(self) -> str:
        name = self.product_string or "HT32 panel"
        return f"{name} ({self.vendor_id:04X}:{self.product_id:04X} if{self.interface_number})"


def import_hid() -> Any:
    """Import ``hid``, or explain what is missing.

    Raises:
        DeviceNotFoundError: If the ``hidapi`` package is not installed.
    """
    try:
        import hid
    except ImportError as exc:
        msg = (
            "the hidapi package is required to talk to an HT32 panel; "
            "install it with `pip install tinydisplay-ht32[hid]`"
        )
        raise DeviceNotFoundError(msg) from exc
    return hid


def is_hid_available() -> bool:
    """Whether a USB HID backend is installed.

    Useful for skipping hardware tests and for telling "no driver" apart from
    "no device" in diagnostics.
    """
    try:
        import_hid()
    except DeviceNotFoundError:
        return False
    return True


def enumerate_panels(
    *,
    vendor_id: int = VENDOR_ID,
    product_id: int = PRODUCT_ID,
) -> tuple[HT32DeviceInfo, ...]:
    """List every HID interface published by attached panels.

    Returns an empty tuple when nothing is attached; that is a normal state,
    not an error. Interfaces are returned in enumeration order.

    Raises:
        DeviceNotFoundError: If no USB HID backend is installed.
    """
    hid = import_hid()
    entries: Sequence[dict[str, Any]] = hid.enumerate(vendor_id, product_id)
    return tuple(
        HT32DeviceInfo(
            path=entry.get("path", b""),
            vendor_id=entry.get("vendor_id", vendor_id),
            product_id=entry.get("product_id", product_id),
            interface_number=entry.get("interface_number", -1),
            serial_number=entry.get("serial_number") or "",
            product_string=entry.get("product_string") or "",
        )
        for entry in entries
    )


def select_display_interface(
    devices: Sequence[HT32DeviceInfo],
) -> HT32DeviceInfo | None:
    """Pick the interface that accepts frames, or fall back to the first.

    The fallback matters on platforms whose HID backend does not report
    interface numbers -- macOS reports -1 for most devices. Preferring the
    documented interface and falling back to whatever exists is what upstream
    does, and it is the difference between working and not working on a Mac.
    """
    if not devices:
        return None
    for device in devices:
        if device.is_display_interface:
            return device
    return devices[0]


def find_panel(
    *,
    vendor_id: int = VENDOR_ID,
    product_id: int = PRODUCT_ID,
    serial_number: str | None = None,
) -> HT32DeviceInfo:
    """Find the one panel to drive.

    Args:
        vendor_id: USB vendor ID to match.
        product_id: USB product ID to match.
        serial_number: Restrict the search to a panel with this serial, for
            setups with more than one attached.

    Raises:
        DeviceNotFoundError: If no matching panel is attached.
    """
    devices = enumerate_panels(vendor_id=vendor_id, product_id=product_id)
    if serial_number is not None:
        devices = tuple(device for device in devices if device.serial_number == serial_number)

    selected = select_display_interface(devices)
    if selected is None:
        wanted = f"{vendor_id:04X}:{product_id:04X}"
        detail = f" with serial {serial_number!r}" if serial_number else ""
        msg = (
            f"no HT32 panel found at {wanted}{detail}; check that it is plugged in, "
            "and on Linux that a udev rule grants access to the hidraw node"
        )
        raise DeviceNotFoundError(msg)
    return selected
