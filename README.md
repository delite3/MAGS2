# Unreal Engine 5.8 SIL proof of concept

This repository now proves both directions of the first SIL loop:

```text
Python -- UDP pose command --> Unreal controlled actor
Python <-- applied UDP ACK  -- Unreal controlled actor

Python <-- tagged JPEG/TCP  -- Unreal SceneCapture camera
```

The pose path is newest-wins and intentionally lossy. The camera path uses one
persistent TCP connection because a JPEG is much larger than a safe UDP
datagram and a local POC does not benefit from implementing image chunk
reassembly yet.

Computer vision, vehicle dynamics, asynchronous GPU readback, hardware video
encoding, and deterministic simulation stepping remain later milestones.

## Current verified state (2026-08-23)

The Unreal project is:

```text
C:\Users\hda\Documents\Unreal Projects\AM
```

The original environment inspection found:

- UE 5.8.1 is installed.
- `AM` is now a C++ project with an empty `AAMBootstrap` class.
- `AM.sln` exists and contains `Development Editor | Win64`.
- Visual Studio 2022 Community 17.14, MSVC 14.44, and Windows SDK 26100 are
  installed; Unreal reports Win64 as buildable.
- The C++ module and `SimUdpBridge` plugin now build successfully.
- The cone has been driven from WSL at 60 command packets per second.
- A measured run sent 600 commands, applied 301 newest-per-tick commands, and
  reported 4.50 ms median / 16.52 ms p95 applied-ACK latency.
- The Python protocol test suite passes.
- The camera streamer is implemented in this repository and must now be copied,
  rebuilt, placed, and exercised in the AM project.

## Architecture and why it is arranged this way

```text
Python path generator
    -> 56-byte UDP command
Windows UDP socket in Unreal
    -> validate and retain newest sequence
Unreal PrePhysics tick (game thread)
    -> apply cone transform
    -> 20-byte UDP ACK
Python
    -> record applied-ACK round-trip latency

Unreal PostUpdateWork tick
    -> explicitly render SceneCapture2D to an RGBA8 render target
    -> synchronously read pixels and encode JPEG (POC implementation)
    -> prepend applied run/sequence/timestamp metadata
    -> write one bounded frame over non-blocking TCP
Python
    -> validate header, receive JPEG, optionally save or OpenCV-decode
```

The UE code is a project plugin rather than an engine modification. That keeps
the POC isolated, removable, reusable, and independent of the Cesium, vehicle,
and Remote Control content already in `AM`.

The socket is non-blocking and polled during `Tick`. For this small POC that is
preferable to a receiver thread: Unreal Actors and Components belong to the
game thread, so polling removes a cross-thread queue and makes the ACK's meaning
unambiguous. `TG_PrePhysics` applies the state before the frame's physics work.

Every Python invocation gets a random 64-bit run ID. Sequence numbers only have
meaning within that run. This prevents a second Python invocation, which starts
again at sequence 1, from being mistaken for stale traffic from the first run.

Only the newest waiting command is applied each Unreal tick. This avoids a UDP
backlog turning into growing control latency. Consequently, sending at 100 Hz
to a 30 or 60 Hz Unreal loop will intentionally produce fewer applied ACKs than
commands.

The camera owns a second actor and runs in `TG_PostUpdateWork`, after the pose
actor's `TG_PrePhysics` update. It keeps at most one partially sent frame, never
waits on the TCP socket in the game thread, and skips capture rather than
building latency when a client is slow. The current GPU readback and JPEG
compression are synchronous; their measured times are visible on the camera
actor so this POC can tell us whether async readback is the next necessary step.

## Original pose-control setup

The following gates document the setup already completed on the current AM
project. Keep them for rebuilding the project from scratch; continue at
**Camera Gate 1** for the new work.

## Gate 1: finish the prerequisite

Close Unreal Editor. In Windows:

