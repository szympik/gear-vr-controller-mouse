"""Connects to a BLE device and lists all GATT services and characteristics."""

import asyncio
import sys
from bleak import BleakClient

DEFAULT_MAC = "XX:XX:XX:XX:XX:XX"


async def explore_device(mac_address):
    print(f"Connecting to {mac_address}...")
    try:
        async with BleakClient(mac_address) as client:
            print("Connected! Listing services and characteristics:\n")
            for service in client.services:
                print(f"[Service] {service.uuid} ({service.description})")
                for char in service.characteristics:
                    props = ", ".join(char.properties)
                    print(f"  |-- [Characteristic] {char.uuid}")
                    print(f"  |     Properties: {props}")
                print("-" * 60)
    except Exception as e:
        print(f"\nError during exploration: {e}")


if __name__ == "__main__":
    mac = sys.argv[1] if len(sys.argv) > 1 else DEFAULT_MAC
    asyncio.run(explore_device(mac))
