# Protocol

This directory defines the communication contract between Raspberry Pi (host) and ESP firmware.

Initial MVP:
- line-based serial output for diagnostics (printf lines)

Next step:
- define framed messages (e.g., COBS/SLIP) and a versioned message format (CBOR/MsgPack/Protobuf).