1. Open **Visual Studio Installer**.
2. Find **Visual Studio Community 2022** and select **Modify**.
3. Open **Individual components**.
4. Search for and enable **.NET Framework 4.8 SDK**.
5. If offered separately and not already selected, also enable the
   **.NET Framework 4.8 targeting pack**.
6. Select **Modify** and let the installer finish.

This addresses the exact `NetFxSDK` error. It is not a request to install
Visual Studio 2026, and the `.NET 10` Automation solution warning is unrelated
to building the AM game module with Visual Studio 2022.

### Gate 1 evidence

Visual Studio Installer must finish successfully. If it does not, stop here.

## Gate 2: build the empty AM module first

This deliberately tests Unreal, Visual Studio, MSVC, Windows SDK, UBT, and the
generated `AM` module without any UDP code involved.

1. Keep Unreal Editor closed.
2. Open:

   ```text
   C:\Users\hda\Documents\Unreal Projects\AM\AM.sln
   ```

   Do not open `Automation_AM.sln`.

3. In the Visual Studio toolbar select:

   ```text
   Solution Configuration: Development Editor
   Solution Platform:       Win64
   ```

4. In Solution Explorer, right-click the **AM** project and select **Build**.
5. Judge the result from **View > Output**, not from the Error List.

### Gate 2 evidence

The build must end successfully and this file must exist:

```text
C:\Users\hda\Documents\Unreal Projects\AM\Binaries\Win64\UnrealEditor-AM.dll
```

Do not copy the UDP plugin until this gate passes. If it fails, preserve the
first compiler/build error and roughly 20 lines around it.

## Gate 3: copy the reviewed UDP plugin

With Unreal and Visual Studio closed, run in WSL:

```bash
mkdir -p "/mnt/c/Users/hda/Documents/Unreal Projects/AM/Plugins"
cp -r "/home/hda/Git/mags/unreal/SimUdpBridge" \
  "/mnt/c/Users/hda/Documents/Unreal Projects/AM/Plugins/"
```

The result must be:

```text
AM/
└── Plugins/
    └── SimUdpBridge/
        ├── SimUdpBridge.uplugin
        └── Source/
```

Right-click `AM.uproject`, choose **Show more options** if necessary, then
choose **Generate Visual Studio project files**. Reopen `AM.sln`, keep
`Development Editor | Win64`, and build **AM** again.

### Gate 3 evidence

The second build must succeed, and the plugin should produce a DLL under:

```text
AM\Plugins\SimUdpBridge\Binaries\Win64\
```

Do not use Live Coding for this first plugin build. Build-system and reflected
header changes are safest with the Editor closed.

## Gate 4: enable and place the bridge in Unreal

1. Open `AM.uproject`.
2. Select **Edit > Plugins**.
3. Search for **SIL UDP Bridge** and confirm it is enabled. Restart if asked.
4. Select the traffic cone in the World Outliner.
5. In the cone's Details panel:

   ```text
   Mobility:         Movable
   Simulate Physics: Off
   ```

   Static mobility can prevent runtime transforms. Physics simulation can
   overwrite a directly commanded pose on the following physics step.

6. Open **Place Actors**, search for **Sim Udp Controlled Actor**, and drag one
   instance into the level.
7. Rename it `SimUdpBridge` in the World Outliner.
8. In its **SIL UDP** Details section configure:

   ```text
   Controlled Actor:            the traffic cone
   Bind Address:                0.0.0.0
   Listen Port:                 5005
   Position Relative To Start:  enabled
   Rotation Relative To Start:  enabled
   Send Acknowledgements:       enabled
   ```

9. Save the level.
10. Open **Edit > Editor Preferences > General > Performance** and disable
    **Use Less CPU when in Background**.

Relative position means Python `(0, 0, 1)` moves the cone one metre above its
authored level position rather than teleporting it to world coordinate
`(0, 0, 100)`.

### Gate 4 evidence

