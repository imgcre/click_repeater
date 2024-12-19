import bluetooth
import random
import struct
import time
import micropython
import asyncio
import network
import json
import hashlib
import ubinascii
import my_uuid
from machine import WDT

from ble_advertising import decode_services, decode_name
from async_websocket_client import AsyncWebsocketClient

from micropython import const

_IRQ_CENTRAL_CONNECT = const(1)
_IRQ_CENTRAL_DISCONNECT = const(2)
_IRQ_GATTS_WRITE = const(3)
_IRQ_GATTS_READ_REQUEST = const(4)
_IRQ_SCAN_RESULT = const(5)
_IRQ_SCAN_DONE = const(6)
_IRQ_PERIPHERAL_CONNECT = const(7)
_IRQ_PERIPHERAL_DISCONNECT = const(8)
_IRQ_GATTC_SERVICE_RESULT = const(9)
_IRQ_GATTC_SERVICE_DONE = const(10)
_IRQ_GATTC_CHARACTERISTIC_RESULT = const(11)
_IRQ_GATTC_CHARACTERISTIC_DONE = const(12)
_IRQ_GATTC_DESCRIPTOR_RESULT = const(13)
_IRQ_GATTC_DESCRIPTOR_DONE = const(14)
_IRQ_GATTC_READ_RESULT = const(15)
_IRQ_GATTC_READ_DONE = const(16)
_IRQ_GATTC_WRITE_DONE = const(17)
_IRQ_GATTC_NOTIFY = const(18)
_IRQ_GATTC_INDICATE = const(19)

_ADV_IND = const(0x00)
_ADV_DIRECT_IND = const(0x01)
_ADV_SCAN_IND = const(0x02)
_ADV_NONCONN_IND = const(0x03)

WIFI_SSID: str = 'CMCC-E9dG'
WIFI_KEY: str = 'kcts7525'

_UART_SERVICE_UUID = bluetooth.UUID("6E400001-B5A3-F393-E0A9-E50E24DCCA9E")
_UART_RX_CHAR_UUID = bluetooth.UUID("6E400002-B5A3-F393-E0A9-E50E24DCCA9E")
_UART_TX_CHAR_UUID = bluetooth.UUID("6E400003-B5A3-F393-E0A9-E50E24DCCA9E")

_HID_SERVICE_UUID = bluetooth.UUID(0x1812)
_HID_REPORT_CHAR_UUID = bluetooth.UUID(0x2A4D)

_CONFIG_DESC_UUID = bluetooth.UUID(0x2902)

RPC_VERSION = 1

