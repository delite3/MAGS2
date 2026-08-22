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

from sim_udp_protocol import ACK_APPLIED, Ack, PoseCommand, pack_pose, unpack_ack


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
    parser.add_argument("--duration", type=float, default=5.0, help="Seconds to run")
    parser.add_argument(
        "--path", choices=("line", "circle", "hover"), default="hover"
    )
    parser.add_argument("--speed", type=float, default=1.0, help="Line speed in m/s")
    parser.add_argument("--radius", type=float, default=2.0, help="Circle radius in m")
    parser.add_argument(
        "--period", type=float, default=8.0, help="Circle period in seconds"
    )
    parser.add_argument(
        "--altitude", type=float, default=2.0, help="Z offset in metres"
    )
    return parser.parse_args()


def validate_args(args: argparse.Namespace) -> None:
    if not 1 <= args.port <= 65535:
        raise SystemExit("--port must be between 1 and 65535")
    if not 0.1 <= args.rate <= 1000.0:
        raise SystemExit("--rate must be between 0.1 and 1000 Hz")
    if args.duration <= 0.0:
        raise SystemExit("--duration must be greater than zero")
    if args.period <= 0.0:
        raise SystemExit("--period must be greater than zero")
    if args.run_id is not None and not 1 <= args.run_id < (1 << 64):
        raise SystemExit("--run-id must be a non-zero unsigned 64-bit integer")


def path_pose(
    path: str,
    elapsed_s: float,
    speed_mps: float,
    radius_m: float,
    period_s: float,
    altitude_m: float,
) -> tuple[tuple[float, float, float], tuple[float, float, float, float]]:
    """Return pose in Unreal axes: X forward, Y right, Z up."""
    if path == "line":
        position = (speed_mps * elapsed_s, 0.0, altitude_m)
        yaw_rad = 0.0 if speed_mps >= 0.0 else math.pi
    elif path == "circle":
        angle = 2.0 * math.pi * elapsed_s / period_s
        # Starting at zero avoids an initial horizontal teleport.
        position = (
            radius_m * (math.cos(angle) - 1.0),
            radius_m * math.sin(angle),
            altitude_m,
        )
        yaw_rad = angle + math.pi / 2.0
    else:
        position = (0.0, 0.0, altitude_m)
        yaw_rad = 0.0

    quaternion = (
        0.0,
        0.0,
        math.sin(yaw_rad / 2.0),
        math.cos(yaw_rad / 2.0),
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


def main() -> None:
    args = parse_args()
    validate_args(args)

    destination = (args.host, args.port)
    run_id = (
        args.run_id
        if args.run_id is not None
        else secrets.randbelow((1 << 64) - 1) + 1
    )
    interval_ns = round(1_000_000_000 / args.rate)
    start_ns = time.perf_counter_ns()
    stop_ns = start_ns + round(args.duration * 1_000_000_000)
    next_send_ns = start_ns
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
        print(
            f"Sending {args.path} path to {args.host}:{args.port} at "
            f"{args.rate:g} Hz for {args.duration:g} s (run 0x{run_id:016X})"
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
            position, quaternion = path_pose(
                args.path,
                elapsed_s,
                args.speed,
                args.radius,
                args.period,
                args.altitude,
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
