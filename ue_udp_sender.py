#!/usr/bin/env python3
"""Send a timestamped path to the SimUdpBridge Unreal actor."""

from __future__ import annotations

import argparse
import math
import secrets
import select
import socket
import statistics
import time
from pathlib import Path

from sim_udp_protocol import (
    ACK_APPLIED,
    Ack,
    GeoreferenceCommand,
    PoseCommand,
    pack_georeference,
    pack_pose,
    unpack_ack,
)
from sim_trajectory import (
    Trajectory,
    load_trajectory_csv,
    unreal_rotator_quaternion,
)


class GeoreferenceRejected(RuntimeError):
    """Unreal received the startup packet but could not apply its origin."""


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Drive an Unreal actor over UDP and report applied-ACK latency."
    )
    parser.add_argument(
        "--host",
        default="172.27.240.1",
        help="Windows/Unreal address as reachable from WSL",
    )
    parser.add_argument("--port", type=int, default=5005)
    parser.add_argument(
        "--run-id",
        type=lambda value: int(value, 0),
        help="Optional reproducible uint64 run ID; random by default",
    )
    parser.add_argument("--rate", type=float, default=30.0, help="Commands per second")
    parser.add_argument(
        "--duration",
        type=float,
        help=(
            "Seconds to run; defaults to 5 for a built-in path or the last "
            "trajectory timestamp"
        ),
    )
    motion_source = parser.add_mutually_exclusive_group()
    motion_source.add_argument(
        "--path",
        choices=("line", "circle", "hover"),
        help="Built-in path (default: circle)",
    )
    motion_source.add_argument(
        "--trajectory",
        type=Path,
        help="CSV containing time-stamped local position and orientation",
    )
    parser.add_argument("--speed", type=float, default=1.0, help="Line speed in m/s")
    parser.add_argument("--radius", type=float, default=2.0, help="Circle radius in m")
    parser.add_argument(
        "--period", type=float, default=8.0, help="Circle period in seconds"
    )
    parser.add_argument(
        "--altitude",
        type=float,
        required=True,
        help=(
            "Cesium WGS84 ellipsoid origin height in metres; this does not "
            "set the object's local height"
        ),
    )
    parser.add_argument(
        "--latitude",
        type=float,
        required=True,
        help="Cesium origin latitude in degrees",
    )
    parser.add_argument(
        "--longitude",
        type=float,
        required=True,
        help="Cesium origin longitude in degrees",
    )
    parser.add_argument(
        "--object-height",
        type=float,
        default=0.0,
        help=(
            "Built-in path height above the Cesium origin along local Unreal "
            "+Z, in metres (default: 0)"
        ),
    )
    parser.add_argument(
        "--roll",
        type=float,
        default=0.0,
        help="Built-in path roll in Unreal degrees (default: 0)",
    )
    parser.add_argument(
        "--pitch",
        type=float,
        default=0.0,
        help="Built-in path pitch in Unreal degrees (default: 0)",
    )
    parser.add_argument(
        "--yaw-offset",
        type=float,
        default=0.0,
        help="Degrees added to the built-in path's automatic yaw (default: 0)",
    )
    return parser.parse_args()


def validate_args(args: argparse.Namespace) -> None:
    if not 1 <= args.port <= 65535:
        raise SystemExit("--port must be between 1 and 65535")
    if not math.isfinite(args.rate) or not 0.1 <= args.rate <= 1000.0:
        raise SystemExit("--rate must be between 0.1 and 1000 Hz")
    if args.duration is not None and (
        not math.isfinite(args.duration) or args.duration <= 0.0
    ):
        raise SystemExit("--duration must be greater than zero")
    if not math.isfinite(args.period) or args.period <= 0.0:
        raise SystemExit("--period must be greater than zero")
    if not math.isfinite(args.speed):
        raise SystemExit("--speed must be finite")
    if not math.isfinite(args.radius):
        raise SystemExit("--radius must be finite")
    if args.run_id is not None and not 1 <= args.run_id < (1 << 64):
        raise SystemExit("--run-id must be a non-zero unsigned 64-bit integer")
    if not math.isfinite(args.latitude) or not -90.0 <= args.latitude <= 90.0:
        raise SystemExit("--latitude must be between -90 and 90 degrees")
    if (
        not math.isfinite(args.longitude)
        or not -180.0 <= args.longitude <= 180.0
    ):
        raise SystemExit("--longitude must be between -180 and 180 degrees")
    if not math.isfinite(args.altitude):
        raise SystemExit("--altitude must be finite")
    if not math.isfinite(args.object_height):
        raise SystemExit("--object-height must be finite")
    orientation_values = (args.roll, args.pitch, args.yaw_offset)
    if not all(math.isfinite(value) for value in orientation_values):
        raise SystemExit("--roll, --pitch, and --yaw-offset must be finite")
    if args.trajectory is not None and any(
        value != 0.0
        for value in (args.object_height, args.roll, args.pitch, args.yaw_offset)
    ):
        raise SystemExit(
            "--trajectory supplies complete poses; do not combine it with "
            "--object-height, --roll, --pitch, or --yaw-offset"
        )


