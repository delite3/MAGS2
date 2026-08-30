# Unreal setup and rebuild

This guide covers the known-good UE 5.8.1 Windows project used by the POC. The
repository contains the project plugin and Python clients; the complete `AM`
project remains external.

Verified project path:

```text
C:\Users\hda\Documents\Unreal Projects\AM
```

## 1. Build prerequisites

Use Visual Studio 2022 with the MSVC and Windows SDK components selected by
Unreal. Cesium for Unreal must also be installed for UE 5.8 and enabled in the
`AM` project because the bridge links against the `CesiumRuntime` module.
If UnrealBuildTool reports a missing `NetFxSDK`, install these Visual Studio
Individual components:

- .NET Framework 4.8 SDK
- .NET Framework 4.8 targeting pack, if offered separately

That repair is specific to the `NetFxSDK` error. The bundled .NET SDK version
shown by Unreal is not a request to install a newer Visual Studio release.

## 2. Verify the base C++ project

For a fresh project setup, prove the empty game module before adding the plugin:

1. Close Unreal Editor.
2. Open `AM.sln`, not `Automation_AM.sln`.
3. Select `Development Editor | Win64`.
4. Build the **AM** project and judge the result from **View > Output**.

The base gate passes when the build succeeds and this file exists:

```text
AM\Binaries\Win64\UnrealEditor-AM.dll
```

If it fails, keep the first build error and approximately 20 surrounding lines.
Later messages are often consequences of the first failure.

## 3. Install or update the plugin

Close Unreal Editor and Visual Studio before copying reflected headers or
`Build.cs` changes. Run from any directory inside the repository:

```bash
REPO_ROOT="$(git rev-parse --show-toplevel)"
UE_PROJECT="/mnt/c/Users/hda/Documents/Unreal Projects/AM"

mkdir -p "$UE_PROJECT/Plugins/SimUdpBridge"
cp -a "$REPO_ROOT/unreal/SimUdpBridge/." \
  "$UE_PROJECT/Plugins/SimUdpBridge/"
```

This updates the descriptor and source without deleting the Windows project's
generated `Binaries` or `Intermediate` directories.

Right-click `AM.uproject`, select **Show more options > Generate Visual Studio
project files**, reopen `AM.sln`, select `Development Editor | Win64`, and
build **AM** again.

Do not use Live Coding for the initial plugin build or after changing reflected
headers or `Build.cs`. The successful plugin build produces:

```text
AM\Plugins\SimUdpBridge\Binaries\Win64\UnrealEditor-SimUdpBridge.dll
```

## 4. Configure the controlled actor

1. Open `AM.uproject`.
2. Select **Edit > Plugins** and confirm both **Cesium for Unreal** and
   **SIL UDP Bridge** are enabled. Restart when requested.
3. Select `CesiumGeoreference0` in the World Outliner and verify:

   ```text
   Origin Placement:  Longitude / Latitude / Height
   Ellipsoid:         WGS84
   ```

   The latitude, longitude, and Origin Height visible here are editor preview
   values. Each Python run replaces all three after PIE starts. Origin Height
   remains the official user-facing WGS84 ellipsoid altitude; it is not the
   traffic cone's local height.
4. Select the traffic cone and configure:

   ```text
   Mobility:         Movable
   Simulate Physics: Off
   ```

5. In **Place Actors**, add **Sim Udp Controlled Actor**.
6. Rename it `SimUdpBridge` in the World Outliner.
7. In **SIL UDP**, configure:

   ```text
   Controlled Actor:            traffic cone
   Cesium Georeference:         CesiumGeoreference0
   Bind Address:                0.0.0.0
   Listen Port:                 5005
   Position Relative To Start:  disabled
   Rotation Relative To Start:  disabled
   Send Acknowledgements:       enabled
   ```

8. Save the level.
9. In **Editor Preferences > General > Performance**, disable **Use Less CPU
   when in Background** for consistent external-loop timing.

The georeference startup packet sets `CesiumGeoreference0` to the sender's
latitude, longitude, and WGS84 ellipsoid height. Keep both relative transform
options disabled so the profile origin is local `(0, 0, 0)` at that Cesium
origin. The sender's independent object height becomes the pose's local +Z
coordinate. Physics must remain disabled because it can overwrite the directly
commanded transform on the next physics step.

## 5. Configure the camera actor

