"""Binary protocol shared by the Python SIL client and Unreal plugin."""

from __future__ import annotations

import dataclasses
import struct


PROTOCOL_VERSION = 1
POSE_MAGIC = b"SUDP"
ACK_MAGIC = b"SACK"

# Network byte order. Quaternion component order is X, Y, Z, W.
POSE_STRUCT = struct.Struct("!4sBBHIQ7f")
ACK_STRUCT = struct.Struct("!4sBBHI")


@dataclasses.dataclass(frozen=True)
class PoseCommand:
    sequence: int
    simulation_time_ns: int
    position_m: tuple[float, float, float]
    quaternion_xyzw: tuple[float, float, float, float]


@dataclasses.dataclass(frozen=True)
class Ack:
    sequence: int
    status: int


def pack_pose(command: PoseCommand) -> bytes:
    """Serialize one absolute/relative pose command for the Unreal receiver."""
    return POSE_STRUCT.pack(
        POSE_MAGIC,
        PROTOCOL_VERSION,
        0,
        0,
        command.sequence,
        command.simulation_time_ns,
        *command.position_m,
        *command.quaternion_xyzw,
    )


def unpack_ack(data: bytes) -> Ack:
    """Validate and deserialize an Unreal acknowledgement."""
    if len(data) != ACK_STRUCT.size:
        raise ValueError(f"ACK must be {ACK_STRUCT.size} bytes, received {len(data)}")

    magic, version, status, reserved, sequence = ACK_STRUCT.unpack(data)
    if magic != ACK_MAGIC:
        raise ValueError(f"Unexpected ACK magic {magic!r}")
    if version != PROTOCOL_VERSION:
        raise ValueError(f"Unsupported ACK protocol version {version}")
    if reserved != 0:
        raise ValueError("ACK reserved field must be zero")

    return Ack(sequence=sequence, status=status)