def path_pose(
    path: str,
    elapsed_s: float,
    speed_mps: float,
    radius_m: float,
    period_s: float,
    object_height_m: float,
    roll_deg: float = 0.0,
    pitch_deg: float = 0.0,
    yaw_offset_deg: float = 0.0,
) -> tuple[tuple[float, float, float], tuple[float, float, float, float]]:
    """Return a pose in metres in the local Unreal X/Y/Z frame."""
    if path == "line":
        position = (speed_mps * elapsed_s, 0.0, object_height_m)
        yaw_rad = 0.0 if speed_mps >= 0.0 else math.pi
    elif path == "circle":
        angle = 2.0 * math.pi * elapsed_s / period_s
        # Starting at zero avoids an initial horizontal teleport.
        position = (
            radius_m * (math.cos(angle) - 1.0),
            radius_m * math.sin(angle),
            object_height_m,
        )
        yaw_rad = angle + math.pi / 2.0
    else:
        position = (0.0, 0.0, object_height_m)
        yaw_rad = 0.0

    quaternion = unreal_rotator_quaternion(
        roll_deg,
        pitch_deg,
        math.degrees(yaw_rad) + yaw_offset_deg,
    )
    return position, quaternion


def drain_acks(
    udp_socket: socket.socket,
    expected_run_id: int,
    sent_ns: dict[int, int],
    latencies_ms: list[float],
    rejected_acks: list[Ack],
) -> None:
    """Drain queued ACKs without ever blocking the command schedule."""
    while True:
        readable, _, _ = select.select([udp_socket], [], [], 0.0)
        if not readable:
            return

        data = udp_socket.recv(256)
        try:
            ack = unpack_ack(data)
        except ValueError as error:
            print(f"Ignoring invalid ACK: {error}")
            continue

        if ack.run_id != expected_run_id:
            continue

        send_time_ns = sent_ns.pop(ack.sequence, None)
        if ack.status != ACK_APPLIED:
            rejected_acks.append(ack)
        elif send_time_ns is not None:
            latencies_ms.append((time.perf_counter_ns() - send_time_ns) / 1_000_000.0)


def percentile(values: list[float], fraction: float) -> float:
    ordered = sorted(values)
    index = min(len(ordered) - 1, math.ceil(fraction * len(ordered)) - 1)
    return ordered[index]


def wait_for_georeference_ack(
    udp_socket: socket.socket, run_id: int, timeout_s: float = 2.0
) -> None:
    """Require Unreal to apply the Cesium origin before sending poses."""
    deadline = time.perf_counter() + timeout_s
    while time.perf_counter() < deadline:
        readable, _, _ = select.select([udp_socket], [], [], 0.05)
        if not readable:
            continue
        try:
            ack = unpack_ack(udp_socket.recv(256))
        except ValueError as error:
            print(f"Ignoring invalid startup ACK: {error}")
            continue
        if ack.run_id != run_id or ack.sequence != 0:
            continue
        if ack.status != ACK_APPLIED:
            raise GeoreferenceRejected(
                f"Unreal rejected georeference startup (status={ack.status})"
            )
        return
    raise RuntimeError("Timed out waiting for Unreal georeference acknowledgement")


