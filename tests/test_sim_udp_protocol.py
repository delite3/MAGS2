import math
import struct
import unittest

from sim_udp_protocol import (
    ACK_APPLIED,
    ACK_MAGIC,
    ACK_STRUCT,
    COMMAND_MAGIC,
    COMMAND_STRUCT,
    GEOREFERENCE_MAGIC,
    GEOREFERENCE_STRUCT,
    PROTOCOL_VERSION,
    START_RUN_FLAG,
    GeoreferenceCommand,
    PoseCommand,
    pack_georeference,
    pack_pose,
    unpack_ack,
)
from ue_udp_sender import path_pose


class ProtocolTests(unittest.TestCase):
    def test_command_packet_layout(self):
        packet = pack_pose(
            PoseCommand(
                run_id=0x0123456789ABCDEF,
                sequence=42,
                simulation_time_ns=123456,
                position_m=(1.0, -2.0, 3.5),
                quaternion_xyzw=(0.0, 0.0, 0.0, 1.0),
                start_of_run=True,
            )
        )
        self.assertEqual(len(packet), 56)
        unpacked = COMMAND_STRUCT.unpack(packet)
        self.assertEqual(
            unpacked[:7],
            (
                COMMAND_MAGIC,
                PROTOCOL_VERSION,
                START_RUN_FLAG,
                0,
                0x0123456789ABCDEF,
                42,
                123456,
            ),
        )
        self.assertEqual(unpacked[7:], (1.0, -2.0, 3.5, 0.0, 0.0, 0.0, 1.0))
        self.assertEqual(
            packet[:28].hex(),
            "53554450020100000123456789abcdef0000002a000000000001e240",
        )

    def test_sequence_one_is_distinct_across_runs(self):
        common = dict(
            sequence=1,
            simulation_time_ns=0,
            position_m=(0.0, 0.0, 0.0),
            quaternion_xyzw=(0.0, 0.0, 0.0, 1.0),
            start_of_run=True,
        )
        first = pack_pose(PoseCommand(run_id=1, **common))
        second = pack_pose(PoseCommand(run_id=2, **common))
        self.assertNotEqual(first, second)
        self.assertEqual(COMMAND_STRUCT.unpack(first)[5], 1)
        self.assertEqual(COMMAND_STRUCT.unpack(second)[5], 1)

    def test_georeference_packet_layout(self):
        packet = pack_georeference(
            GeoreferenceCommand(
                run_id=7,
                latitude_deg=48.8566,
                longitude_deg=2.3522,
                ellipsoid_height_m=35.5,
            )
        )
        self.assertEqual(len(packet), GEOREFERENCE_STRUCT.size)
        self.assertEqual(
            GEOREFERENCE_STRUCT.unpack(packet),
            (GEOREFERENCE_MAGIC, PROTOCOL_VERSION, 0, 0, 7, 48.8566, 2.3522, 35.5),
        )

    def test_ack_layout_and_validation(self):
        packet = ACK_STRUCT.pack(
            ACK_MAGIC,
            PROTOCOL_VERSION,
            ACK_APPLIED,
            0,
            0x0123456789ABCDEF,
            99,
        )
        ack = unpack_ack(packet)
        self.assertEqual(ack.run_id, 0x0123456789ABCDEF)
        self.assertEqual(ack.sequence, 99)
        self.assertEqual(ack.status, ACK_APPLIED)

    def test_circle_starts_at_origin_and_faces_tangent(self):
        position, quaternion = path_pose("circle", 0.0, 1.0, 2.0, 8.0, 3.0)
        self.assertEqual(position, (0.0, 0.0, 3.0))
        self.assertAlmostEqual(quaternion[2], math.sqrt(0.5))
        self.assertAlmostEqual(quaternion[3], math.sqrt(0.5))

    def test_object_height_is_independent_of_path_profile(self):
        for path in ("line", "circle", "hover"):
            with self.subTest(path=path):
                position, _ = path_pose(path, 1.25, 2.0, 4.0, 8.0, 12.5)
                self.assertEqual(position[2], 12.5)

    def test_rejects_bad_ack_magic(self):
        packet = struct.pack(
            "!4sBBHQI", b"NOPE", PROTOCOL_VERSION, 0, 0, 1, 1
        )
        with self.assertRaises(ValueError):
            unpack_ack(packet)

    def test_rejects_non_finite_pose(self):
        with self.assertRaises(ValueError):
            pack_pose(
                PoseCommand(
                    run_id=1,
                    sequence=1,
                    simulation_time_ns=0,
                    position_m=(float("nan"), 0.0, 0.0),
                    quaternion_xyzw=(0.0, 0.0, 0.0, 1.0),
                )
            )

    def test_rejects_zero_quaternion(self):
        with self.assertRaises(ValueError):
            pack_pose(
                PoseCommand(
                    run_id=1,
                    sequence=1,
                    simulation_time_ns=0,
                    position_m=(0.0, 0.0, 0.0),
                    quaternion_xyzw=(0.0, 0.0, 0.0, 0.0),
                )
            )

    def test_rejects_reserved_zero_run_id(self):
        with self.assertRaises(ValueError):
            pack_pose(
                PoseCommand(
                    run_id=0,
                    sequence=1,
                    simulation_time_ns=0,
                    position_m=(0.0, 0.0, 0.0),
                    quaternion_xyzw=(0.0, 0.0, 0.0, 1.0),
                )
            )

    def test_rejects_invalid_georeference(self):
        with self.assertRaises(ValueError):
            pack_georeference(GeoreferenceCommand(0, 0.0, 0.0, 0.0))
        with self.assertRaises(ValueError):
            pack_georeference(GeoreferenceCommand(1, 91.0, 0.0, 0.0))
        with self.assertRaises(ValueError):
            pack_georeference(GeoreferenceCommand(1, 0.0, 181.0, 0.0))
        with self.assertRaises(ValueError):
            pack_georeference(GeoreferenceCommand(1, 0.0, 0.0, float("nan")))


if __name__ == "__main__":
    unittest.main()
