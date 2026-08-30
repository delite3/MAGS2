# Architecture and timing model

## Data flow

```text
Python built-in path or CSV pose-trajectory generator
    -> 40-byte georeference startup packet, then 56-byte UDP commands
Windows UDP socket in Unreal
    -> set Cesium origin, then validate and retain newest sequence
Unreal PrePhysics tick on the game thread
    -> apply controlled-actor transform
    -> send 20-byte applied ACK

Unreal PostUpdateWork tick
    -> render SceneCapture2D to an RGBA8 render target
    -> synchronously read pixels and encode JPEG
    -> prepend applied-pose metadata
    -> send one bounded frame over non-blocking TCP
Python
    -> validate header and JPEG
    -> optionally save or OpenCV-decode the frame
```

The UE code is a project plugin rather than an engine modification. This keeps
the integration isolated, removable, and reusable without modifying the UE
installation or unrelated project content.

## Why UDP for pose commands

Pose commands are small state updates. If command 101 is available, processing
commands 98 through 100 first only creates latency. The bridge therefore polls
a non-blocking UDP socket, validates every packet, and retains only the newest
valid command waiting at each Unreal tick.

The actor ticks in `TG_PrePhysics`, so the accepted pose is applied before that
frame's physics work. An `Applied` ACK is sent only after
`SetActorLocationAndRotation` succeeds on the game thread.

Sending at 100 Hz to a 30 or 60 Hz Unreal loop intentionally produces fewer
applied ACKs than sent commands. That is state-stream behavior, not packet-loss
evidence by itself.

## Run and sequence identity

The sender first transmits one georeference startup packet containing latitude,
longitude, and WGS84 ellipsoid height. Unreal acknowledges it only after
setting the Cesium origin; pose packets for that run are rejected until this
step succeeds. The movement-profile clock starts after this acknowledgement,
so an origin retry cannot skip the beginning of a run.

Every sender invocation uses a random nonzero 64-bit run ID unless one is
provided explicitly. Sequences start again at one inside each run. The pair
`(run_id, sequence)` is therefore the command identity; a sequence number alone
is not globally unique.

The first pose packet in a run carries the start-of-run flag. The georeference
packet establishes the run before that pose arrives.

## Pose generation

The UDP wire format always carries a complete local pose: XYZ position plus an
XYZW quaternion. Two Python-side sources now produce that same command:

- `--path` generates line, circle, or hover motion. Roll and pitch are constant;
  yaw follows the path heading plus the requested yaw offset.
- `--trajectory` loads time-stamped XYZ and roll/pitch/yaw rows from CSV.
  Position is linearly interpolated and orientation uses shortest-path SLERP.

CSV timestamps describe the intended simulation trajectory. They do not need
to match the send rate: the sender evaluates the continuous interpolated pose
at each `--rate` interval. The path file is therefore a set of keyframes, not a
packet log.

## Why TCP for camera frames

A JPEG is much larger than a safe UDP datagram. The camera actor therefore
keeps one persistent TCP connection and frames every image with a fixed 64-byte
header. TCP is treated as a byte stream: the receiver reads exactly one header,
validates its advertised sizes, then reads exactly that many JPEG bytes.

The socket remains non-blocking on the Unreal game thread. The camera keeps at
most one partially sent frame and skips capture when a slow client would create
a backlog. This bounds latency and memory instead of quietly buffering old
sensor data.

## Capture ordering and correlation

The camera actor ticks in `TG_PostUpdateWork`, after the pose actor's
`TG_PrePhysics` update. At capture time it copies the most recently applied run
ID, pose sequence, and sender simulation timestamp into the camera header.

This provides state correlation:

```text
pose command (run R, sequence N)
    -> applied by Unreal
    -> camera frame tagged with (R, N)
```

An applied ACK does not mean that a corresponding image has already been
rendered or delivered. The camera header is the evidence linking a delivered
frame to an applied command.

With `Require Applied Pose` disabled, frames may legitimately report
`pose=unavailable` before the first command. With `Capture Only On New Pose`
enabled, the actor sends at most one image for each distinct applied pose.

## Coordinate and time conventions

- Wire positions are metres.
- Unreal positions are centimetres; conversion occurs at the plugin boundary.
- At Cesium's cartographic origin, Unreal +X is east, +Y is south, and +Z is up.
  The current profiles consequently begin facing east along +X.
- The georeference altitude sets the WGS84 ellipsoid origin. The independently
  commanded object height becomes the pose's local +Z coordinate.
- Quaternions use X/Y/Z/W order.
- User-authored orientation uses Unreal Transform degrees: roll about X, pitch
  about Y, and yaw about Z, converted with Unreal's `FRotator` convention.
- The POC applies kinematic pose with teleport semantics. It does not calculate
  velocity, force, aerodynamic response, or rigid-body dynamics.

The path profiles operate in this local tangent frame. Holding object height
constant while moving X/Y is therefore not exactly the same as holding
ellipsoid altitude constant over large distances. That distinction is
negligible for the small POC paths, but a later wide-area simulation should
command cartographic vehicle coordinates or use a Cesium Globe Anchor.

The Python and Windows monotonic clocks do not share an epoch. Raw camera and
sender timestamps must not be subtracted to claim cross-process latency. Pose
sequence correlation is valid; end-to-end time requires an echoed timestamp or
an explicit clock-synchronization measurement.

## Current performance boundary

Scene capture, GPU readback, and JPEG encoding are synchronous in this POC.
Their timings are exposed on the camera actor so measurements can determine
whether asynchronous GPU readback or hardware video encoding is necessary.

The current loop is real-time and asynchronous. Strict deterministic lockstep
would require an explicit step protocol:

```text
StepCommand N
    -> apply state
    -> advance exactly one simulation step
    -> capture image N
    -> StepComplete N
    -> wait for StepCommand N+1
```

Take Recorder should observe this simulation state; it should not own the SIL
clock or command sequencing.
