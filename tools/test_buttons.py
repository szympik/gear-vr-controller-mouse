"""Connects to a Gear VR Controller and prints button states in real-time."""

import asyncio
import sys
from bleak import BleakClient

DEFAULT_MAC = "XX:XX:XX:XX:XX:XX"

NOTIFY_CHAR_UUID = "c8c51726-81bc-483b-a052-f7a14ea3d281"
WRITE_CHAR_UUID  = "c8c51726-81bc-483b-a052-f7a14ea3d282"
CMD_SENSOR       = bytearray([0x01, 0x00])


def notification_handler(sender, data):
    if len(data) == 60:
        buttons = data[58]
        if buttons != 0:
            trigger       = bool(buttons & 0x01)
            home          = bool(buttons & 0x02)
            back          = bool(buttons & 0x04)
            touchpad_click = bool(buttons & 0x08)
            vol_up        = bool(buttons & 0x10)
            vol_down      = bool(buttons & 0x20)
            print(
                f"Trigger: {trigger} | Home: {home} | Back: {back} | "
                f"Touchpad: {touchpad_click} | Vol+: {vol_up} | Vol-: {vol_down}"
            )


async def main(mac_address):
    print(f"Connecting to {mac_address}...")
    try:
        async with BleakClient(mac_address) as client:
            print("Connected!")
            await client.start_notify(NOTIFY_CHAR_UUID, notification_handler)
            await client.write_gatt_char(WRITE_CHAR_UUID, CMD_SENSOR, response=True)
            print("Sensor active. Press buttons on the controller. Ctrl+C to exit.\n")
            while True:
                await asyncio.sleep(1)
    except Exception as e:
        print(f"\nError: {e}")


if __name__ == "__main__":
    mac = sys.argv[1] if len(sys.argv) > 1 else DEFAULT_MAC
    asyncio.run(main(mac))
