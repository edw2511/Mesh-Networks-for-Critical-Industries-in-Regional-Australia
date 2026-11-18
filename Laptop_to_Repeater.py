import serial        # PySerial library for UART communication
import time          # Used for sleep delays between commands
import asyncio
import struct

from winsdk.windows.devices.geolocation import Geolocator

# Configure port and serial API parameters
PORT     = "COM3"               # laptop COM port for source node's USB-to-TTL adapter
BAUD     = 115200               # baud rate of UART port
BYTESIZE = serial.EIGHTBITS     # data bits set to 8
PARITY   = serial.PARITY_NONE   # set no parity bits
STOPBITS = serial.STOPBITS_ONE  # set one stopbit

# Setting network identifiers and parameters
NODE_ID  = "0001"               # source node unique device ID - dongle
PAN_ID   = "FFFE"               # network unique ID
QOS      = "06"                 # degree of priority, set to repeater AND remote ACK required
# PAYLOAD  = "48454C4C4F"       # payload is in hex string format, hex-encoded ASCII "HELLO"

# Obtain current laptop coordinates from Windows Location Services
async def get_laptop_location_async():

    # Create Windows geolocation object
    locator = Geolocator()

    # Request a location measurement
    position = await locator.get_geoposition_async()

    # Extract coordinates
    latitude = position.coordinate.point.position.latitude
    longitude = position.coordinate.point.position.longitude

    return latitude, longitude


# Synchronous wrapper for the asynchronous location function
def get_laptop_location():

    try:
        latitude, longitude = asyncio.run(
            get_laptop_location_async()
        )

        print("\nLaptop location acquired:")
        print(f"  Latitude : {latitude:.7f}")
        print(f"  Longitude: {longitude:.7f}")

        return latitude, longitude

    except Exception as e:
        print(f"Failed to obtain laptop location: {e}")
        return None

# Convert GPS coordinates into ASCII hexadecimal
def gps_to_hex(latitude, longitude):

    # Format coordinates as comma-separated ASCII text
    gps_string = f"{latitude:.6f},{longitude:.6f}"

    # Convert ASCII string into hexadecimal
    gps_hex = gps_string.encode("ascii").hex().upper()

    print("\nGPS payload encoding:")
    print(f"  GPS coordinates : {gps_string}")
    print(f"  Payload (hex)   : {gps_hex}")
    print(f"  Payload length  : {len(gps_hex)} hex characters")

    return gps_hex

# Reads full UART response until timeout or short silence
def read_response(ser, timeout=3.0):
    response = ""
    deadline = time.time() + timeout
    last_rx = time.time()

    while time.time() < deadline:
        if ser.in_waiting:
            chunk = ser.read(ser.in_waiting).decode("utf-8", errors="replace")
            response += chunk
            last_rx = time.time()
            deadline = last_rx + 0.3
        else:
            if response and (time.time() - last_rx) > 0.3:
                break
        time.sleep(0.02)
    return response

# AT Command Helper Function - formats, encodes, submits and prints AT commands
def send_cmd(ser, cmd, wait=1.0):
    ser.reset_input_buffer()                                        # reset input buffer to avoid overlap with old bytes
    ser.write((cmd + "\r\n").encode("utf-8"))                       # add terminator, encode to bytes and write to UART
    print(f">> {cmd}")                                              # print sent command for visibility
    time.sleep(wait)                                                # forced processing delay before reading
    resp = read_response(ser, timeout=max(wait, 1.0))
    print(f"<< {resp.strip()}")                                     # print raw device response to console
    return resp

# Network Joiner Function - repeatedly polls AT+STATUS=? to confirm connection
# AT+JOIN returns event +EVT= CONNECTED X, AT+STATUS returns IDLE/TXRX/BUZY 
def wait_for_repeater(ser, retries=20, interval=3):
    # Read UART Response 20 times for impending repeater data
    for i in range(retries):
        async_buf = read_response(ser, timeout=0.5)
        if async_buf:
            print(f"[async] {async_buf.strip()}")
            if "CONNECTED" in async_buf:
                print("Joined repeater successfully.")
                return True
        
        # Check status of connection to repeater 
        resp = send_cmd(ser, "AT+STATUS=?", wait=interval)
        if "IDLE" in resp or "TXRX" in resp or "CONNECTED" in resp:
            print("Status confirms repeater connection.")
            return True

        print(f"Still searching... ({i+1}/{retries})")

    return False

