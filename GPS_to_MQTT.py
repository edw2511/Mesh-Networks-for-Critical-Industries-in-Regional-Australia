import json
import time

import serial
import paho.mqtt.client as mqtt

# Raspberry Pi GNSS UART configuration
GPS_PORT = "/dev/serial0"  # GPS serial port
GPS_BAUD = 9600            # Default NMEA baud rate of the GNSS receiver

# Existing MQTT broker configuration
MQTT_HOST = "13.236.148.255"
MQTT_PORT = 1883
MQTT_USERNAME = "test"
MQTT_PASSWORD = "holograms2022" 

# Set repeater ID to sepcify the topic payload will be published to
REPEATER_ID = "01"
MQTT_TOPIC = f"lora/repeaters/{REPEATER_ID}/location"

PUBLISH_INTERVAL = 10  # Minimum seconds between position publications

# Converts NMEA ddmm.mmmm/dddmm.mmmm into signed decimal degrees.
# The hemisphere is required to distinguish north/south/east/west
def nmea_to_decimal(value: str, hemisphere: str) -> float:
    
    if hemisphere not in ("N", "S", "E", "W"):
        raise ValueError("Invalid NMEA hemisphere")

    raw = float(value)
    degrees = int(raw // 100)
    minutes = raw - (degrees * 100)
    if not 0 <= minutes < 60:
        raise ValueError("Invalid NMEA minutes")

    decimal = degrees + minutes / 60.0
    return -decimal if hemisphere in ("S", "W") else decimal

# Extract a valid location from a GPS/GNSS GGA NMEA sentence.
def parse_gga(sentence: str):

    if not sentence.startswith(("$GNGGA,", "$GPGGA,")):
        return None

    # Ignore a sentence if its NMEA checksum is missing or incorrect
    if "*" not in sentence:
        return None
    body, checksum = sentence[1:].split("*", 1)
    try:
        expected = int(checksum[:2], 16)
    except ValueError:
        return None
    actual = 0
    for character in body:
        actual ^= ord(character)
    if actual != expected:
        return None

    fields = body.split(",")
    if len(fields) < 9:
        return None

    try:
        fix_quality = int(fields[6])
        satellites = int(fields[7])
        if fix_quality <= 0:  # 0 means the receiver has no valid GPS fix
            return None

        latitude = nmea_to_decimal(fields[2], fields[3])
        longitude = nmea_to_decimal(fields[4], fields[5])
        if not (-90 <= latitude <= 90 and -180 <= longitude <= 180):
            return None
    except (ValueError, IndexError):
        return None

    # These keys are read by Home Assistant's MQTT device_tracker
    return {
        "latitude": round(latitude, 7),
        "longitude": round(longitude, 7),
        "gps_fix": True,
        "satellites": satellites,
    }

# Connect to the existing broker and start MQTT network processing
def create_mqtt_client():
    # Support Paho MQTT 1.x (older Raspberry Pi packages) and 2.x
    if hasattr(mqtt, "CallbackAPIVersion"):
        client = mqtt.Client(callback_api_version=mqtt.CallbackAPIVersion.VERSION2)
    else:
        client = mqtt.Client()

    client.username_pw_set(MQTT_USERNAME, MQTT_PASSWORD)
    client.connect(MQTT_HOST, MQTT_PORT, keepalive=60)
    client.loop_start()  # Handle MQTT acknowledgements in a background thread
    return client

# Read GNSS continuously; publish at most once every 10 seconds
def main():
    client = create_mqtt_client()
    last_publish = 0.0

    try:
        with serial.Serial(GPS_PORT, GPS_BAUD, timeout=1) as gps:
            print(f"Repeater {REPEATER_ID}: GNSS -> {MQTT_TOPIC}")
            while True:
                # Non-ASCII bytes are ignored rather than crashing the process
                sentence = gps.readline().decode("ascii", errors="ignore").strip()
                position = parse_gga(sentence)
                if position is None:
                    continue  # Ignore invalid NMEA or GPS fixes

                now = time.monotonic()
                if now - last_publish < PUBLISH_INTERVAL:
                    continue  # Limit traffic to one message every 10 seconds

                # QoS 1 requests broker acknowledgement; retain=True makes
                # the latest coordinates available to new MQTT subscribers
                result = client.publish(
                    MQTT_TOPIC, json.dumps(position), qos=1, retain=True
                )
                result.wait_for_publish()
                if result.rc != mqtt.MQTT_ERR_SUCCESS:
                    print(f"MQTT publish error: {result.rc}")
                    continue

                last_publish = now
                print(f"Published: {position}")

    except KeyboardInterrupt:
        print("GPS publisher stopped by user")
    finally:
        # Cleanly stop MQTT networking when the script exits
        client.loop_stop()
        client.disconnect()


if __name__ == "__main__":
    main()
