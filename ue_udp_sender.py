#!/usr/bin/env python3
"""Send a timestamped flight path to the SimUdpBridge Unreal plugin."""

from __future__ import annotations

import argparse
import math
import select
import socket
import statistics
import time

from sim_udp_protocol import Ack, PoseCommand, pack_pose, unpack_ack


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Drive an Unreal actor over UDP and report ACK latency."
    )
    parser.add_argument(
        "--host",
        default="172.27.240.1",
        help="Windows/Unreal host address as reachable from WSL",
    )
    parser.add_argument("--port", type=int, default=5005)
    parser.add_argument("--rate", type=float, default=30.0, help="Commands per second")
    parser.add_argument("--duration", type=float, default=10.0, help="Seconds to run")
    parser.add_argument(
        "--path", choices=("line", "circle", "hover"), default="line"
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


def path_pose(
    path: str,
    elapsed_s: float,
    speed_mps: float,
    radius_m: float,
    period_s: float,
    altitude_m: float,
) -> tuple[tuple[float, float, float], tuple[float, float, float, float]]:
    """Return position and quaternion in Unreal axes: X forward, Y right, Z up."""
    if path == "line":
        position = (speed_mps * elapsed_s, 0.0, altitude_m)
        yaw_rad = 0.0 if speed_mps >= 0.0 else math.pi
    elif path == "circle":
        angle = 2.0 * math.pi * elapsed_s / period_s
        # Subtract radius so the path starts at zero X offset.
        position = (radius_m * (math.cos(angle) - 1.0), radius_m * math.sin(angle), altitude_m)
        yaw_rad = angle + math.pi / 2.0
    else:
        position = (0.0, 0.0, altitude_m)
        yaw_rad = 0.0

    quaternion = (0.0, 0.0, math.sin(yaw_rad / 2.0), math.cos(yaw_rad / 2.0))
    return position, quaternion


def drain_acks(
    udp_socket: socket.socket,
    sent_ns: dict[int, int],
    latencies_ms: list[float],
) -> Ack | None:
    newest_ack = None
    while True:
        readable, _, _ = select.select([udp_socket], [], [], 0.0)
        if not readable:
            return newest_ack

        data, _ = udp_socket.recvfrom(256)
        try:
            ack = unpack_ack(data)
        except ValueError as error:
            print(f"Ignoring invalid ACK: {error}")
            continue

        newest_ack = ack
        send_time_ns = sent_ns.pop(ack.sequence, None)
        if send_time_ns is not None:
            latencies_ms.append((time.perf_counter_ns() - send_time_ns) / 1_000_000.0)


def percentile(values: list[float], fraction: float) -> float:
    ordered = sorted(values)
    index = min(len(ordered) - 1, math.ceil(fraction * len(ordered)) - 1)
    return ordered[index]


def validate_args(args: argparse.Namespace) -> None:
    if not 1 <= args.port <= 65535:
        raise SystemExit("--port must be between 1 and 65535")
    if args.rate <= 0.0:
        raise SystemExit("--rate must be greater than zero")
    if args.duration <= 0.0:
        raise SystemExit("--duration must be greater than zero")
    if args.period <= 0.0:
        raise SystemExit("--period must be greater than zero")


def main() -> None:
    args = parse_args()
    validate_args(args)

    destination = (args.host, args.port)
    interval_ns = round(1_000_000_000 / args.rate)
    start_ns = time.perf_counter_ns()
    stop_ns = start_ns + round(args.duration * 1_000_000_000)
    next_send_ns = start_ns
    sequence = 1
    sent_ns: dict[int, int] = {}
    latencies_ms: list[float] = []

    with socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as udp_socket:
        udp_socket.setblocking(False)
        print(
            f"Sending {args.path} path to {args.host}:{args.port} at "
            f"{args.rate:g} Hz for {args.duration:g} s"
        )

        while True:
            now_ns = time.perf_counter_ns()
            drain_acks(udp_socket, sent_ns, latencies_ms)
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
                sequence=sequence,
                simulation_time_ns=now_ns - start_ns,
                position_m=position,
                quaternion_xyzw=quaternion,
            )
            packet = pack_pose(command)
            udp_socket.sendto(packet, destination)
            sent_ns[sequence] = time.perf_counter_ns()
            sequence += 1

            # Do not accumulate timing drift if one iteration runs late.
            next_send_ns += interval_ns
            if now_ns - next_send_ns > interval_ns:
                next_send_ns = now_ns + interval_ns

        # Give the final UE tick and ACK a short opportunity to arrive.
        final_deadline = time.perf_counter() + 0.25
        while time.perf_counter() < final_deadline:
            drain_acks(udp_socket, sent_ns, latencies_ms)
            if not sent_ns:
                break
            time.sleep(0.001)

    sent_count = sequence - 1
    print(f"Sent {sent_count} commands; received {len(latencies_ms)} ACKs")
    if latencies_ms:
        print(
            "ACK latency: "
            f"median={statistics.median(latencies_ms):.2f} ms, "
            f"p95={percentile(latencies_ms, 0.95):.2f} ms, "
            f"max={max(latencies_ms):.2f} ms"
        )
    else:
        print("No ACKs received. Check PIE, the actor, host address, port, and firewall.")


if __name__ == "__main__":
    main()
