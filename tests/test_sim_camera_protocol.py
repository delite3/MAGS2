import dataclasses
import unittest

from sim_camera_protocol import (
    CAMERA_PROTOCOL_VERSION,
    DEFAULT_MAX_JPEG_BYTES,
    ENCODING_JPEG,
    FRAME_HEADER_BYTES,
    FRAME_HEADER_STRUCT,
    FRAME_MAGIC,
    POSE_METADATA_VALID_FLAG,
    CameraFrameHeader,
    pack_frame_header,
    receive_frame,
    recv_exact,
    unpack_frame_header,
)


class ChunkedSocket:
    def __init__(self, chunks):
        self.chunks = [bytes(chunk) for chunk in chunks]
        self.recv_calls = 0

    def recv(self, buffer_size):
        self.recv_calls += 1
        if not self.chunks:
            return b""
        chunk = self.chunks.pop(0)
        result = chunk[:buffer_size]
        remainder = chunk[buffer_size:]
        if remainder:
            self.chunks.insert(0, remainder)
        return result


def sample_header(**changes):
    header = CameraFrameHeader(
        pose_metadata_valid=True,
        jpeg_bytes=1234,
        run_id=0x0123456789ABCDEF,
        pose_sequence=42,
        width=320,
        height=240,
        simulation_time_ns=123456,
        camera_frame_id=7,
        streamer_tick_id=99,
        capture_monotonic_ns=654321,
    )
    return dataclasses.replace(header, **changes)


class CameraProtocolTests(unittest.TestCase):
    def test_header_layout_is_exactly_shared_with_unreal(self):
        packet = pack_frame_header(sample_header())
        self.assertEqual(FRAME_HEADER_BYTES, 64)
        self.assertEqual(len(packet), 64)
        self.assertEqual(
            FRAME_HEADER_STRUCT.unpack(packet),
            (
                FRAME_MAGIC,
                CAMERA_PROTOCOL_VERSION,
                ENCODING_JPEG,
                FRAME_HEADER_BYTES,
                POSE_METADATA_VALID_FLAG,
                1234,
                0x0123456789ABCDEF,
                42,
                320,
                240,
                123456,
                7,
                99,
                654321,
            ),
        )
        self.assertEqual(
            packet[:32].hex(),
            "53494d470101004000000001000004d2"
            "0123456789abcdef0000002a014000f0",
        )

    def test_round_trip_with_pose_metadata(self):
        expected = sample_header()
        self.assertEqual(unpack_frame_header(pack_frame_header(expected)), expected)

    def test_frame_without_pose_metadata_uses_zero_pose_fields(self):
        expected = sample_header(
            pose_metadata_valid=False,
            run_id=0,
            pose_sequence=0,
            simulation_time_ns=0,
        )
        self.assertEqual(unpack_frame_header(pack_frame_header(expected)), expected)

    def test_rejects_nonzero_pose_fields_without_valid_flag(self):
        with self.assertRaisesRegex(ValueError, "zero pose fields"):
            pack_frame_header(sample_header(pose_metadata_valid=False))

    def test_rejects_zero_run_id_with_valid_pose_flag(self):
        with self.assertRaisesRegex(ValueError, "non-zero run_id"):
            pack_frame_header(sample_header(run_id=0))

    def test_rejects_unknown_flags(self):
        values = list(FRAME_HEADER_STRUCT.unpack(pack_frame_header(sample_header())))
        values[4] = 0x80000000
        with self.assertRaisesRegex(ValueError, "unknown camera flags"):
            unpack_frame_header(FRAME_HEADER_STRUCT.pack(*values))

    def test_rejects_bad_magic_version_encoding_and_header_size(self):
        valid_values = list(
            FRAME_HEADER_STRUCT.unpack(pack_frame_header(sample_header()))
        )
        for index, replacement, message in (
            (0, b"NOPE", "magic"),
            (1, CAMERA_PROTOCOL_VERSION + 1, "version"),
            (2, ENCODING_JPEG + 1, "encoding"),
            (3, FRAME_HEADER_BYTES + 1, "header size"),
        ):
            with self.subTest(field=index):
                values = valid_values.copy()
                values[index] = replacement
                with self.assertRaisesRegex(ValueError, message):
                    unpack_frame_header(FRAME_HEADER_STRUCT.pack(*values))

    def test_rejects_dimensions_and_payload_over_receiver_limits(self):
        packet = pack_frame_header(sample_header(width=640, height=480))
        with self.assertRaisesRegex(ValueError, "camera width"):
            unpack_frame_header(packet, max_width=320)
        with self.assertRaisesRegex(ValueError, "camera height"):
            unpack_frame_header(packet, max_height=240)
        with self.assertRaisesRegex(ValueError, "JPEG payload"):
            unpack_frame_header(packet, max_jpeg_bytes=1000)

    def test_recv_exact_handles_split_tcp_reads(self):
        sock = ChunkedSocket([b"a", b"bc", b"def"])
        self.assertEqual(recv_exact(sock, 6), b"abcdef")
        self.assertEqual(sock.recv_calls, 3)

    def test_recv_exact_reports_early_eof(self):
        sock = ChunkedSocket([b"abc"])
        with self.assertRaisesRegex(EOFError, "3 of 6 bytes"):
            recv_exact(sock, 6)

    def test_receive_frame_reads_header_then_jpeg(self):
        jpeg = b"\xff\xd8test-jpeg\xff\xd9"
        header = sample_header(jpeg_bytes=len(jpeg))
        message = pack_frame_header(header) + jpeg
        sock = ChunkedSocket([message[:3], message[3:67], message[67:]])
        frame = receive_frame(sock)
        self.assertEqual(frame.header, header)
        self.assertEqual(frame.jpeg, jpeg)

    def test_oversized_payload_is_rejected_before_payload_read(self):
        values = list(FRAME_HEADER_STRUCT.unpack(pack_frame_header(sample_header())))
        values[5] = DEFAULT_MAX_JPEG_BYTES + 1
        sock = ChunkedSocket([FRAME_HEADER_STRUCT.pack(*values)])
        with self.assertRaisesRegex(ValueError, "JPEG payload"):
            receive_frame(sock)
        self.assertEqual(sock.recv_calls, 1)


if __name__ == "__main__":
    unittest.main()
