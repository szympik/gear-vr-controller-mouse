"""Scans for nearby Bluetooth Low Energy devices and prints their names and MAC addresses."""

import asyncio
from bleak import BleakScanner


async def run_scanner():
    print("Scanning for nearby Bluetooth devices...")
    devices = await BleakScanner.discover()

    print(f"\nFound {len(devices)} device(s):")
    print("-" * 40)
    for d in devices:
        name = d.name if d.name else "Unknown"
        print(f"Name: {name}")
        print(f"MAC:  {d.address}")
        print("-" * 40)


if __name__ == "__main__":
    asyncio.run(run_scanner())