# Parse the response of AT+CHAN_INFO=1 (blockMode)
def show_channel_info(resp):
    # Expected response is +RES=BUSY, ERROR, TIMEOUT after +RES=OK (received command)
    if "+RES=" not in resp or "," not in resp:
        print("No valid channel info returned.")
        return

    # Put response data into a indexed string based on whitespace 
    data = resp.split("+RES=")[-1].strip().rstrip(",")
    fields = data.split(",")

    # Print in repeater data fields in more readable format
    print("Visible repeater channels:")
    for i in range(0, len(fields) - 3, 4):
        rid = fields[i]
        idx = fields[i + 1]
        rss = fields[i + 2]
        snr = fields[i + 3]
        print(f"  Repeater ID={rid}  idx={idx}  RSS={rss} dBm  SNR={snr} dB")

# Parse +RECEIVE event
def wait_for_receive(ser, timeout=8.0):
    # Check if UART response contains +RECEIVE
    resp = read_response(ser, timeout=timeout)
    if "+RECEIVE=" not in resp:
        print("No ACK / reply received in window.")
        return

    # Put +RECEIVE data into a indexed string based on whitespace
    line = resp.split("+RECEIVE=")[-1].strip().splitlines()[0]
    parts = line.split(",")

    if len(parts) < 5:
        print(f"Malformed receive line: {line}")
        return

    source = parts[0]
    src_repeater = parts[1]
    msg_type = parts[2]
    payload = parts[3]
    session = parts[4]

    # Decode compact GPS coordinates into processable format
    try:
        gps_bytes = bytes.fromhex(payload)

        if len(gps_bytes) == 8:
            lat_int, lon_int = struct.unpack(">ii", gps_bytes)

            rx_latitude = lat_int / 10000000
            rx_longitude = lon_int / 10000000

            decoded_payload = (
                f"Latitude={rx_latitude:.7f}, "
                f"Longitude={rx_longitude:.7f}"
            )
        else:
            decoded_payload = "Not an 8-byte GPS payload"

    except (ValueError, struct.error):
        decoded_payload = "Invalid GPS payload"

    print("Received async event:")
    print(f"  Source         : {source}")
    print(f"  Source repeater: {src_repeater}")
    print(f"  Message type   : {msg_type}")
    print(f"  Payload (hex)  : {payload}")
    print(f"  Payload (GPS)  : {decoded_payload}")
    print(f"  Session ID     : {session}")


def main(): 
    # Retrieve current laptop coordinates
    location = get_laptop_location()

    # Stop if coordinates cannot be obtained
    if location is None:
        print("GPS acquisition failed. Transmission cancelled.")
        return

    # Extract latitude and longitude
    latitude, longitude = location

    # Convert coordinates to hexadecimal payload
    payload = gps_to_hex(latitude, longitude)
    
    # Defining parameters of serial input
    with serial.Serial(
        PORT,
        baudrate=BAUD,
        bytesize=BYTESIZE,
        parity=PARITY,
        stopbits=STOPBITS,
        timeout=3,
        dsrdtr=False,
        rtscts=False
    ) as ser:

        time.sleep(1)

        # Reset end device
        send_cmd(ser, "AT+RESET", wait=2.5)

        # Assign unique device ID
        send_cmd(ser, f"AT+SET_ID={NODE_ID},{PAN_ID}", wait=1)

        # Start searching for an existing repeater
        send_cmd(ser, "AT+JOIN", wait=1)

        #  Wait until repeater is joined
        print("\nWaiting to join repeater...")
        if not wait_for_repeater(ser):
            print("Failed to join repeater.")
            return

        # Request for repeater channel info
        print("\nRequesting repeater channel info...")
        chan_resp = send_cmd(ser, "AT+CHAN_INFO=1", wait=22)
        show_channel_info(chan_resp)

        # Send message with GPS coordinates to destination end device 0001
        print(f"\nSending GPS coordinates to destination device {PAN_ID} ...")
        send_resp = send_cmd(
            ser,
            f"AT+SEND={PAN_ID},0,{payload},{QOS}",
            wait=22
        )

        if "+STATUS=" in send_resp and "OK" in send_resp:
            print("Message accepted by network.")
        else:
            print("Send did not complete successfully.")
            return

        # Wait for ACK or reply
        print("\nWaiting for ACK / reply...")
        wait_for_receive(ser, timeout=8.0)


if __name__ == "__main__":
    main()