The bridge appears in the World Outliner, and `Controlled Actor` displays the
cone rather than `None`.

## Gate 5: start the receiver

1. Open **Window > Developer Tools > Output Log**.
2. Press **Play**. The socket opens in `BeginPlay`; merely opening the level
   does not start it.
3. Look for:

   ```text
   LogSimUdpBridge: Display: Listening for SIL pose packets on 0.0.0.0:5005
   ```

If Windows Firewall prompts, allow Unreal Editor on the applicable **Private**
network only. This POC is unauthenticated and should not be exposed publicly.

### Gate 5 evidence

The exact listener message appears without a bind error. Only one bridge actor
may use port 5005.

## Gate 6: run the smallest Python test

The Python side uses only the standard library. Verify it first:

```bash
cd /home/hda/Git/mags
python3 -B -m unittest discover -s tests -v
```

Keep PIE running. Start with a slow, three-second hover command:

```bash
python3 ue_udp_sender.py \
  --host 172.27.240.1 \
  --path hover \
  --altitude 1 \
  --rate 5 \
  --duration 3
```

If the WSL-to-Windows address has changed, run `ip route show default` and use
the address after `via` as `--host`.

Expected behavior:

- The cone moves one metre upward relative to its starting location.
- Unreal logs a new hexadecimal SIL run ID.
- Python receives applied ACKs and prints median, p95, and maximum latency.
- Stopping PIE restores the level's authored cone location. PIE runs a temporary
  world; that restoration is expected.

After the hover gate passes, try:

```bash
python3 ue_udp_sender.py --host 172.27.240.1 --path line --altitude 2 --rate 30 --duration 5
python3 ue_udp_sender.py --host 172.27.240.1 --path circle --altitude 2 --radius 2 --period 8 --rate 30 --duration 10
```

Each invocation has a new run ID, so these may be run during the same PIE
session. Use only one Python sender at a time in this POC.

## Camera Gate 1: install the updated plugin source

Close Unreal Editor and Visual Studio. Reflected headers and `Build.cs` changed,
so do not use Live Coding for this update.

From WSL, copy the repository version over the existing project plugin:

```bash
cp -a "/home/hda/Git/mags/unreal/SimUdpBridge/." \
  "/mnt/c/Users/hda/Documents/Unreal Projects/AM/Plugins/SimUdpBridge/"
```

This updates source and the plugin descriptor without deleting the project's
generated `Binaries` or `Intermediate` directories.

Right-click `AM.uproject`, select **Show more options > Generate Visual Studio
project files**, open `AM.sln`, select `Development Editor | Win64`, and build
the **AM** project.

Expected evidence:

- The build finishes with `Build: 1 succeeded` (or reports the project as up to
  date).
- `Sim Camera Streamer Actor` becomes a placeable C++ actor after opening UE.

If compilation fails, preserve the first compiler error and approximately 20
lines around it. Later errors are often consequences of the first one.

## Camera Gate 2: place and configure the sensor

1. Open `AM.uproject` and the level containing the cone.
2. In **Place Actors**, search for **Sim Camera Streamer Actor**.
3. Drag one instance into the level and rename it `SimCamera`.
4. Use the viewport move/rotate tools to place `SimCamera` at the desired sensor
   viewpoint. A good first location is roughly one metre above the cone origin.
   Unreal cameras look along their local positive X axis; the component frustum
   shows the viewing direction.
5. In the actor's **SIL Camera** sections configure:

   ```text
   Follow Actor:              traffic cone
   Pose Bridge:               existing SimUdpBridge actor
   Require Applied Pose:      disabled for the first image-only test
   Capture Only On New Pose:  disabled

   Image Width:               320
   Image Height:              240
   Capture Rate Hz:           15
   Jpeg Quality:              80
   Field Of View Degrees:     90

   Bind Address:              0.0.0.0
   Listen Port:               5006
   Streaming Enabled:         enabled
   Client Stall Timeout:      2
   ```

