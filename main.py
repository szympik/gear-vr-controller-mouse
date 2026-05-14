import asyncio
import time
import ctypes
import struct
import logging
from bleak import BleakClient, BleakScanner

# Windows API mouse input flags
MOUSEEVENTF_MOVE     = 0x0001
MOUSEEVENTF_LEFTDOWN = 0x0002
MOUSEEVENTF_LEFTUP   = 0x0004


def fast_click():
    ctypes.windll.user32.mouse_event(MOUSEEVENTF_LEFTDOWN, 0, 0, 0, 0)
    ctypes.windll.user32.mouse_event(MOUSEEVENTF_LEFTUP, 0, 0, 0, 0)


def move_mouse(dx, dy):
    ctypes.windll.user32.mouse_event(MOUSEEVENTF_MOVE, int(dx), int(dy), 0, 0)


# Bluetooth device configuration — replace with your controller's MAC address
MAC_ADDRESS = "XX:XX:XX:XX:XX:XX"
DEVICE_NAME = None  # Optional: e.g. "Gear VR Controller(7E15)"

# Controller GATT characteristic UUIDs (reverse-engineered)
NOTIFY_CHAR_UUID = "c8c51726-81bc-483b-a052-f7a14ea3d281"
WRITE_CHAR_UUID  = "c8c51726-81bc-483b-a052-f7a14ea3d282"

# Controller commands (reverse-engineered)
CMD_OFF         = bytearray([0x00, 0x00])  # Disable sensor
CMD_SENSOR      = bytearray([0x01, 0x00])  # Enable touchpad + sensor + IMU
CMD_KEEP_ALIVE  = bytearray([0x04, 0x00])  # Keep alive signal
CMD_LPM_ENABLE  = bytearray([0x06, 0x00])  # Enable Low Power Mode
CMD_LPM_DISABLE = bytearray([0x07, 0x00])  # Disable Low Power Mode
CMD_VR_MODE     = bytearray([0x08, 0x00])  # VR mode (high refresh rate)

# Timing configuration
BLE_TIMEOUT         = 10.0
HEARTBEAT_INTERVAL  = 0.1   # Aggressive heartbeat to prevent BLE disconnect
SENSOR_BOOT_PACKETS = 50    # Packets to discard on startup (sensor stabilization)

# Logging
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s [%(levelname)s] %(message)s',
    datefmt='%H:%M:%S'
)
log = logging.getLogger('GearVR')

# Global state
current_state = {
    "trigger": False,
    "gyro_x": 0, "gyro_y": 0, "gyro_z": 0
}
last_trigger_state = False
last_click_time = 0.0
DEBOUNCE_DELAY = 0.03

last_notification_time = 0.0
notification_count = 0
boot_packets_remaining = SENSOR_BOOT_PACKETS

# Mouse control settings
SENSITIVITY = 150.0
DEADZONE = 50


def notification_handler(sender, data):
    """Handles incoming BLE notification packets from the controller."""
    global current_state, last_trigger_state, last_click_time
    global last_notification_time, notification_count, boot_packets_remaining

    last_notification_time = time.time()
    notification_count += 1

    # Discard initial packets while sensor stabilizes
    if boot_packets_remaining > 0:
        boot_packets_remaining -= 1
        return

    if len(data) >= 60:
        buttons = data[58]
        current_state["trigger"] = bool(buttons & 0x01)

        # Decode gyroscope data (signed 16-bit little-endian)
        current_state["gyro_x"] = struct.unpack('<h', data[10:12])[0]
        current_state["gyro_y"] = struct.unpack('<h', data[12:14])[0]
        current_state["gyro_z"] = struct.unpack('<h', data[14:16])[0]

        # Trigger click on rising edge with debounce
        if current_state["trigger"] and not last_trigger_state:
            current_time = time.time()
            if current_time - last_click_time > DEBOUNCE_DELAY:
                asyncio.get_running_loop().call_soon_threadsafe(fast_click)
                last_click_time = current_time

        last_trigger_state = current_state["trigger"]


def verify_characteristics(client):
    """Verifies that the required GATT characteristics exist after connection."""
    notify_char = None
    write_char = None
    for service in client.services:
        for char in service.characteristics:
            if char.uuid == NOTIFY_CHAR_UUID:
                notify_char = char
            elif char.uuid == WRITE_CHAR_UUID:
                write_char = char
    if notify_char is None:
        log.error(f'NOTIFY characteristic not found: {NOTIFY_CHAR_UUID}')
    if write_char is None:
        log.error(f'WRITE characteristic not found: {WRITE_CHAR_UUID}')
    return notify_char is not None and write_char is not None


async def heartbeat_task(client, connected_flag):
    """Sends periodic commands to prevent the controller from entering sleep mode.

    The combination of CMD_LPM_DISABLE + CMD_SENSOR + CMD_KEEP_ALIVE every 100ms
    prevents the ~14s disconnect timeout caused by the BLE adapter firmware.
    """
    while connected_flag["value"]:
        await asyncio.sleep(HEARTBEAT_INTERVAL)
        if not connected_flag["value"]:
            break
        try:
            await client.write_gatt_char(WRITE_CHAR_UUID, CMD_LPM_DISABLE)
            await client.write_gatt_char(WRITE_CHAR_UUID, CMD_SENSOR)
            await client.write_gatt_char(WRITE_CHAR_UUID, CMD_KEEP_ALIVE)
        except Exception:
            break