class BLESimpleCentral:
    def __init__(self, ble: bluetooth.BLE):
        self._ble = ble
        self._ble.active(True)
        self._ble.irq(self._irq)

        self._reset()

    def _reset(self):
        # Cached name and address from a successful scan.
        self._name = None
        self._addr_type = None
        self._addr = None

        # Callbacks for completion of various operations.
        # These reset back to None after being invoked.
        self._scan_callback = None
        self._conn_callback = None
        self._read_callback = None

        # Persistent callback for when new data is notified from the device.
        self._notify_callback = None

        # Connected device.
        self._conn_handle = None
        self._start_handle = None
        self._end_handle = None
        self._tx_handle = None
        self._config_desc_handle = None
        self._i = 0

    def _irq(self, event, data):
        if event == _IRQ_SCAN_RESULT:
            addr_type, addr, adv_type, rssi, adv_data = data
            if adv_type in (_ADV_IND, _ADV_DIRECT_IND) and bytes(addr) == b'\xe0\x86\x5d\x34\xd2\xc3':
                # print(addr_type, bytes(addr), adv_type, rssi)
            #     print(_HID_SERVICE_UUID)
            #     print(decode_services(adv_data))
            # if adv_type in (_ADV_IND, _ADV_DIRECT_IND) and _UART_SERVICE_UUID in decode_services(
            #     adv_data
            # ):
                # Found a potential device, remember it and stop scanning.
                self._addr_type = addr_type
                self._addr = bytes(addr)  # Note: addr buffer is owned by caller so need to copy it.
                self._name = decode_name(adv_data) or "?"
                self._ble.gap_scan(None)

        elif event == _IRQ_SCAN_DONE:
            if self._scan_callback:
                if self._addr:
                    # Found a device during the scan (and the scan was explicitly stopped).
                    self._scan_callback(self._addr_type, self._addr, self._name)
                    self._scan_callback = None
                else:
                    # Scan timed out.
                    self._scan_callback(None, None, None)

        elif event == _IRQ_PERIPHERAL_CONNECT:
            # Connect successful.
            conn_handle, addr_type, addr = data
            if addr_type == self._addr_type and addr == self._addr:
                self._conn_handle = conn_handle
                self._ble.gattc_discover_services(self._conn_handle)

        elif event == _IRQ_PERIPHERAL_DISCONNECT:
            # Disconnect (either initiated by us or the remote end).
            conn_handle, _, _ = data
            if conn_handle == self._conn_handle:
                # If it was initiated by us, it'll already be reset.
                self._reset()

        elif event == _IRQ_GATTC_SERVICE_RESULT:
            # Connected device returned a service.
            conn_handle, start_handle, end_handle, uuid = data
            print("service", data)
            if conn_handle == self._conn_handle and uuid == _HID_SERVICE_UUID:
                self._start_handle, self._end_handle = start_handle, end_handle

        elif event == _IRQ_GATTC_SERVICE_DONE:
            # Service query complete.
            if self._start_handle and self._end_handle:
                self._ble.gattc_discover_characteristics(
                    self._conn_handle, self._start_handle, self._end_handle
                )
            else:
                # 重新尝试扫描
                self._ble.gattc_discover_services(self._conn_handle)
                print("Failed to find hid service.") #TODO: 出现过这个错误

        elif event == _IRQ_GATTC_CHARACTERISTIC_RESULT:
            # Connected device returned a characteristic.
            conn_handle, def_handle, value_handle, properties, uuid = data
            if conn_handle == self._conn_handle and uuid == _HID_REPORT_CHAR_UUID:
                print('char', value_handle, uuid, self._i)
                if self._i == 1:
                    self._tx_handle = value_handle
                self._i += 1
            

        elif event == _IRQ_GATTC_CHARACTERISTIC_DONE:
            # Characteristic query complete.
            if self._tx_handle is not None:
                # We've finished connecting and discovering device, fire the connect callback.
                if self._conn_callback:
                    self._conn_callback()
                self._ble.gattc_discover_descriptors(
                    self._conn_handle, self._tx_handle, self._tx_handle + 1
                )
            else:
                print("Failed to find uart rx characteristic.")
        elif event == _IRQ_GATTC_DESCRIPTOR_RESULT:
            conn_handle, dsc_handle, uuid = data
            print('desc', conn_handle, dsc_handle, uuid)
            if uuid == _CONFIG_DESC_UUID:
                self._config_desc_handle = dsc_handle
        elif event == _IRQ_GATTC_WRITE_DONE:
            conn_handle, value_handle, status = data
            print("write", value_handle, status)

        elif event == _IRQ_GATTC_NOTIFY:
            conn_handle, value_handle, notify_data = data
            # Note: Status will be zero on success, implementation-specific value otherwise.
            # print(conn_handle, value_handle, notify_data)
            if conn_handle == self._conn_handle and value_handle == self._tx_handle:
                if self._notify_callback:
                    self._notify_callback(notify_data)
        elif event == _IRQ_GATTC_READ_RESULT:
            conn_handle, value_handle, char_data = data
            print('read', conn_handle, value_handle, bytes(char_data))
            ...

    # Returns true if we've successfully connected and discovered characteristics.
    def is_connected(self):
        return (
            self._conn_handle is not None
            and self._tx_handle is not None
            and self._config_desc_handle is not None
        )

    # Find a device advertising the environmental sensor service.
    def scan(self, callback=None):
        self._addr_type = None
        self._addr = None
        self._scan_callback = callback
        self._ble.gap_scan(0, 30000, 30000)

    # Connect to the specified device (otherwise use cached address from a scan).
    def connect(self, addr_type=None, addr=None, callback=None):
        self._addr_type = addr_type or self._addr_type
        self._addr = addr or self._addr
        self._conn_callback = callback
        if self._addr_type is None or self._addr is None:
            return False
        self._ble.gap_connect(self._addr_type, self._addr)
        return True

    # Disconnect from current device.
    def disconnect(self):
        if self._conn_handle is None:
            return
        self._ble.gap_disconnect(self._conn_handle)
        self._reset()

    # Send data over the UART
    def write(self, v, response=False):
        if not self.is_connected():
            return
        # self._ble.gattc_write(self._conn_handle, self._rx_handle, v, 1 if response else 0)

    def enable_notify(self):
        if not self.is_connected():
            return
        self._ble.gattc_write(self._conn_handle, self._config_desc_handle, b'\x01\x00', 1)
        ...

    def read_report(self):
        if not self.is_connected():
            return
        self._ble.gattc_read(self._conn_handle, self._tx_handle)

    # Set handler for when data is received over the UART.
    def on_notify(self, callback):
        self._notify_callback = callback

