import unittest

from multi_uav_field_toolkit.monitor.common.protocol import (
    MAX_IMAGE_FRAME_BYTES,
    ProtocolError,
    decode_status_packet,
    split_image_frame,
    unpack_image_chunk,
)


class ProtocolTests(unittest.TestCase):
    def test_image_frame_round_trip(self):
        frame = b"abcdef" * 200
        packets = split_image_frame(
            uav_id="uav1",
            frame_id=42,
            timestamp_monotonic_ms=1234,
            frame=frame,
            max_payload_bytes=256,
        )

        chunks = [unpack_image_chunk(packet) for packet in packets]
        self.assertGreater(len(chunks), 1)
        self.assertEqual(chunks[0].uav_id, "uav1")
        self.assertEqual(chunks[0].frame_id, 42)
        self.assertEqual(b"".join(chunk.payload for chunk in chunks), frame)

    def test_crc_rejects_corrupted_payload(self):
        packet = bytearray(
            split_image_frame(
                uav_id="uav1",
                frame_id=1,
                timestamp_monotonic_ms=1,
                frame=b"payload",
                max_payload_bytes=256,
            )[0]
        )
        packet[-1] ^= 0xFF

        with self.assertRaises(ProtocolError):
            unpack_image_chunk(bytes(packet))

    def test_status_rejects_unsafe_uav_id(self):
        with self.assertRaises(ProtocolError):
            decode_status_packet(b'{"uav_id":"../../escape"}')

    def test_status_rejects_pathological_json_without_leaking_exception(self):
        deeply_nested = b'{"uav_id":"uav1","data":' + b"[" * 1500 + b"0" + b"]" * 1500 + b"}"
        with self.assertRaises(ProtocolError):
            decode_status_packet(deeply_nested)

        huge_integer = b'{"uav_id":"uav1","data":' + b"9" * 5000 + b"}"
        with self.assertRaises(ProtocolError):
            decode_status_packet(huge_integer)

    def test_rejects_oversized_image(self):
        with self.assertRaises(ProtocolError):
            split_image_frame(
                uav_id="uav1",
                frame_id=1,
                timestamp_monotonic_ms=1,
                frame=b"x" * (MAX_IMAGE_FRAME_BYTES + 1),
            )


if __name__ == "__main__":
    unittest.main()
