import time
import serial

def open_serial(port: str, baud: int, timeout: float):
    return serial.Serial(port, baudrate=baud, timeout=timeout)

def relay_loop(ser: serial.Serial):
    """
    Minimal loop: reads lines from ESP and prints them.
    Extend this later to parse messages and forward to network/MQTT/etc.
    """
    while True:
        line = ser.readline()
        if not line:
            continue
        text = line.decode(errors="ignore").strip()
        if text:
            print(f"[esp] {text}", flush=True)

def run_with_retries(port: str, baud: int, timeout: float, retry_delay: float):
    while True:
        try:
            print(f"[comms] Opening serial {port} @ {baud}", flush=True)
            with open_serial(port, baud, timeout) as ser:
                relay_loop(ser)
        except Exception as exc:
            print(f"[comms] Error: {exc} — retrying in {retry_delay}s", flush=True)
            time.sleep(retry_delay)
