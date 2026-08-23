"""Tagged JPEG frame protocol shared by Unreal and the Python SIL client."""

from __future__ import annotations

import dataclasses
import struct
from typing import Protocol


CAMERA_PROTOCOL_VERSION = 1
FRAME_MAGIC = b"SIMG"
ENCODING_JPEG = 1
POSE_METADATA_VALID_FLAG = 0x00000001
KNOWN_FRAME_FLAGS = POSE_METADATA_VALID_FLAG

# Network byte order, exactly 64 bytes:
# magic, version, encoding, header bytes, flags, payload bytes,
# applied run/sequence, dimensions, applied simulation time,
# camera frame, streamer tick, and UE-local capture time.
FRAME_HEADER_STRUCT = struct.Struct("!4sBBHIIQIHHQQQQ")
FRAME_HEADER_BYTES = FRAME_HEADER_STRUCT.size

DEFAULT_MAX_WIDTH = 3840
DEFAULT_MAX_HEIGHT = 2160
DEFAULT_MAX_JPEG_BYTES = 32 * 1024 * 1024


class ReceivingSocket(Protocol):
    def recv(self, buffer_size: int) -> bytes: ...


@dataclasses.dataclass(frozen=True)
class CameraFrameHeader:
    pose_metadata_valid: bool
    jpeg_bytes: int
    run_id: int
    pose_sequence: int
    width: int
    height: int
    simulation_time_ns: int
    camera_frame_id: int
    streamer_tick_id: int
    capture_monotonic_ns: int


@dataclasses.dataclass(frozen=True)
class CameraFrame:
    header: CameraFrameHeader
    jpeg: bytes


def _require_unsigned(name: str, value: int, bits: int) -> None:
    if not 0 <= value < (1 << bits):
        raise ValueError(f"{name} must fit in uint{bits}")


def _validate_header(
    header: CameraFrameHeader,
    *,
    max_width: int,
    max_height: int,
    max_jpeg_bytes: int,
) -> None:
    _require_unsigned("jpeg_bytes", header.jpeg_bytes, 32)
    _require_unsigned("run_id", header.run_id, 64)
    _require_unsigned("pose_sequence", header.pose_sequence, 32)
    _require_unsigned("width", header.width, 16)
    _require_unsigned("height", header.height, 16)
    _require_unsigned("simulation_time_ns", header.simulation_time_ns, 64)
    _require_unsigned("camera_frame_id", header.camera_frame_id, 64)
    _require_unsigned("streamer_tick_id", header.streamer_tick_id, 64)
    _require_unsigned("capture_monotonic_ns", header.capture_monotonic_ns, 64)

    if not 1 <= header.width <= max_width:
        raise ValueError(
            f"camera width must be between 1 and {max_width}, got {header.width}"
        )
    if not 1 <= header.height <= max_height:
        raise ValueError(
            f"camera height must be between 1 and {max_height}, got {header.height}"
        )
    if not 1 <= header.jpeg_bytes <= max_jpeg_bytes:
        raise ValueError(
            "JPEG payload must be between 1 and "
            f"{max_jpeg_bytes} bytes, got {header.jpeg_bytes}"
        )
    if header.camera_frame_id == 0:
        raise ValueError("camera_frame_id zero is reserved")

    if header.pose_metadata_valid:
        if header.run_id == 0:
            raise ValueError("a pose-tagged frame must have a non-zero run_id")
    elif (
        header.run_id != 0
        or header.pose_sequence != 0
        or header.simulation_time_ns != 0
    ):
        raise ValueError("a frame without valid pose metadata must use zero pose fields")