click_event: asyncio.Event = asyncio.ThreadSafeFlag()

ble = bluetooth.BLE()
central = BLESimpleCentral(ble)

async def demo():

    not_found = False

    trigger_conn = False

    def on_scan(addr_type, addr, name):
        if addr_type is not None:
            nonlocal trigger_conn
            # print("Found peripheral:", addr_type, addr, name)
            trigger_conn = True
            # central.connect()
            ...
        else:
            nonlocal not_found
            not_found = True
            print("No peripheral found.")

    print('start scan peripheral')
    central.scan(callback=on_scan)

    # Wait for connection...
    while not central.is_connected():
        if trigger_conn:
            trigger_conn = False
            central.connect()
        await asyncio.sleep(0.1)
        if not_found:
            return

    print("Connected")
    await send_event('CtrlBtnConnected')

    def on_rx(v):
        # RX b'\x07\x06p\x07\x80\x0c\x01\x00'
        # RX b'\x07\x06p\x07(\n\x01\x00'
        # RX b'\x07\x06p\x07\xfc\x08\x01\x00'
        # RX b'\x07\x06p\x07\xd0\x07\x01\x00'
        # RX b'\x07\x06p\x07\xa4\x06\x01\x00'
        # RX b'\x07\x06p\x07x\x05\x01\x00'
        # RX b'\x07\x06p\x07L\x04\x01\x00'
        # RX b'\x07\x06p\x07 \x03\x01\x00'
        # RX b'\x07\x06p\x07\xf4\x01\x01\x00'
        # RX b'\x00\x06p\x07\xc8\x00\x00\x00'

        # RX b'\x07\x07p\x07p\x07\x01\x00'
        # RX b'\x00\x07p\x07p\x07\x00\x00'
        # RX b'\x07\x07p\x07p\x07\x01\x00'
        # RX b'\x00\x07p\x07p\x07\x00\x00'
        if bytes(v) == b'\x07\x06p\x07\x80\x0c\x01\x00':
            print('click')
            click_event.set()
        # print("RX", bytes(v))

    central.on_notify(on_rx)

    central.enable_notify()

    while central.is_connected():
        await asyncio.sleep(0.5)

    print("Disconnected")
    await send_event('CtrlBtnDisconnected')

wlan = network.WLAN(network.STA_IF)

