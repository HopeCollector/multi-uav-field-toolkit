# Monitor protocol

The protocol is intentionally small and one-way. It is suitable for synthetic demos and
isolated lab networks, not hostile networks.

## Status packet

A status packet is a UTF-8 JSON object sent over UDP. It must include `uav_id`. IDs accept
1–64 ASCII characters matching `[A-Za-z0-9][A-Za-z0-9_.-]*`. The encoded packet is capped
at 16 KiB; malformed, deeply nested, and oversized JSON is rejected without stopping the
listener. JSON nesting is capped at 64 levels independently of the Python parser version.

The host treats all other fields as display data. The mock sender demonstrates the current
shape; consumers should tolerate missing fields.

## Image packet

An image is a JPEG split across UDP packets. Each packet has a fixed network-byte-order
header, the UTF-8 UAV ID, and one payload chunk.

| Field | Type | Meaning |
| --- | --- | --- |
| magic | 4 bytes | `UIMG` |
| version | uint8 | currently `1` |
| UAV ID length | uint8 | 1–64 bytes |
| header length | uint16 | fixed header plus UAV ID |
| frame ID | uint64 | sender-local sequence |
| monotonic time | uint64 | sender monotonic milliseconds |
| chunk index/count | 2 × uint16 | frame position |
| payload length | uint32 | bytes in this packet |
| CRC-32 | uint32 | payload integrity check |

The public host rejects frames larger than 8 MiB, more than 8192 chunks, chunks above
60,000 bytes, unsafe IDs, invalid UTF-8, and bad CRC values. It retains one incomplete frame
per UAV and expires an incomplete assembly after three seconds.

## Security properties

CRC detects accidental payload corruption; it is not authentication. Packets can be
spoofed or replayed. Keep the default loopback bind unless a separate authenticated tunnel
or trusted isolated network supplies the missing boundary.
