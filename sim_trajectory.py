"""Load and interpolate user-defined local Unreal pose trajectories."""

from __future__ import annotations

import bisect
import csv
import dataclasses
import math
from pathlib import Path


Vector3 = tuple[float, float, float]
Quaternion = tuple[float, float, float, float]

TRAJECTORY_COLUMNS = (
    "time_s",
    "x_m",
    "y_m",
    "z_m",
    "roll_deg",
    "pitch_deg",
    "yaw_deg",
)


@dataclasses.dataclass(frozen=True)
class TrajectorySample:
    time_s: float
    position_m: Vector3
    quaternion_xyzw: Quaternion


@dataclasses.dataclass(frozen=True)
class Trajectory:
    samples: tuple[TrajectorySample, ...]

    @property
    def duration_s(self) -> float:
        return self.samples[-1].time_s

    def pose_at(self, elapsed_s: float) -> tuple[Vector3, Quaternion]:
        """Interpolate the trajectory, holding its endpoints outside its range."""
        if elapsed_s <= 0.0:
            first = self.samples[0]
            return first.position_m, first.quaternion_xyzw
        if elapsed_s >= self.duration_s:
            last = self.samples[-1]
            return last.position_m, last.quaternion_xyzw

        right_index = bisect.bisect_right(
            self.samples,
            elapsed_s,
            key=lambda sample: sample.time_s,
        )
        left = self.samples[right_index - 1]
        right = self.samples[right_index]
        fraction = (elapsed_s - left.time_s) / (right.time_s - left.time_s)

        position = tuple(
            start + fraction * (end - start)
            for start, end in zip(left.position_m, right.position_m)
        )
        quaternion = slerp(
            left.quaternion_xyzw,
            right.quaternion_xyzw,
            fraction,
        )
        return position, quaternion


def unreal_rotator_quaternion(
    roll_deg: float,
    pitch_deg: float,
    yaw_deg: float,
) -> Quaternion:
    """Match Unreal FRotator(Pitch, Yaw, Roll).Quaternion() signs and order."""
    if not all(math.isfinite(value) for value in (roll_deg, pitch_deg, yaw_deg)):
        raise ValueError("roll, pitch, and yaw must be finite")

    half_to_rad = math.pi / 360.0
    pitch = math.fmod(pitch_deg, 360.0) * half_to_rad
    yaw = math.fmod(yaw_deg, 360.0) * half_to_rad
    roll = math.fmod(roll_deg, 360.0) * half_to_rad
    sp, cp = math.sin(pitch), math.cos(pitch)
    sy, cy = math.sin(yaw), math.cos(yaw)
    sr, cr = math.sin(roll), math.cos(roll)

    quaternion = (
        cr * sp * sy - sr * cp * cy,
        -cr * sp * cy - sr * cp * sy,
        cr * cp * sy - sr * sp * cy,
        cr * cp * cy + sr * sp * sy,
    )
    return _normalize_quaternion(quaternion)


def slerp(first: Quaternion, second: Quaternion, fraction: float) -> Quaternion:
    """Interpolate unit quaternions along the shortest rotational arc."""
    if not 0.0 <= fraction <= 1.0:
        raise ValueError("SLERP fraction must be between zero and one")

    start = _normalize_quaternion(first)
    end = _normalize_quaternion(second)
    dot = sum(left * right for left, right in zip(start, end))

    if dot < 0.0:
        end = tuple(-value for value in end)
        dot = -dot

    dot = min(1.0, max(-1.0, dot))
    if dot > 0.9995:
        blended = tuple(
            left + fraction * (right - left)
            for left, right in zip(start, end)
        )
        return _normalize_quaternion(blended)

    angle = math.acos(dot)
    sin_angle = math.sin(angle)
    start_weight = math.sin((1.0 - fraction) * angle) / sin_angle
    end_weight = math.sin(fraction * angle) / sin_angle
    return _normalize_quaternion(
        tuple(
            start_weight * left + end_weight * right
            for left, right in zip(start, end)
        )
    )


def load_trajectory_csv(path: Path) -> Trajectory:
    """Read strictly increasing, time-stamped local poses from a CSV file."""
    with path.open("r", encoding="utf-8-sig", newline="") as stream:
        reader = csv.DictReader(stream)
        if reader.fieldnames is None:
            raise ValueError(f"{path}: trajectory CSV has no header")

        missing = [
            column for column in TRAJECTORY_COLUMNS if column not in reader.fieldnames
        ]
        if missing:
            raise ValueError(f"{path}: missing CSV columns: {', '.join(missing)}")

        samples: list[TrajectorySample] = []
        for line_number, row in enumerate(reader, start=2):
            try:
                values = {
                    column: float(row[column])
                    for column in TRAJECTORY_COLUMNS
                }
            except (TypeError, ValueError) as error:
                raise ValueError(
                    f"{path}:{line_number}: every trajectory field must be numeric"
                ) from error

            if not all(math.isfinite(value) for value in values.values()):
                raise ValueError(
                    f"{path}:{line_number}: trajectory values must be finite"
                )
            if values["time_s"] < 0.0:
                raise ValueError(
                    f"{path}:{line_number}: time_s cannot be negative"
                )
            if samples and values["time_s"] <= samples[-1].time_s:
                raise ValueError(
                    f"{path}:{line_number}: time_s must be strictly increasing"
                )

            samples.append(
                TrajectorySample(
                    time_s=values["time_s"],
                    position_m=(values["x_m"], values["y_m"], values["z_m"]),
                    quaternion_xyzw=unreal_rotator_quaternion(
                        values["roll_deg"],
                        values["pitch_deg"],
                        values["yaw_deg"],
                    ),
                )
            )

    if len(samples) < 2:
        raise ValueError(f"{path}: trajectory requires at least two samples")
    if samples[0].time_s != 0.0:
        raise ValueError(f"{path}: first trajectory sample must have time_s = 0")
    return Trajectory(tuple(samples))


def _normalize_quaternion(quaternion: Quaternion) -> Quaternion:
    magnitude_squared = sum(value * value for value in quaternion)
    if not math.isfinite(magnitude_squared) or magnitude_squared < 1.0e-12:
        raise ValueError("quaternion must have finite, non-zero length")
    inverse_magnitude = 1.0 / math.sqrt(magnitude_squared)
    return tuple(value * inverse_magnitude for value in quaternion)
