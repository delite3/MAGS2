# Unreal Engine 5.8 SIL proof of concept

This repository contains a working bidirectional software-in-the-loop POC:

```text
Python -- UDP pose command --> Unreal controlled actor
Python <-- applied UDP ACK  -- Unreal controlled actor

Python <-- tagged JPEG/TCP  -- Unreal SceneCapture camera
```

Pose commands use UDP because they are small, frequent, and newest-wins. Camera
frames use one persistent TCP connection because JPEGs are too large for safe
single UDP datagrams and this POC does not need image-chunk reassembly.

## Verified state

Verified on 2026-08-26 with UE 5.8.1 and the Windows project at:

```text
C:\Users\hda\Documents\Unreal Projects\AM
```

- The `AM` C++ module and `SimUdpBridge` plugin build successfully.
- WSL Python drives the cone through versioned UDP commands and receives
  applied acknowledgements.
- A measured 60 Hz run sent 600 commands and received 301 newest-per-tick
  applied ACKs, with 4.50 ms median and 16.52 ms p95 latency.
- The camera sends valid 320x240 JPEG frames over TCP and tags them with the
  most recently applied run, pose sequence, and simulation timestamp.
- The BGRA8/sRGB and persistent-view-state fixes produce non-white scene images.
- All 20 Python protocol tests pass.

Computer vision, vehicle dynamics, asynchronous GPU readback, hardware video
encoding, and deterministic lockstep stepping remain later milestones.

## Repository layout

```text
.
├── sim_udp_protocol.py              # UDP command/ACK wire format
├── sim_camera_protocol.py           # TCP camera frame wire format
├── ue_udp_sender.py                 # Path generator and ACK metrics
├── ue_camera_receiver.py            # JPEG receiver, saver, and display
├── tests/                            # Standard-library protocol tests
├── unreal/SimUdpBridge/              # UE project plugin source
├── docs/                             # Setup, architecture, protocol, recovery
└── requirements-display.txt          # Optional OpenCV display dependencies
```

The repository tracks integration code, not the complete Windows `AM` project.
Back up or version the project level, configuration, and custom assets separately;
do not rely on this repository alone to reproduce their editor state.

## Requirements

- Unreal Engine 5.8.1 on Windows.
- Visual Studio 2022 with the MSVC toolchain and Windows SDK used by UE.
- Python 3.10 or newer. The verified environment uses Python 3.12.
- OpenCV and NumPy only for `ue_camera_receiver.py --display`. Receiving and
  saving JPEGs uses the Python standard library.

See [Unreal setup](docs/unreal-setup.md) when installing or rebuilding the
plugin. The quick start below assumes both actors are already configured.

## Quick start

Run commands from the repository root.

### 1. Run the Python tests

```bash
python3 -B -m unittest discover -s tests -v
```

### 2. Start Unreal

Open the `AM` project, open the Output Log, and press **Play**. Confirm both
listeners start:

```text
LogSimUdpBridge: Display: Listening for SIL pose packets on 0.0.0.0:5005
LogSimCameraStreamer: Display: Listening for a SIL camera client on TCP 0.0.0.0:5006
```

Sockets are created in `BeginPlay`; merely opening the level is not enough.

### 3. Determine the Windows address from WSL

```bash
ip route show default
```

Use the address after `via`. Store it for the current shell, for example:

```bash
UE_HOST=172.27.240.1
```

The address is an example and can change after a reboot.

### 4. Receive camera frames

```bash
python3 ue_camera_receiver.py \
  --host "$UE_HOST" \
  --duration 15 \
  --output-dir artifacts/camera \
  --save-every 15
```

### 5. Move the cone

In a second terminal, from the same repository root:

```bash
python3 ue_udp_sender.py \
  --host "$UE_HOST" \
  --path circle \
  --altitude 2 \
  --radius 2 \
  --period 8 \
  --rate 30 \
  --duration 10
```

`--speed` controls only the line path; circle speed is set by `--period`.

To display frames, install `requirements-display.txt` in your chosen Python
environment and add `--display`. Press `q` in the OpenCV window to stop.

## Detailed documentation

- [Unreal setup and rebuild](docs/unreal-setup.md)
- [Architecture and timing model](docs/architecture.md)
- [Wire protocols](docs/protocol.md)
- [Troubleshooting](docs/troubleshooting.md)
- [Change history](CHANGELOG.md)

The next implementation milestone is one combined client performing:

```text
receive image N -> run CV -> send pose N+1 -> receive tagged image N+1
```