def pack_frame_header(header: CameraFrameHeader) -> bytes:
    """Validate and serialize a camera frame header for tests/tools."""
    _validate_header(
        header,
        max_width=(1 << 16) - 1,
        max_height=(1 << 16) - 1,
        max_jpeg_bytes=(1 << 32) - 1,
    )
    flags = POSE_METADATA_VALID_FLAG if header.pose_metadata_valid else 0
    return FRAME_HEADER_STRUCT.pack(
        FRAME_MAGIC,
        CAMERA_PROTOCOL_VERSION,
        ENCODING_JPEG,
        FRAME_HEADER_BYTES,
        flags,
        header.jpeg_bytes,
        header.run_id,
        header.pose_sequence,
        header.width,
        header.height,
        header.simulation_time_ns,
        header.camera_frame_id,
        header.streamer_tick_id,
        header.capture_monotonic_ns,
    )


def unpack_frame_header(
    data: bytes,
    *,
    max_width: int = DEFAULT_MAX_WIDTH,
    max_height: int = DEFAULT_MAX_HEIGHT,
    max_jpeg_bytes: int = DEFAULT_MAX_JPEG_BYTES,
) -> CameraFrameHeader:
    """Validate and deserialize one fixed-size camera frame header."""
    if len(data) != FRAME_HEADER_BYTES:
        raise ValueError(
            f"camera header must be {FRAME_HEADER_BYTES} bytes, received {len(data)}"
        )

    (
        magic,
        version,
        encoding,
        header_bytes,
        flags,
        jpeg_bytes,
        run_id,
        pose_sequence,
        width,
        height,
        simulation_time_ns,
        camera_frame_id,
        streamer_tick_id,
        capture_monotonic_ns,
    ) = FRAME_HEADER_STRUCT.unpack(data)

    if magic != FRAME_MAGIC:
        raise ValueError(f"unexpected camera magic {magic!r}")
    if version != CAMERA_PROTOCOL_VERSION:
        raise ValueError(f"unsupported camera protocol version {version}")
    if encoding != ENCODING_JPEG:
        raise ValueError(f"unsupported camera encoding {encoding}")
    if header_bytes != FRAME_HEADER_BYTES:
        raise ValueError(f"unsupported camera header size {header_bytes}")
    if flags & ~KNOWN_FRAME_FLAGS:
        raise ValueError(f"unknown camera flags 0x{flags:08X}")

    header = CameraFrameHeader(
        pose_metadata_valid=bool(flags & POSE_METADATA_VALID_FLAG),
        jpeg_bytes=jpeg_bytes,
        run_id=run_id,
        pose_sequence=pose_sequence,
        width=width,
        height=height,
        simulation_time_ns=simulation_time_ns,
        camera_frame_id=camera_frame_id,
        streamer_tick_id=streamer_tick_id,
        capture_monotonic_ns=capture_monotonic_ns,
    )
    _validate_header(
        header,
        max_width=max_width,
        max_height=max_height,
        max_jpeg_bytes=max_jpeg_bytes,
    )
    return header


def recv_exact(sock: ReceivingSocket, byte_count: int) -> bytes:
    """Receive exactly byte_count bytes or raise EOFError on a closed stream."""
    if byte_count < 0:
        raise ValueError("byte_count must not be negative")

    chunks: list[bytes] = []
    remaining = byte_count
    while remaining:
        try:
            chunk = sock.recv(remaining)
        except InterruptedError:
            continue
        if not chunk:
            received = byte_count - remaining
            raise EOFError(
                f"camera stream closed after {received} of {byte_count} bytes"
            )
        chunks.append(chunk)
        remaining -= len(chunk)
    return b"".join(chunks)


def receive_frame(
    sock: ReceivingSocket,
    *,
    max_width: int = DEFAULT_MAX_WIDTH,
    max_height: int = DEFAULT_MAX_HEIGHT,
    max_jpeg_bytes: int = DEFAULT_MAX_JPEG_BYTES,
) -> CameraFrame:
    """Read and validate one complete header-plus-JPEG message from TCP."""
    header_data = recv_exact(sock, FRAME_HEADER_BYTES)
    header = unpack_frame_header(
        header_data,
        max_width=max_width,
        max_height=max_height,
        max_jpeg_bytes=max_jpeg_bytes,
    )
    jpeg = recv_exact(sock, header.jpeg_bytes)
    return CameraFrame(header=header, jpeg=jpeg)
