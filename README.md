# Unreal SIL UDP proof of concept

This proof of concept sends a timestamped pose path from Python to Unreal Engine
5.8 over UDP. Unreal applies the newest command once per game tick and sends a
small acknowledgement after the transform has been applied.

The initial protocol deliberately carries only pose data. Camera capture and
explicit lockstep stepping should be added after this transport is measured.

## Unreal Engine 5.8 setup

The current `AM` project is Blueprint-only. Convert it into a buildable C++
project before installing this source plugin:

1. Open `AM.uproject` in Unreal Editor.
2. Select **Tools > New C++ Class**.
3. Choose **Actor**, click **Next**, name it `AMBootstrap`, and click
   **Create Class**. The class can remain empty; its purpose is to make Unreal
   generate the project's C++ module and target files.
4. If Unreal asks for an IDE, use a UE 5.8-compatible Visual Studio toolchain.
5. Close Unreal Editor before copying the plugin.

From WSL, copy the complete plugin folder into the project:

```bash
mkdir -p "/mnt/c/Users/hda/Documents/Unreal Projects/AM/Plugins"
cp -r "/home/hda/Git/mags/unreal/SimUdpBridge" \
  "/mnt/c/Users/hda/Documents/Unreal Projects/AM/Plugins/"
```

Then in Windows:

1. Right-click `AM.uproject` and select **Generate Visual Studio project
   files**. On Windows 11 this may be under **Show more options**.
2. Open `AM.sln`.
3. Set the solution configuration to **Development Editor** and platform to
   **Win64**.
4. Build the `AM` project. Do not use Live Coding for this first plugin build.
5. Open `AM.uproject`. If needed, select **Edit > Plugins**, search for
   `SIL UDP Bridge`, enable it, and restart the editor.

### Configure the level

1. Select the traffic cone in the World Outliner.
2. In **Details > Transform**, set **Mobility** to **Movable**. Unreal will not
   runtime-move a static component reliably.
3. Open **Place Actors**, search for `Sim Udp Controlled Actor`, and drag one
   instance into the level. Its own location is irrelevant.
4. Select the new bridge actor. Under **Details > SIL UDP**:
   - Set **Controlled Actor** to the traffic cone using the eyedropper.
   - Leave **Bind Address** at `0.0.0.0`.
   - Leave **Listen Port** at `5005`.
   - Leave both relative-transform options enabled.
   - Leave acknowledgements enabled.
5. Save the level.
6. Disable **Edit > Editor Preferences > General > Performance > Use Less CPU
   when in Background**.
7. Press **Play**. The UDP socket exists only while BeginPlay/EndPlay are active.
8. Open **Window > Developer Tools > Output Log** and confirm:

   ```text
   LogSimUdpBridge: Display: Listening for SIL pose packets on 0.0.0.0:5005
   ```

The first inbound packet may make Windows Firewall prompt for Unreal Editor.
Allow it on the applicable private network. Do not expose this unauthenticated
POC port to an untrusted/public network.

## Protocol

All multibyte values use network byte order (big endian).

Pose packet, 48 bytes:

| Offset | Type | Meaning |
| ---: | --- | --- |
| 0 | 4 bytes | `SUDP` magic |
| 4 | `uint8` | protocol version (`1`) |
| 5 | `uint8` | flags, currently zero |
| 6 | `uint16` | reserved, zero |
| 8 | `uint32` | sequence number |
| 12 | `uint64` | sender simulation time in nanoseconds |
| 20 | 3 x `float32` | X/Y/Z position in metres |
| 32 | 4 x `float32` | X/Y/Z/W quaternion |

ACK packet, 12 bytes: `SACK`, version, status, reserved, applied sequence.
Status zero means the pose was applied; status one means an invalid packet.

## Python test

Run the standard-library tests:

```bash
python3 -m unittest discover -s tests -v
```

After completing the Unreal setup and entering Play In Editor:

```bash
python3 ue_udp_sender.py --host 172.27.240.1 --path line --duration 10
```

The default Windows host address is copied from the earlier working Remote
Control setup. If WSL's Windows host address changes, obtain the current value
with:

```bash
ip route show default
```

Use the address after `via` as `--host`. Other useful tests are:

```bash
python3 ue_udp_sender.py --host 172.27.240.1 --path hover --altitude 2
python3 ue_udp_sender.py --host 172.27.240.1 --path circle --radius 2 --period 8
python3 ue_udp_sender.py --host 172.27.240.1 --path line --rate 100 --speed 3
```

Coordinates follow Unreal's axes (X forward, Y right, Z up), but positions are
specified in metres. The plugin converts them to Unreal centimetres.

With the default line command, the cone should immediately rise two metres and
then move along positive X at one metre per second. A ten-second, 30 Hz run sends
about 300 commands. The plugin intentionally applies only the newest packet per
Unreal tick, so an ACK count below the send count at high command rates is not
automatically packet loss.