async def mouse_loop():
    """Translates gyroscope data into mouse movement at ~60fps."""
    while True:
        g_x = current_state.get('gyro_x', 0)
        g_z = current_state.get('gyro_z', 0)

        dx = 0
        dy = 0

        if abs(g_z) > DEADZONE:
            dx = -g_z / SENSITIVITY
        if abs(g_x) > DEADZONE:
            dy = -g_x / SENSITIVITY

        if dx != 0 or dy != 0:
            move_mouse(dx, dy)

        await asyncio.sleep(0.016)


async def diagnostics_task():
    """Logs packet statistics every 30 seconds for connection monitoring."""
    global notification_count
    while True:
        await asyncio.sleep(30.0)
        now = time.time()
        since = now - last_notification_time if last_notification_time > 0 else -1
        log.info(
            f'Diagnostics: {notification_count} packets/30s, '
            f'last {since:.1f}s ago'
        )
        notification_count = 0


async def run_controller():
    """Main connection loop with automatic reconnection.

    Disconnections (~14s) may be unavoidable due to firmware/adapter limitations.
    This loop reconnects as fast as possible (~0.5s) so the user barely notices.
    """
    global last_notification_time, notification_count, boot_packets_remaining

    first_time = True
    cached_device = None
    consecutive_failures = 0
    mouse_task = None
    diag_task = None

    while True:
        # Phase 1: Scan for device (skipped on reconnect if cached)
        device = cached_device

        if device is None:
            if DEVICE_NAME is not None:
                log.info(f'Searching by name: {DEVICE_NAME}...')
                device = await BleakScanner.find_device_by_name(DEVICE_NAME, timeout=BLE_TIMEOUT)

            if device is None:
                log.info(f'Searching by MAC: {MAC_ADDRESS}...')
                device = await BleakScanner.find_device_by_address(MAC_ADDRESS, timeout=BLE_TIMEOUT)

            if device is None:
                log.warning('Controller not found. Retrying in 5s...')
                await asyncio.sleep(5.0)
                consecutive_failures += 1
                if consecutive_failures >= 3:
                    log.info('Multiple failures. Waiting 10s...')
                    await asyncio.sleep(10.0)
                    consecutive_failures = 0
                continue

            log.info(f'Found: {device.name} [{device.address}]')

        consecutive_failures = 0
        cached_device = device

        # Phase 2: Connect
        connected_flag = {"value": False}
        disconnect_event = asyncio.Event()

        def on_disconnect(client):
            connected_flag["value"] = False
            # Zero out gyro to prevent mouse drift on stale data
            current_state["gyro_x"] = 0
            current_state["gyro_y"] = 0
            current_state["gyro_z"] = 0
            disconnect_event.set()

        try:
            async with BleakClient(
                device,
                disconnected_callback=on_disconnect,
                timeout=BLE_TIMEOUT,
                use_cached_services=True
            ) as client:

                if not client.is_connected:
                    log.error('Connection failed')
                    cached_device = None
                    await asyncio.sleep(1.0)
                    continue

                connected_flag["value"] = True
                disconnect_event.clear()
                log.info('Connected!')

                if first_time:
                    await asyncio.sleep(0.5)  # Wait for GATT discovery

                if not verify_characteristics(client):
                    log.error('Required characteristics not found!')
                    cached_device = None
                    await asyncio.sleep(2.0)
                    continue

                # Phase 3: Battery read + pairing (first connection only)
                if first_time:
                    try:
                        log.info('Attempting pairing...')
                        result = await client.pair()
                        log.info(f'Pairing result: {result}')
                    except Exception as e:
                        log.warning(f'Pairing failed: {e}')

                    try:
                        battery_char = "00002a19-0000-1000-8000-00805f9b34fb"
                        battery_data = await client.read_gatt_char(battery_char)
                        battery_level = int.from_bytes(battery_data, byteorder='big')
                        log.info(f'Battery level: {battery_level}%')
                    except Exception:
                        log.warning('Could not read battery level')

                # Phase 4: Start sensor
                # Fewer boot packets on reconnect (sensor was already active)
                boot_packets_remaining = 5 if not first_time else SENSOR_BOOT_PACKETS
                last_notification_time = time.time()
                notification_count = 0
                first_time = False

                await client.write_gatt_char(WRITE_CHAR_UUID, CMD_LPM_DISABLE)
                await client.start_notify(NOTIFY_CHAR_UUID, notification_handler)
                await client.write_gatt_char(WRITE_CHAR_UUID, CMD_SENSOR)
                log.info('Sensor active. Mouse ready.')

                # Phase 5: Background tasks (mouse + diagnostics persist across reconnects)
                if mouse_task is None or mouse_task.done():
                    mouse_task = asyncio.create_task(mouse_loop())
                if diag_task is None or diag_task.done():
                    diag_task = asyncio.create_task(diagnostics_task())

                heartbeat = asyncio.create_task(heartbeat_task(client, connected_flag))

                # Phase 6: Wait for disconnect, then reconnect
                await disconnect_event.wait()
                heartbeat.cancel()

            await asyncio.sleep(0.5)  # Brief pause before reconnect

        except Exception as e:
            msg = str(e) if str(e) else type(e).__name__
            log.error(f'Error: {msg}. Retrying in 2s...')
            cached_device = None
            await asyncio.sleep(2.0)


if __name__ == "__main__":
    try:
        asyncio.run(run_controller())
    except KeyboardInterrupt:
        print("\nStopped by user.")