# Unreal Engine 5.8 SIL UDP proof of concept

This is the first deliberately small part of the eventual SIL loop. Python
sends timestamped vehicle poses to Unreal over UDP; Unreal applies the newest
pose to an existing level actor and returns an acknowledgement after the
transform has been applied on the game thread.

Camera capture, computer vision, vehicle dynamics, and deterministic lockstep
are intentionally not in this milestone. The purpose of this POC is to prove
and measure one boundary before adding the next one.

## Verified starting state (2026-08-23)

The Unreal project is:

```text
C:\Users\hda\Documents\Unreal Projects\AM
```

Read-only inspection found:

- UE 5.8.1 is installed.
- `AM` is now a C++ project with an empty `AAMBootstrap` class.
- `AM.sln` exists and contains `Development Editor | Win64`.
- Visual Studio 2022 Community 17.14, MSVC 14.44, and Windows SDK 26100 are
  installed; Unreal reports Win64 as buildable.
- The `AM` module has never completed its first build, and
  `Binaries\Win64\UnrealEditor-AM.dll` does not exist.
- Project generation reports a missing .NET Framework SDK:

  ```text
  Could not find NetFxSDK install dir ...
  Install a version of .NET Framework SDK at 4.6.0 or higher.
  ```

- Editor background throttling is still enabled.
- No `SimUdpBridge` plugin has been copied into `AM` or compiled.

This means the first gate is the C++ toolchain, not UDP.

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
has been rendered or delivered. Frame-level acknowledgement belongs in the
later camera/lockstep milestone.

Unreal positions are centimetres; the wire uses metres. Unreal's axes are
treated as X forward, Y right, Z up. The bridge converts metres to centimetres
at the boundary and uses teleport semantics, so this POC commands kinematic
pose—it does not calculate velocity or aerodynamic/rigid-body response.
