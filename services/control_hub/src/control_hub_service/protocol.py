from __future__ import annotations

from dataclasses import dataclass


SYNC = b"\xAA\x55\xAA\x55"
HOST_ID = 0x00
MODULE_ID = 0x15
MODULE_NAME = "control_hub_module"
FRAME_MAX = 200
TYPE_CMD = 0x01
TYPE_CONTROL = 0x04


@dataclass(frozen=True)
class Frame:
    message: str
    message_type: int
    sequence: int


def build_control(command: int) -> bytes:
    return SYNC + bytes((HOST_ID, command & 0xFF))


def build_stream(message: str, message_type: int, sequence: int) -> bytes:
    payload = str(message).encode("utf-8")
    if not payload or len(payload) > FRAME_MAX:
        raise ValueError(f"message must contain between 1 and {FRAME_MAX} UTF-8 bytes")
    return (
        SYNC + bytes((len(payload),)) + payload + bytes((message_type & 0xFF,))
        + (int(sequence) & 0xFFFFFF).to_bytes(3, "big")
    )


class StreamParser:
    def __init__(self) -> None:
        self.buffer = bytearray()

    def feed(self, chunk: bytes) -> list[Frame]:
        self.buffer.extend(chunk)
        frames: list[Frame] = []
        while True:
            start = self.buffer.find(SYNC)
            if start < 0:
                self.buffer[:] = self.buffer[-(len(SYNC) - 1):]
                break
            if start:
                del self.buffer[:start]
            if len(self.buffer) < len(SYNC) + 1:
                break
            size = self.buffer[len(SYNC)]
            if not 1 <= size <= FRAME_MAX:
                del self.buffer[0]
                continue
            total = len(SYNC) + 1 + size + 1 + 3
            if len(self.buffer) < total:
                break
            raw = bytes(self.buffer[:total])
            del self.buffer[:total]
            offset = len(SYNC) + 1
            payload = raw[offset:offset + size]
            frames.append(Frame(
                message=payload.decode("utf-8", errors="replace"),
                message_type=raw[offset + size],
                sequence=int.from_bytes(raw[offset + size + 1:offset + size + 4], "big"),
            ))
        if len(self.buffer) > 4096:
            self.buffer[:] = self.buffer[-4096:]
        return frames


def control_response(buffer: bytes, command: int) -> tuple[bool, str | None]:
    """Read the unsequenced response used before stream communication starts."""
    marker = SYNC + bytes((MODULE_ID, command & 0xFF))
    start = buffer.find(marker)
    if start < 0:
        return False, None
    if command == 0x06:
        return True, None
    length_offset = start + len(marker)
    if len(buffer) <= length_offset:
        return False, None
    size = buffer[length_offset]
    end = length_offset + 1 + size
    if len(buffer) < end:
        return False, None
    return True, buffer[length_offset + 1:end].decode("utf-8", errors="replace")
