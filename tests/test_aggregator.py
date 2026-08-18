import unittest

from multi_uav_field_toolkit.monitor.common.protocol import (
    ProtocolError,
    encode_status_packet,
    split_image_frame,
)
from multi_uav_field_toolkit.monitor.host.aggregator import (
    MonitorAggregator,
    RateMeter,
    level_for_age,
)


class AggregatorTests(unittest.TestCase):
    def test_status_snapshot_contains_host_rates(self):
        aggregator = MonitorAggregator(expected_uavs=["uav1"])
        aggregator.ingest_status_packet(
            encode_status_packet({"uav_id": "uav1", "odom": {"freshness_ms": 100}}),
            ("192.0.2.11", 20000),
        )

        snapshot = aggregator.snapshot()
        status = snapshot["uavs"][0]["status"]
        self.assertEqual(status["host"]["source_ip"], "192.0.2.11")
        self.assertEqual(status["host"]["status_level"], "ok")
        self.assertEqual(status["host"]["odom_level"], "ok")
        self.assertGreaterEqual(status["host"]["status_rx_bps"], 0)

    def test_image_assembly_accepts_out_of_order_chunks(self):
        aggregator = MonitorAggregator()
        frame = b"\xff\xd8" + b"0123456789" * 200 + b"\xff\xd9"
        packets = split_image_frame(
            uav_id="uav1",
            frame_id=10,
            timestamp_monotonic_ms=500,
            frame=frame,
            max_payload_bytes=256,
        )

        for packet in reversed(packets):
            aggregator.ingest_image_packet(packet, ("192.0.2.11", 20001))

        snapshot = aggregator.snapshot()
        self.assertEqual(snapshot["uavs"][0]["status"]["host"]["image_frame_id"], 10)
        self.assertTrue(snapshot["uavs"][0]["has_image"])
        self.assertEqual(aggregator.latest_image("uav1"), frame)
        image, version = aggregator.wait_for_image("uav1", -1, timeout_seconds=0.01)
        self.assertEqual(image, frame)
        self.assertGreater(version, 0)

    def test_image_assembly_discards_old_frame(self):
        aggregator = MonitorAggregator()
        for packet in split_image_frame(
            uav_id="uav1",
            frame_id=2,
            timestamp_monotonic_ms=20,
            frame=b"\xff\xd8new\xff\xd9",
            max_payload_bytes=256,
        ):
            aggregator.ingest_image_packet(packet, ("127.0.0.1", 1))
        for packet in split_image_frame(
            uav_id="uav1",
            frame_id=1,
            timestamp_monotonic_ms=10,
            frame=b"\xff\xd8old\xff\xd9",
            max_payload_bytes=256,
        ):
            aggregator.ingest_image_packet(packet, ("127.0.0.1", 1))

        snapshot = aggregator.snapshot()
        self.assertEqual(snapshot["uavs"][0]["status"]["host"]["image_frame_id"], 2)

    def test_image_assembly_rejects_non_jpeg_payload(self):
        aggregator = MonitorAggregator()
        packet = split_image_frame(
            uav_id="uav1",
            frame_id=1,
            timestamp_monotonic_ms=1,
            frame=b"not-a-jpeg",
            max_payload_bytes=256,
        )[0]

        with self.assertRaises(ProtocolError):
            aggregator.ingest_image_packet(packet, ("127.0.0.1", 1))

    def test_rate_meter_window(self):
        meter = RateMeter(window_seconds=2.0)
        meter.add(100, now=10.0)
        meter.add(300, now=11.0)
        self.assertEqual(meter.rate_bps(now=11.0), 200.0)
        self.assertEqual(meter.rate_bps(now=13.1), 0.0)

    def test_level_for_age(self):
        self.assertEqual(level_for_age(None, 10, 20), "missing")
        self.assertEqual(level_for_age(9, 10, 20), "ok")
        self.assertEqual(level_for_age(10, 10, 20), "warn")
        self.assertEqual(level_for_age(20, 10, 20), "error")


if __name__ == "__main__":
    unittest.main()
