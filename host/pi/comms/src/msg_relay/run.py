import os
from .constants import (
    DEFAULT_SERIAL_PORT,
    DEFAULT_SERIAL_BAUD,
    READ_TIMEOUT_SEC,
    RETRY_DELAY_SEC,
)
from .functions import run_with_retries

def main():
    port = os.getenv("SERIAL_PORT", DEFAULT_SERIAL_PORT)
    baud = int(os.getenv("SERIAL_BAUD", str(DEFAULT_SERIAL_BAUD)))
    run_with_retries(
        port=port,
        baud=baud,
        timeout=READ_TIMEOUT_SEC,
        retry_delay=RETRY_DELAY_SEC,
    )

if __name__ == "__main__":
    main()
