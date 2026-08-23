#!/usr/bin/env python3
"""Receive tagged JPEG sensor frames from the Unreal SIL camera actor."""

from __future__ import annotations

import argparse
import math
import pathlib
import socket
import statistics
import time
from typing import Any

from sim_camera_protocol import (
    DEFAULT_MAX_HEIGHT,
    DEFAULT_MAX_JPEG_BYTES,
    DEFAULT_MAX_WIDTH,
    FRAME_HEADER_BYTES,
    CameraFrame,
    receive_frame,
)


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Receive and inspect tagged JPEG frames from Unreal."
    )
    parser.add_argument(
        "--host",
        default="172.27.240.1",
        help="Windows/Unreal address as reachable from WSL",
    )
    parser.add_argument("--port", type=int, default=5006)
    parser.add_argument(
        "--duration",
        type=float,
        default=10.0,
        help="Seconds to receive; zero runs until Ctrl+C",
    )
    parser.add_argument(
        "--max-frames",
        type=int,
        default=0,
        help="Stop after this many frames; zero is unlimited",
    )
    parser.add_argument("--connect-timeout", type=float, default=5.0)
    parser.add_argument(
        "--read-timeout",
        type=float,
        default=30.0,
        help="Maximum wait for the next complete frame",
    )
    parser.add_argument("--max-width", type=int, default=DEFAULT_MAX_WIDTH)
    parser.add_argument("--max-height", type=int, default=DEFAULT_MAX_HEIGHT)
    parser.add_argument(
        "--max-jpeg-bytes", type=int, default=DEFAULT_MAX_JPEG_BYTES
    )
    parser.add_argument(
        "--output-dir",
        type=pathlib.Path,
        help="Optionally save received JPEGs to this directory",
    )
    parser.add_argument(
        "--save-every",
        type=int,
        default=1,
        help="When --output-dir is used, save every Nth frame",
    )
    parser.add_argument(
        "--display",
        action="store_true",
        help="Decode/display with OpenCV; press q in the image window to stop",
    )
    parser.add_argument(
        "--print-every",
        type=int,
        default=15,
        help="Print one live status line every N received frames",
    )
    return parser.parse_args()


def validate_args(args: argparse.Namespace) -> None:
    if not 1 <= args.port <= 65535:
        raise SystemExit("--port must be between 1 and 65535")
    if args.duration < 0.0:
        raise SystemExit("--duration must not be negative")
    if args.max_frames < 0:
        raise SystemExit("--max-frames must not be negative")
    if args.connect_timeout <= 0.0 or args.read_timeout <= 0.0:
        raise SystemExit("socket timeouts must be greater than zero")
    if args.max_width <= 0 or args.max_height <= 0:
        raise SystemExit("maximum dimensions must be greater than zero")
    if args.max_jpeg_bytes <= 0:
        raise SystemExit("--max-jpeg-bytes must be greater than zero")
    if args.save_every <= 0 or args.print_every <= 0:
        raise SystemExit("--save-every and --print-every must be greater than zero")


def percentile(values: list[float], fraction: float) -> float:
    ordered = sorted(values)
    index = min(len(ordered) - 1, math.ceil(fraction * len(ordered)) - 1)
    return ordered[index]


def load_opencv() -> tuple[Any, Any]:
    try:
        import cv2
        import numpy
    except ImportError as error:
        raise SystemExit(
            "--display requires OpenCV and NumPy. Install an OpenCV package "
            "in the Python environment used for this script."
        ) from error
    return cv2, numpy


def frame_filename(frame: CameraFrame) -> str:
    header = frame.header
    if header.pose_metadata_valid:
        pose_part = (
            f"run_{header.run_id:016X}_pose_{header.pose_sequence:010d}_"
            f"sim_{header.simulation_time_ns:019d}"
        )
    else:
        pose_part = "pose_unavailable"
    return f"frame_{header.camera_frame_id:010d}_{pose_part}.jpg"


def describe_pose(frame: CameraFrame) -> str:
    header = frame.header
    if not header.pose_metadata_valid:
        return "pose=unavailable"
    return (
        f"run=0x{header.run_id:016X} "
        f"pose_seq={header.pose_sequence} "
        f"sim={header.simulation_time_ns / 1_000_000_000.0:.3f}s"
    )


def print_summary(
    *,
    frame_count: int,
    wire_bytes: int,
    jpeg_sizes: list[int],
    interarrival_ms: list[float],
    elapsed_seconds: float,
    frame_id_gaps: int,
    nonmonotonic_frame_ids: int,
    saved_count: int,
    decoded_count: int,
    decode_failures: int,
    stop_reason: str,
) -> None:
    print(f"Stopped: {stop_reason}")
    print(
        f"Received {frame_count} frames in {elapsed_seconds:.2f} s "
        f"({frame_count / max(elapsed_seconds, 1.0e-9):.2f} observed FPS)"
    )
    if not frame_count:
        return

    print(
        f"TCP camera throughput: {wire_bytes / max(elapsed_seconds, 1.0e-9) / 1_000_000.0:.2f} MB/s; "
        f"JPEG bytes min/mean/max={min(jpeg_sizes)}/"
        f"{statistics.mean(jpeg_sizes):.0f}/{max(jpeg_sizes)}"
    )
    if interarrival_ms:
        print(
            "Arrival interval: "
            f"median={statistics.median(interarrival_ms):.2f} ms, "
            f"mean={statistics.mean(interarrival_ms):.2f} ms, "
            f"p95={percentile(interarrival_ms, 0.95):.2f} ms, "
            f"max={max(interarrival_ms):.2f} ms"
        )
    print(
        f"Frame-ID gaps={frame_id_gaps}, non-monotonic IDs={nonmonotonic_frame_ids}, "
        f"saved={saved_count}, decoded={decoded_count}, decode failures={decode_failures}"
    )