def main() -> None:
    args = parse_args()
    validate_args(args)

    trajectory: Trajectory | None = None
    if args.trajectory is not None:
        try:
            trajectory = load_trajectory_csv(args.trajectory)
        except (OSError, ValueError) as error:
            raise SystemExit(f"Could not load trajectory: {error}") from error
        duration_s = args.duration if args.duration is not None else trajectory.duration_s
        motion_label = f"trajectory {args.trajectory}"
    else:
        duration_s = args.duration if args.duration is not None else 5.0
        motion_label = f"{args.path or 'circle'} path"

    destination = (args.host, args.port)
    run_id = (
        args.run_id
        if args.run_id is not None
        else secrets.randbelow((1 << 64) - 1) + 1
    )
    interval_ns = round(1_000_000_000 / args.rate)
    sequence = 1
    sent_ns: dict[int, int] = {}
    latencies_ms: list[float] = []
    rejected_acks: list[Ack] = []
    start_marker_packets = max(3, math.ceil(args.rate * 0.25))

    with socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as udp_socket:
        # A connected UDP socket still sends datagrams, but the kernel filters
        # replies so unrelated hosts cannot be mistaken for Unreal ACKs.
        udp_socket.connect(destination)
        udp_socket.setblocking(False)
        georeference_packet = pack_georeference(
            GeoreferenceCommand(
                run_id=run_id,
                latitude_deg=args.latitude,
                longitude_deg=args.longitude,
                ellipsoid_height_m=args.altitude,
            )
        )
        print(
            f"Setting Cesium origin to lat={args.latitude:g}, "
            f"lon={args.longitude:g}, height={args.altitude:g} m"
        )
        try:
            for attempt in range(3):
                udp_socket.send(georeference_packet)
                try:
                    wait_for_georeference_ack(udp_socket, run_id, timeout_s=1.0)
                    break
                except GeoreferenceRejected:
                    raise
                except RuntimeError:
                    if attempt == 2:
                        raise
                    print("No startup ACK; retrying georeference packet")
        except RuntimeError as error:
            raise SystemExit(
                f"{error}. Confirm Unreal is running the rebuilt plugin, "
                "the Cesium Georeference field is assigned, and UDP port "
                f"{args.port} is reachable."
            ) from error
        start_ns = time.perf_counter_ns()
        stop_ns = start_ns + round(duration_s * 1_000_000_000)
        next_send_ns = start_ns
        if trajectory is not None:
            print(
                f"Loaded {len(trajectory.samples)} poses spanning "
                f"{trajectory.duration_s:g} s from {args.trajectory}"
            )
        print(
            f"Sending {motion_label} to {args.host}:{args.port} at "
            f"{args.rate:g} Hz for {duration_s:g} s (run 0x{run_id:016X})"
        )

        while True:
            now_ns = time.perf_counter_ns()
            drain_acks(
                udp_socket, run_id, sent_ns, latencies_ms, rejected_acks
            )
            if now_ns >= stop_ns:
                break

            if now_ns < next_send_ns:
                time.sleep(min((next_send_ns - now_ns) / 1_000_000_000, 0.001))
                continue

            elapsed_s = (now_ns - start_ns) / 1_000_000_000.0
            if trajectory is not None:
                position, quaternion = trajectory.pose_at(elapsed_s)
            else:
                position, quaternion = path_pose(
                    args.path or "circle",
                    elapsed_s,
                    args.speed,
                    args.radius,
                    args.period,
                    args.object_height,
                    args.roll,
                    args.pitch,
                    args.yaw_offset,
                )
            command = PoseCommand(
                run_id=run_id,
                sequence=sequence,
                simulation_time_ns=now_ns - start_ns,
                position_m=position,
                quaternion_xyzw=quaternion,
                start_of_run=sequence <= start_marker_packets,
            )
            udp_socket.send(pack_pose(command))
            sent_ns[sequence] = time.perf_counter_ns()
            sequence += 1

            # Superseded commands intentionally receive no ACK. Prevent those
            # diagnostic timestamps from growing forever during a long test.
            stale_before_ns = time.perf_counter_ns() - 5_000_000_000
            sent_ns = {
                key: sent_time
                for key, sent_time in sent_ns.items()
                if sent_time >= stale_before_ns
            }

            next_send_ns += interval_ns
            if now_ns - next_send_ns > interval_ns:
                next_send_ns = now_ns + interval_ns

        final_deadline = time.perf_counter() + 0.25
        while time.perf_counter() < final_deadline:
            drain_acks(
                udp_socket, run_id, sent_ns, latencies_ms, rejected_acks
            )
            time.sleep(0.001)

    sent_count = sequence - 1
    print(f"Sent {sent_count} commands; received {len(latencies_ms)} applied ACKs")
    if rejected_acks:
        newest = rejected_acks[-1]
        print(
            f"Unreal rejected {len(rejected_acks)} commands; "
            f"latest status={newest.status}, sequence={newest.sequence}"
        )
    if latencies_ms:
        print(
            "Applied-ACK latency: "
            f"median={statistics.median(latencies_ms):.2f} ms, "
            f"mean={statistics.mean(latencies_ms):.2f} ms, "
            f"p95={percentile(latencies_ms, 0.95):.2f} ms, "
            f"max={max(latencies_ms):.2f} ms"
        )
    else:
        print("No applied ACKs. Check PIE, actor setup, host, port, and firewall.")


if __name__ == "__main__":
    main()
