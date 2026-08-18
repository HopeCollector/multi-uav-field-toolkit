import unittest

from multi_uav_field_toolkit.monitor.common.protocol import split_image_frame
from multi_uav_field_toolkit.monitor.host.aggregator import IMAGE_ERROR_MS, MonitorAggregator


class AgentRestartFrameTests(unittest.TestCase):
    def test_lower_frame_id_with_newer_agent_time_is_accepted(self):
        aggregator = MonitorAggregator()
        for packet in split_image_frame(
            uav_id="uav1",
            frame_id=637,
            timestamp_monotonic_ms=1000,
            frame=b"\xff\xd8old-frame\xff\xd9",
            max_payload_bytes=256,
        ):
            aggregator.ingest_image_packet(packet, ("127.0.0.1", 1))

        for packet in split_image_frame(
            uav_id="uav1",
            frame_id=1,
            timestamp_monotonic_ms=2000,
            frame=b"\xff\xd8new-frame\xff\xd9",
            max_payload_bytes=256,
        ):
            aggregator.ingest_image_packet(packet, ("127.0.0.1", 1))

        snapshot = aggregator.snapshot()
        host = snapshot["uavs"][0]["status"]["host"]
        self.assertEqual(host["image_frame_id"], 1)
        self.assertEqual(host["image_agent_monotonic_ms"], 2000)
        self.assertEqual(aggregator.latest_image("uav1"), b"\xff\xd8new-frame\xff\xd9")

    def test_lower_frame_id_after_stale_image_is_accepted(self):
        aggregator = MonitorAggregator()
        for packet in split_image_frame(
            uav_id="uav1",
            frame_id=637,
            timestamp_monotonic_ms=100000,
            frame=b"\xff\xd8old-frame\xff\xd9",
            max_payload_bytes=256,
        ):
            aggregator.ingest_image_packet(packet, ("127.0.0.1", 1))

        aggregator._uavs["uav1"].latest_image_received_monotonic_ms -= IMAGE_ERROR_MS + 1

        for packet in split_image_frame(
            uav_id="uav1",
            frame_id=1,
            timestamp_monotonic_ms=1000,
            frame=b"\xff\xd8new-frame\xff\xd9",
            max_payload_bytes=256,
        ):
            aggregator.ingest_image_packet(packet, ("127.0.0.1", 1))

        snapshot = aggregator.snapshot()
        host = snapshot["uavs"][0]["status"]["host"]
        self.assertEqual(host["image_frame_id"], 1)
        self.assertEqual(host["image_agent_monotonic_ms"], 1000)
        self.assertEqual(aggregator.latest_image("uav1"), b"\xff\xd8new-frame\xff\xd9")


if __name__ == "__main__":
    unittest.main()