def main() -> None:
    args = parse_args()
    validate_args(args)

    cv2: Any = None
    numpy: Any = None
    if args.display:
        cv2, numpy = load_opencv()

    if args.output_dir:
        args.output_dir.mkdir(parents=True, exist_ok=True)

    print(f"Connecting to Unreal camera at {args.host}:{args.port} over TCP...")
    try:
        camera_socket = socket.create_connection(
            (args.host, args.port), timeout=args.connect_timeout
        )
    except OSError as error:
        raise SystemExit(
            f"Could not connect to Unreal at {args.host}:{args.port}: {error}. "
            "Check PIE, the Sim Camera Streamer actor, its TCP port, and firewall."
        ) from error

    frame_count = 0
    wire_bytes = 0
    jpeg_sizes: list[int] = []
    interarrival_ms: list[float] = []
    frame_id_gaps = 0
    nonmonotonic_frame_ids = 0
    saved_count = 0
    decoded_count = 0
    decode_failures = 0
    previous_frame_id: int | None = None
    previous_arrival_ns: int | None = None
    start_ns = time.perf_counter_ns()
    stop_reason = "requested limit reached"

    with camera_socket:
        camera_socket.settimeout(args.read_timeout)
        print(
            "Connected. Waiting for frames; Unreal captures only while this "
            "client is connected."
        )
        try:
            while True:
                elapsed_seconds = (
                    time.perf_counter_ns() - start_ns
                ) / 1_000_000_000.0
                if args.duration and elapsed_seconds >= args.duration:
                    stop_reason = f"duration {args.duration:g} s reached"
                    break
                if args.max_frames and frame_count >= args.max_frames:
                    stop_reason = f"frame limit {args.max_frames} reached"
                    break

                if args.duration:
                    remaining_duration = max(
                        args.duration - elapsed_seconds, 0.001
                    )
                    camera_socket.settimeout(
                        min(args.read_timeout, remaining_duration)
                    )
                else:
                    camera_socket.settimeout(args.read_timeout)

                frame = receive_frame(
                    camera_socket,
                    max_width=args.max_width,
                    max_height=args.max_height,
                    max_jpeg_bytes=args.max_jpeg_bytes,
                )
                arrival_ns = time.perf_counter_ns()
                frame_count += 1
                jpeg_sizes.append(len(frame.jpeg))
                wire_bytes += FRAME_HEADER_BYTES + len(frame.jpeg)

                if previous_arrival_ns is not None:
                    interarrival_ms.append(
                        (arrival_ns - previous_arrival_ns) / 1_000_000.0
                    )
                previous_arrival_ns = arrival_ns

                frame_id = frame.header.camera_frame_id
                if previous_frame_id is not None:
                    if frame_id <= previous_frame_id:
                        nonmonotonic_frame_ids += 1
                    elif frame_id > previous_frame_id + 1:
                        frame_id_gaps += frame_id - previous_frame_id - 1
                previous_frame_id = frame_id

                if args.output_dir and (frame_count - 1) % args.save_every == 0:
                    output_path = args.output_dir / frame_filename(frame)
                    output_path.write_bytes(frame.jpeg)
                    saved_count += 1

                if args.display:
                    encoded = numpy.frombuffer(frame.jpeg, dtype=numpy.uint8)
                    image = cv2.imdecode(encoded, cv2.IMREAD_COLOR)
                    if image is None:
                        decode_failures += 1
                    else:
                        decoded_count += 1
                        cv2.imshow("Unreal SIL Camera", image)
                        if cv2.waitKey(1) & 0xFF == ord("q"):
                            stop_reason = "q pressed in display window"
                            break

                if frame_count == 1 or frame_count % args.print_every == 0:
                    print(
                        f"frame={frame.header.camera_frame_id} "
                        f"{frame.header.width}x{frame.header.height} "
                        f"jpeg={len(frame.jpeg) / 1024.0:.1f} KiB "
                        f"{describe_pose(frame)}"
                    )
        except KeyboardInterrupt:
            stop_reason = "Ctrl+C"
        except socket.timeout:
            elapsed_seconds = (
                time.perf_counter_ns() - start_ns
            ) / 1_000_000_000.0
            if args.duration and elapsed_seconds >= args.duration:
                stop_reason = f"duration {args.duration:g} s reached"
            else:
                stop_reason = (
                    f"no complete frame arrived for {args.read_timeout:g} s"
                )
        except EOFError as error:
            stop_reason = str(error)
        finally:
            if args.display:
                cv2.destroyAllWindows()

    elapsed_seconds = (time.perf_counter_ns() - start_ns) / 1_000_000_000.0
    print_summary(
        frame_count=frame_count,
        wire_bytes=wire_bytes,
        jpeg_sizes=jpeg_sizes,
        interarrival_ms=interarrival_ms,
        elapsed_seconds=elapsed_seconds,
        frame_id_gaps=frame_id_gaps,
        nonmonotonic_frame_ids=nonmonotonic_frame_ids,
        saved_count=saved_count,
        decoded_count=decoded_count,
        decode_failures=decode_failures,
        stop_reason=stop_reason,
    )


if __name__ == "__main__":
    main()