6. Save the level.

At `BeginPlay`, the camera attaches to `Follow Actor` using **Keep World
Transform**. This is why it is positioned visually first: its authored world
viewpoint becomes a fixed mount relative to the cone and follows subsequent
cone translation and rotation. The attachment only exists in the temporary PIE
world; the authored editor placement is not modified when PIE stops.

## Camera Gate 3: start Unreal and verify both listeners

Open **Window > Developer Tools > Output Log**, press **Play**, and find both:

```text
LogSimUdpBridge: Display: Listening for SIL pose packets on 0.0.0.0:5005
LogSimCameraStreamer: Display: Listening for a SIL camera client on TCP 0.0.0.0:5006
```

The render target is created at runtime. The camera performs no extra scene
renders until a Python TCP client connects. If Windows Firewall prompts again,
allow Unreal Editor only on the applicable private network.

## Camera Gate 4: receive and save sample images

In WSL terminal A:

```bash
cd /home/hda/Git/mags
python3 ue_camera_receiver.py \
  --host 172.27.240.1 \
  --duration 15 \
  --output-dir received_camera_frames \
  --save-every 15
```

Expected Unreal log:

```text
LogSimCameraStreamer: Display: SIL camera client connected from ...
```

Because `Require Applied Pose` is disabled for this first gate, images begin
immediately and Python initially prints `pose=unavailable`. That proves capture,
readback, JPEG encoding, TCP framing, and file output independently of motion.

While terminal A is still receiving, use terminal B to move the cone:

```bash
cd /home/hda/Git/mags
python3 ue_udp_sender.py \
  --host 172.27.240.1 \
  --path circle \
  --rate 30 \
  --duration 10 \
  --speed 1
```

After Unreal applies the first command, receiver status changes to a hexadecimal
run ID and `pose_seq=...`. Open the JPEG files under:

```text
/home/hda/Git/mags/received_camera_frames
```

Success means:

- The saved files are valid images of the level.
- The viewpoint follows the moving cone.
- Python reports increasing camera frame IDs with zero gaps/non-monotonic IDs.
- Frames captured during motion contain the current run ID and an applied pose
  sequence.
- Unreal's `SimCamera` Details diagnostics show increasing Captured/Sent counts
  and plausible readback/JPEG times.

The two monotonic clocks in Windows UE and WSL Python do not share an epoch, so
the receiver intentionally does not claim an end-to-end latency from their raw
timestamps. Pose sequence correlation is valid; cross-process latency needs an
echoed Python timestamp or a clock-synchronization measurement in the combined
closed-loop client.

### White or otherwise uniform images

The runtime render target must be initialized with a consistent BGRA8/sRGB
format. Assigning `RTF_RGBA8_SRGB` directly does not invoke Unreal Editor's
property-change callback, so the plugin uses `InitCustomFormat(...,
PF_B8G8R8A8, false)` and an explicit target gamma of 2.2.

Because capture is explicit rather than every rendered frame, the component
also enables **Always Persist Rendering State**. UE otherwise creates no view
state, leaves pre-exposure at its fallback, cannot retain eye-adaptation
history, and cannot supply the persistent scene data required by Lumen. In a
physically lit level this can make Final Color LDR saturate to pure white.

On the first received frame, Unreal logs:

```text
First camera readback RGB min/mean/max: ...
```

The same values remain visible under **SIL Camera > Diagnostics**. A useful
scene image normally has a meaningful range between minimum and maximum. Values
near `255 / 255 / 255` mean the render target itself is uniformly white before
JPEG/TCP; values with a broad range mean capture works and the next diagnostic
is display/color handling. Qt font warnings from `cv2.imshow` affect window
fonts and do not alter decoded pixels.

## Optional live OpenCV display

The receiver has no third-party dependency when it only receives or saves
JPEGs. If the Python environment already contains OpenCV and NumPy, use:

```bash
python3 ue_camera_receiver.py \
  --host 172.27.240.1 \
  --duration 30 \
  --display
```

Press `q` in the image window to stop. Under WSL, display also requires working
WSLg/X display support. Saving JPEGs is the cleaner first verification.

## Camera Gate 5: enable pose-correlated sensor behavior

After the image-only gate succeeds, stop PIE and change:

```text
Require Applied Pose:      enabled
Capture Only On New Pose:  enabled
```

Now the actor sends no image before an applied command and sends at most one
image for each distinct applied pose. This is the correct mode for the next
request/response milestone:

```text
receive image N -> run CV -> send pose N+1 -> receive tagged image N+1
```

The current `ue_udp_sender.py` and `ue_camera_receiver.py` are intentionally
separate diagnostic programs. Combining them with the computer-vision function
is the next small change after both directions are independently measured.

## Wire protocol

All multibyte values use network byte order (big endian).

Command packet, 56 bytes:

| Offset | Type | Meaning |
| ---: | --- | --- |
| 0 | 4 bytes | `SUDP` command magic |
| 4 | `uint8` | protocol version (`2`) |
| 5 | `uint8` | bit 0 marks the beginning of a run |
| 6 | `uint16` | reserved; must be zero |
| 8 | `uint64` | run ID |
| 16 | `uint32` | sequence within that run |
| 20 | `uint64` | sender simulation time in nanoseconds |
| 28 | 3 x `float32` | X/Y/Z position in metres |
| 40 | 4 x `float32` | X/Y/Z/W quaternion |

ACK packet, 20 bytes:

| Offset | Type | Meaning |
| ---: | --- | --- |
| 0 | 4 bytes | `SACK` magic |
| 4 | `uint8` | protocol version (`2`) |
| 5 | `uint8` | status: applied, invalid, rejected, or apply failed |
| 6 | `uint16` | reserved; zero |
| 8 | `uint64` | echoed run ID |
| 16 | `uint32` | echoed sequence |

An `Applied` ACK means `SetActorLocationAndRotation` succeeded on the Unreal
game thread. It does **not** yet mean that a camera frame containing that pose
has been rendered or delivered. The camera header closes that ambiguity by
copying the most recently applied run, sequence, and simulation timestamp at
the point where capture is requested.

Unreal positions are centimetres; the wire uses metres. Unreal's axes are
treated as X forward, Y right, Z up. The bridge converts metres to centimetres
at the boundary and uses teleport semantics, so this POC commands kinematic
pose—it does not calculate velocity or aerodynamic/rigid-body response.

Camera frame message, one 64-byte header followed by `payload bytes` of JPEG:

| Offset | Type | Meaning |
| ---: | --- | --- |
| 0 | 4 bytes | `SIMG` camera magic |
| 4 | `uint8` | camera protocol version (`1`) |
| 5 | `uint8` | encoding (`1` = JPEG) |
| 6 | `uint16` | header size (`64`) |
| 8 | `uint32` | flags; bit 0 means applied-pose metadata is valid |
| 12 | `uint32` | JPEG payload bytes following the header |
| 16 | `uint64` | applied run ID, or zero when pose metadata is unavailable |
| 24 | `uint32` | applied pose sequence, or zero |
| 28 | `uint16` | image width |
| 30 | `uint16` | image height |
| 32 | `uint64` | applied Python simulation time in nanoseconds, or zero |
| 40 | `uint64` | camera frame ID |
| 48 | `uint64` | camera actor tick ID |
| 56 | `uint64` | capture request time since camera `BeginPlay`, nanoseconds |

TCP is a byte stream rather than a message protocol. Python therefore reads
exactly 64 bytes, validates the advertised dimensions and payload size, and
only then reads exactly the advertised JPEG bytes. Unreal manually serializes
every integer in network byte order; it never transmits a padded native C++
struct.
