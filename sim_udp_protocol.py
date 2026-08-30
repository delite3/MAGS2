"""Wire format shared by the Python SIL client and Unreal UDP bridge."""

from __future__ import annotations

import dataclasses
import math
import struct


PROTOCOL_VERSION = 2
COMMAND_MAGIC = b"SUDP"
GEOREFERENCE_MAGIC = b"SGRF"
ACK_MAGIC = b"SACK"
START_RUN_FLAG = 0x01

ACK_APPLIED = 0
ACK_INVALID_PACKET = 1
ACK_REJECTED = 2
ACK_APPLY_FAILED = 3
KNOWN_ACK_STATUSES = {
    ACK_APPLIED,
    ACK_INVALID_PACKET,
    ACK_REJECTED,
    ACK_APPLY_FAILED,
}

# Network byte order. Quaternion component order is X, Y, Z, W.
# Command: magic, version, flags, reserved, run, sequence, sim time, pose.
COMMAND_STRUCT = struct.Struct("!4sBBHQIQ7f")
# Georeference: magic, version, flags, reserved, run, latitude, longitude,
# ellipsoid height. The user-facing order is latitude, longitude, height.
GEOREFERENCE_STRUCT = struct.Struct("!4sBBHQ3d")
# ACK: magic, version, status, reserved, run, applied/rejected sequence.
ACK_STRUCT = struct.Struct("!4sBBHQI")


@dataclasses.dataclass(frozen=True)
class PoseCommand:
    run_id: int
    sequence: int
    simulation_time_ns: int
    position_m: tuple[float, float, float]
    quaternion_xyzw: tuple[float, float, float, float]
    start_of_run: bool = False


@dataclasses.dataclass(frozen=True)
class GeoreferenceCommand:
    run_id: int
    latitude_deg: float
    longitude_deg: float
    ellipsoid_height_m: float


@dataclasses.dataclass(frozen=True)
class Ack:
    run_id: int
    sequence: int
    status: int


def _require_unsigned(name: str, value: int, bits: int) -> None:
    if not 0 <= value < (1 << bits):
        raise ValueError(f"{name} must fit in uint{bits}")


def pack_pose(command: PoseCommand) -> bytes:
    """Validate and serialize one pose command."""
    _require_unsigned("run_id", command.run_id, 64)
    if command.run_id == 0:
        raise ValueError("run_id zero is reserved")
    _require_unsigned("sequence", command.sequence, 32)
    _require_unsigned("simulation_time_ns", command.simulation_time_ns, 64)

    values = (*command.position_m, *command.quaternion_xyzw)
    if len(command.position_m) != 3 or len(command.quaternion_xyzw) != 4:
        raise ValueError("pose requires three position and four quaternion values")
    if not all(math.isfinite(value) for value in values):
        raise ValueError("pose values must be finite")
    if sum(value * value for value in command.quaternion_xyzw) < 1.0e-12:
        raise ValueError("quaternion must have non-zero length")

    flags = START_RUN_FLAG if command.start_of_run else 0
    return COMMAND_STRUCT.pack(
        COMMAND_MAGIC,
        PROTOCOL_VERSION,
        flags,
        0,
        command.run_id,
        command.sequence,
        command.simulation_time_ns,
        *values,
    )


def pack_georeference(command: GeoreferenceCommand) -> bytes:
    """Validate and serialize one Cesium startup origin command."""
    _require_unsigned("run_id", command.run_id, 64)
    if command.run_id == 0:
        raise ValueError("run_id zero is reserved")
    values = (
        command.latitude_deg,
        command.longitude_deg,
        command.ellipsoid_height_m,
    )
    if not all(math.isfinite(value) for value in values):
        raise ValueError("georeference values must be finite")
    if not -90.0 <= command.latitude_deg <= 90.0:
        raise ValueError("latitude must be between -90 and 90 degrees")
    if not -180.0 <= command.longitude_deg <= 180.0:
        raise ValueError("longitude must be between -180 and 180 degrees")

    return GEOREFERENCE_STRUCT.pack(
        GEOREFERENCE_MAGIC,
        PROTOCOL_VERSION,
        0,
        0,
        command.run_id,
        *values,
    )


def unpack_ack(data: bytes) -> Ack:
    """Validate and deserialize an Unreal acknowledgement."""
    if len(data) != ACK_STRUCT.size:
        raise ValueError(f"ACK must be {ACK_STRUCT.size} bytes, received {len(data)}")

    magic, version, status, reserved, run_id, sequence = ACK_STRUCT.unpack(data)
    if magic != ACK_MAGIC:
        raise ValueError(f"unexpected ACK magic {magic!r}")
    if version != PROTOCOL_VERSION:
        raise ValueError(f"unsupported ACK protocol version {version}")
    if reserved != 0:
        raise ValueError("ACK reserved field must be zero")
    if status not in KNOWN_ACK_STATUSES:
        raise ValueError(f"unknown ACK status {status}")

    return Ack(run_id=run_id, sequence=sequence, status=status)