1. In **Place Actors**, add **Sim Camera Streamer Actor**.
2. Rename it `SimCamera`.
3. Position and rotate it at the desired sensor viewpoint. Unreal cameras look
   along local positive X; the component frustum shows the view direction.
4. Configure:

   ```text
   Follow Actor:              traffic cone
   Pose Bridge:               SimUdpBridge
   Require Applied Pose:      disabled
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

5. Save the level.

At `BeginPlay`, the camera attaches to `Follow Actor` using **Keep World
Transform**. Position it visually before PIE: its authored world viewpoint then
becomes a fixed mount relative to the cone. PIE uses a temporary world and does
not rewrite the authored editor transform when stopped.

## 6. Start both listeners

Open **Window > Developer Tools > Output Log** and press **Play**. Confirm:

```text
LogSimUdpBridge: Display: Listening for SIL pose packets on 0.0.0.0:5005
LogSimCameraStreamer: Display: Listening for a SIL camera client on TCP 0.0.0.0:5006
```

The sockets open in `BeginPlay`; opening the map without PIE does not start
them. Only one actor may bind each port. If Windows Firewall prompts, allow
Unreal Editor on the applicable private network only. These POC interfaces are
unauthenticated and should not be exposed publicly.

## 7. Run the Python verification

From the repository root:

```bash
python3 -B -m unittest discover -s tests -v
```

In WSL, find the current Windows gateway:

```bash
ip route show default
```

Use the address after `via`:

```bash
UE_HOST=172.27.240.1
```

First send a slow hover command:

```bash
python3 ue_udp_sender.py \
  --host "$UE_HOST" \
  --path hover \
  --latitude 48.8566 \
  --longitude 2.3522 \
  --altitude 35.5 \
  --object-height 2 \
  --rate 5 \
  --duration 3
```

Expected evidence:

- The Cesium origin is set to the supplied coordinates and the cone is placed
  at local Unreal Z = 200 cm, two metres above that origin.
- Unreal logs a new hexadecimal run ID.
- Python reports applied ACK counts and latency statistics.
- Stopping PIE restores the authored level state.

Then receive image-only camera frames:

```bash
python3 ue_camera_receiver.py \
  --host "$UE_HOST" \
  --duration 15 \
  --output-dir artifacts/camera \
  --save-every 15
```

The Unreal log should report a connected SIL camera client. With pose gating
disabled, frames start immediately and initially may report
`pose=unavailable`. This independently proves capture, readback, JPEG encoding,
TCP framing, and file output.

While the receiver runs, send motion from another terminal:

```bash
python3 ue_udp_sender.py \
  --host "$UE_HOST" \
  --path circle \
  --latitude 48.8566 \
  --longitude 2.3522 \
  --altitude 35.5 \
  --object-height 2 \
  --radius 2 \
  --period 8 \
  --rate 30 \
  --duration 10
```

Successful camera evidence includes valid level images, increasing frame IDs,
no non-monotonic IDs, and pose metadata changing to the active run and applied
sequence after motion begins.

To verify a user-authored position and orientation profile, no plugin rebuild or
new actor setup is needed. Keep both relative transform options disabled and
run:

```bash
python3 ue_udp_sender.py \
  --host "$UE_HOST" \
  --trajectory examples/pose_trajectory.csv \
  --latitude 48.8566 \
  --longitude 2.3522 \
  --altitude 35.5 \
  --rate 30
```

Watch the cone translate and rotate through all five keyframes. The file's XYZ
values are metres in the local Cesium tangent frame; its roll, pitch, and yaw
values are Unreal Transform degrees. If the mesh's visual nose is not local +X,
correct the yaw values in the CSV rather than changing the bridge actor's
relative-rotation setting.

## 8. Enable pose-correlated capture

After image-only capture works, stop PIE and change:

```text
Require Applied Pose:      enabled
Capture Only On New Pose:  enabled
```

The camera now sends nothing before the first applied command and at most one
frame for each distinct applied pose. This is the intended mode for the next
closed-loop milestone:

```text
receive image N -> run CV -> send pose N+1 -> receive tagged image N+1
```

## Optional OpenCV display

Receiving and saving frames has no third-party Python dependency. For display,
install `requirements.txt` in a Python 3.10+ environment, then run:

```bash
python3 ue_camera_receiver.py \
  --host "$UE_HOST" \
  --duration 30 \
  --display
```

Press `q` to stop. Under WSL, a live window also requires working WSLg/X display
support. Saving JPEGs is the cleaner first verification.