async def conn_wifi():
    wlan.active(True)
    # wlan.config(pm = 0xa11140)  # Disable power-save mode
    wlan.connect(WIFI_SSID, WIFI_KEY)
    
    max_wait = 10
    while max_wait > 0:
        if wlan.status() not in (network.STAT_IDLE, network.STAT_CONNECTING):
            break
        max_wait -= 1
        print('waiting for connection...')
        await asyncio.sleep(1)

    if wlan.status() != network.STAT_GOT_IP:
        raise RuntimeError('network connection failed')
    
    print('ip addr:', wlan.ifconfig()[0])

async def ble_task():
    while True:
        try:
            await demo()
        except:
            ...
        await asyncio.sleep(1)
    ...

ws = None
identified = False

async def send_event(event_type):
    if ws is None or not identified: return
    await ws.send(json.dumps({
        'op': 6,
        'd': {
            'requestType': 'BroadcastCustomEvent',
            'requestId': str(my_uuid.uuid4()),
            'requestData': {
                'eventData': {
                    'type': event_type,
                }
            }
        }
    }))

async def wifi_related_task():
    while True:
        try:
            if wlan.status() != network.STAT_GOT_IP:
                await conn_wifi()
            await asyncio.gather(
                report_task()
            )
        except: ...
        await asyncio.sleep(1)
    ...

# https://github.com/Vovaman/example_async_websocket/blob/master/src/main_f/func.py
async def report_task():
    global ws
    global identified
    ws_password = 'DKgIh7edWmEa3KvC'
    while True:
        try:
            identified = False
            if wlan.status() != network.STAT_GOT_IP:
                break
            ws = AsyncWebsocketClient()
            print("Handshaking...")
            if not await ws.handshake("ws://192.168.1.3:4455"):
                raise Exception('Handshake error.')
            print("...handshaked.")

            hello_message = None
            async def ws_recv():
                nonlocal hello_message
                global identified
                while True:
                    try:
                        # print('recving data')
                        message = await ws.recv()
                        # print(f'Received message:\n{message}')
                        incoming_payload = json.loads(message)
                        op_code = incoming_payload['op']
                        data_payload = incoming_payload['d']
                        if op_code == 0: # Hello
                            hello_message = data_payload
                            identify_message = {'op': 1, 'd': {}}
                            identify_message['d']['rpcVersion'] = RPC_VERSION
                            if 'authentication' in hello_message:
                                secret = ubinascii.b2a_base64(hashlib.sha256((ws_password + hello_message['authentication']['salt']).encode('utf-8')).digest(), newline=False)
                                authentication_string = ubinascii.b2a_base64(hashlib.sha256(secret + (hello_message['authentication']['challenge'].encode('utf-8'))).digest(), newline=False).decode('utf-8')
                                identify_message['d']['authentication'] = authentication_string
                            await ws.send(json.dumps(identify_message))
                        if op_code == 2: # Identified
                            print('Identified')
                            identified = True
                            ...
                    except asyncio.CancelledError:
                        print('ws_recv cancelled')
                        break
                    except: continue
                identified = False
            recv_task = asyncio.create_task(ws_recv())

            wdt = WDT(timeout=5000)

            while await ws.open():
                # get queue data
                try:
                    wdt.feed()
                    await asyncio.wait_for(click_event.wait(), 1)
                    print('click sending!')
                    await send_event('StartTranscribe')
                except:
                    ...
                ...

            
            print('cancelling recv_task...')
            recv_task.cancel()
            print('obs ws disconnected.')
        except: 
            print('error. retry')
        await asyncio.sleep(1)

        ...
    ...

# 创建任务
async def main():
    for i in range(10):
        print(f'waiting {10 - i}s')
        await asyncio.sleep(1)
    await asyncio.gather(
        ble_task(),
        wifi_related_task()
    )
    # while True:
    #     await demo()
    #     await asyncio.sleep(1)

if __name__ == "__main__":
    asyncio.run(main())
