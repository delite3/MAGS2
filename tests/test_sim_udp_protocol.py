import math
import struct
import unittest

from sim_udp_protocol import (
    ACK_MAGIC,
    ACK_STRUCT,
    POSE_MAGIC,
    POSE_STRUCT,
    PROTOCOL_VERSION,
    PoseCommand,
    pack_pose,
    unpack_ack,
)
from ue_udp_sender import path_pose


class ProtocolTests(unittest.TestCase):
    def test_pose_packet_layout(self):
        packet = pack_pose(
            PoseCommand(
                sequence=42,
                simulation_time_ns=123456,
                position_m=(1.0, -2.0, 3.5),
                quaternion_xyzw=(0.0, 0.0, 0.0, 1.0),
            )
        )
        self.assertEqual(len(packet), 48)
        unpacked = POSE_STRUCT.unpack(packet)
        self.assertEqual(unpacked[:6], (POSE_MAGIC, PROTOCOL_VERSION, 0, 0, 42, 123456))
        self.assertEqual(unpacked[6:], (1.0, -2.0, 3.5, 0.0, 0.0, 0.0, 1.0))

    def test_ack_validation(self):
        packet = ACK_STRUCT.pack(ACK_MAGIC, PROTOCOL_VERSION, 0, 0, 99)
        ack = unpack_ack(packet)
        self.assertEqual(ack.sequence, 99)
        self.assertEqual(ack.status, 0)

    def test_circle_starts_at_origin_and_faces_tangent(self):
        position, quaternion = path_pose("circle", 0.0, 1.0, 2.0, 8.0, 3.0)
        self.assertEqual(position, (0.0, 0.0, 3.0))
        self.assertAlmostEqual(quaternion[2], math.sqrt(0.5))
        self.assertAlmostEqual(quaternion[3], math.sqrt(0.5))

    def test_rejects_bad_ack_magic(self):
        packet = struct.pack("!4sBBHI", b"NOPE", PROTOCOL_VERSION, 0, 0, 1)
        with self.assertRaises(ValueError):
            unpack_ack(packet)


if __name__ == "__main__":
    unittest.main()
