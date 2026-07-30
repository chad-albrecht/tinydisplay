#!/usr/bin/env python3
"""Can this container drive the HT32 panel over raw USB? Standard library only.

Copy this one file onto the machine and run it *inside the container that will
hold the driver*::

    docker exec homeassistant python3 /config/ht32_usbfs_preflight.py

It answers the question that decides whether the Home Assistant integration can
work at all. Bring-up reached the panel from an **add-on** container, which had
asked for ``usb``, ``udev`` and ``full_access`` and had Protection Mode turned
off. An integration runs in the **Core** container instead and cannot request
any of that, so every permission the driver depends on has to be re-checked
there rather than assumed.

Nothing here writes to the panel. It reads sysfs, stats the usbfs node and
opens it read-write, then closes it again -- so it is safe to run against a
panel something else is currently driving, and safe to run on a live system.

It deliberately duplicates the discovery constants in
``tinydisplay.ht32.usbfs``. That duplication is the point -- it is what lets
the file run where nothing is installed -- and
``tests/ht32/test_usbfs_preflight.py`` asserts the two agree, so the copy
cannot drift without failing the suite.

Exit status is 0 when the panel is reachable, 1 when it is not, 2 on a platform
that has no usbfs at all.
"""

from __future__ import annotations

import os
import sys
from pathlib import Path

# The panel, as `lsusb` would show it.
VENDOR_ID = 0x04D9
PRODUCT_ID = 0xFD01

# Where the kernel describes USB devices, and where it exposes them for raw
# transfers. The driver derives the second from `busnum` and `devnum` read out
# of the first, and so does this.
USB_DEVICES = Path("/sys/bus/usb/devices")
USB_NODES = Path("/dev/bus/usb")

#: The interface class the display speaks. Reported for orientation only --
#: the driver chooses by capability rather than by number, because upstream
#: hard-codes an interface number that real hardware disagrees with.
HID_CLASS = 0x03

# Transfer types from bmAttributes & 0x03. Only these two can carry a frame;
# control and isochronous endpoints cannot.
TRANSFER_BULK = 2
TRANSFER_INTERRUPT = 3


def read_text(path: Path) -> str:
    """A sysfs attribute, or an empty string if it cannot be read."""
    try:
        return path.read_text(encoding="ascii", errors="replace").strip()
    except OSError:
        return ""


def find_panel() -> tuple[int, int, Path] | None:
    """Locate the panel, returning ``(bus, device, sysfs)`` or ``None``.

    Matches ``tinydisplay.ht32.usbfs.find_usb_panel``: walk the device tree and
    compare the vendor and product ids, which sysfs writes as lowercase hex.
    """
    if not USB_DEVICES.is_dir():
        return None
    for entry in sorted(USB_DEVICES.iterdir()):
        try:
            vendor = int(read_text(entry / "idVendor") or "x", 16)
            product = int(read_text(entry / "idProduct") or "x", 16)
            if (vendor, product) != (VENDOR_ID, PRODUCT_ID):
                continue
            bus = int(read_text(entry / "busnum"))
            device = int(read_text(entry / "devnum"))
        except ValueError:
            continue
        return (bus, device, entry)
    return None


def read_interfaces(sysfs: Path) -> dict[int, list[tuple[int, int, int]]]:
    """Map interface number to its endpoints as ``(address, kind, packet_size)``.

    Mirrors ``tinydisplay.ht32.usbfs.usb_interfaces``, including that sysfs
    writes all three of these attributes as hex. An interface is any child
    declaring an interface number -- matching on content rather than on the
    kernel's ``<device>:<config>.<interface>`` naming is harder to get subtly
    wrong.
    """
    interfaces: dict[int, list[tuple[int, int, int]]] = {}
    for entry in sorted(sysfs.iterdir()):
        if not entry.is_dir():
            continue
        try:
            number = int(read_text(entry / "bInterfaceNumber"), 16)
        except ValueError:
            continue

        endpoints: list[tuple[int, int, int]] = []
        for ep_dir in sorted(entry.glob("ep_*")):
            try:
                endpoints.append(
                    (
                        int(read_text(ep_dir / "bEndpointAddress"), 16),
                        int(read_text(ep_dir / "bmAttributes"), 16) & 0x03,
                        int(read_text(ep_dir / "wMaxPacketSize"), 16),
                    )
                )
            except ValueError:
                continue
        interfaces[number] = endpoints
    return interfaces


def can_carry_frames(address: int, kind: int) -> bool:
    """Whether an endpoint can take frame data: OUT, and bulk or interrupt."""
    return not address & 0x80 and kind in (TRANSFER_BULK, TRANSFER_INTERRUPT)


def find_output_endpoint(
    interfaces: dict[int, list[tuple[int, int, int]]],
) -> tuple[int, int] | None:
    """The interface and endpoint the driver would claim, or ``None``.

    Mirrors ``tinydisplay.ht32.usbfs.find_output_endpoint``: among the
    endpoints that can carry frames, the largest packet size wins, and a tie
    keeps the lowest interface number. Reported here because choosing the
    wrong interface is the failure this panel has already produced once, and
    it looks like a blank screen rather than an error.
    """
    best: tuple[int, int, int] | None = None
    for number, endpoints in sorted(interfaces.items()):
        for address, kind, packet_size in endpoints:
            if not can_carry_frames(address, kind):
                continue
            if best is None or packet_size > best[2]:
                best = (number, address, packet_size)
    return None if best is None else (best[0], best[1])


def describe_interfaces(sysfs: Path) -> list[str]:
    """One line per interface: number, class, bound driver, endpoints.

    Read from sysfs rather than by opening the device, so it works while
    something else owns the interface.
    """
    kinds = {TRANSFER_BULK: "bulk", TRANSFER_INTERRUPT: "int"}
    interfaces = read_interfaces(sysfs)
    lines: list[str] = []

    for entry in sorted(sysfs.iterdir()):
        if not entry.is_dir():
            continue
        try:
            number = int(read_text(entry / "bInterfaceNumber"), 16)
        except ValueError:
            continue

        klass = read_text(entry / "bInterfaceClass")
        driver = (entry / "driver").resolve().name if (entry / "driver").exists() else "none"
        described = [
            f"{address:#04x} {'OUT' if not address & 0x80 else 'IN '} "
            f"{kinds.get(kind, str(kind))} {packet_size}B"
            for address, kind, packet_size in interfaces.get(number, [])
        ]
        marker = " <- HID" if klass and int(klass, 16) == HID_CLASS else ""
        lines.append(
            f"  if{number:02d}  class {klass}  driver {driver}{marker}\n"
            + ("\n".join(f"          ep {item}" for item in described) or "          no endpoints")
        )
    return lines


def check_node(bus: int, device: int) -> tuple[bool, str]:
    """Try to open the usbfs node read-write, reporting what happened.

    Read-write because that is what the driver needs and what a read-only
    listing cannot tell you: the nodes are ``crw-rw-r--``, so a process outside
    the owning group can stat and list them and still not be able to write a
    single frame.
    """
    node = USB_NODES / f"{bus:03d}" / f"{device:03d}"
    if not node.exists():
        return (False, f"{node} does not exist")

    try:
        info = node.stat()
    except OSError as exc:
        return (False, f"cannot stat {node}: {exc}")

    mode = oct(info.st_mode & 0o777)
    try:
        handle = os.open(node, os.O_RDWR)
    except PermissionError:
        return (False, f"{node} (mode {mode}) is not writable by uid {os.geteuid()}")
    except OSError as exc:
        return (False, f"cannot open {node}: {exc}")
    os.close(handle)
    return (True, f"{node} (mode {mode}) opened read-write")


def main() -> int:
    """Report whether the panel is reachable from here."""
    if not sys.platform.startswith("linux"):
        print(f"usbfs is Linux-only; this is {sys.platform}")
        return 2
    if not USB_DEVICES.is_dir():
        print(f"no {USB_DEVICES} -- this container cannot see the USB bus at all")
        return 2

    print(f"running as uid {os.geteuid()}")
    print(f"looking for {VENDOR_ID:04X}:{PRODUCT_ID:04X}")

    found = find_panel()
    if found is None:
        print("panel:  NOT FOUND on the USB bus")
        print()
        print("The panel is either not attached or not visible from this container.")
        print("If an add-on can see it and this cannot, the difference is the")
        print("container's device mapping, not the driver.")
        return 1

    bus, device, sysfs = found
    print(f"panel:  bus {bus:03d} device {device:03d}  ({sysfs.name})")
    for line in describe_interfaces(sysfs):
        print(line)

    target = find_output_endpoint(read_interfaces(sysfs))
    if target is None:
        print("claim:  NONE -- no endpoint here can carry frames")
    else:
        print(f"claim:  interface {target[0]} endpoint {target[1]:#04x}")

    writable, detail = check_node(bus, device)
    print(f"node:   {detail}")

    print()
    if target is None:
        print("NOT READY. The panel is present but publishes no usable OUT endpoint.")
        print("The driver would have nowhere to write frames.")
        return 1
    if not writable:
        print("NOT READY. The panel is present but this container cannot write to it.")
        print("Raw USB needs write access to the node, which a read-only listing")
        print("of /dev/bus/usb does not prove.")
        return 1

    print("READY. The panel is present and this container can write to it.")
    print("Note that a kernel driver shown above as `usbhid` is expected -- the")
    print("driver detaches it per interface when it connects, which needs the")
    print("same write access this check just confirmed.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
