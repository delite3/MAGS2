# Troubleshooting

Start from the first failing boundary. A successful process launch does not
prove that Unreal is in PIE, a socket is bound, a pose was applied, or a camera
frame contains the intended state.

## Build fails

Check these basics first:

- Open `AM.sln`, not `Automation_AM.sln`.
- Select `Development Editor | Win64`.
- Close Unreal Editor before the initial module/plugin build.
- Do not use Live Coding after changing reflected headers or `Build.cs`.
- Read **View > Output** and preserve the first compiler error plus nearby
  context; the Error List often contains secondary failures.

### Missing NetFxSDK

In Visual Studio Installer, add the **.NET Framework 4.8 SDK** and, if offered,
the **.NET Framework 4.8 targeting pack** under Individual components. Rebuild
the empty AM module before adding the plugin.

### Live Coding mutex blocks the build

`Unable to build while Live Coding is active` means UnrealBuildTool still sees
the Live Coding owner, even if no editor window is visible. Close surviving
`UnrealEditor` and `LiveCodingConsole` processes, then run a normal build with
the editor closed.

### `uint64` is not supported by Blueprint

Unreal Header Tool does not expose raw `uint64` properties to Blueprint. Keep
the full run ID as a C++-only property or expose a Blueprint-compatible
representation. The current plugin already follows this rule.

## The AM game module cannot be loaded

Read `AM/Saved/Logs/AM.log` and find the `Failed to load` line with its Windows
error code. Do not assume the DLL is missing merely because Unreal displays the
generic module dialog.

For `GetLastError=4551`, Windows defines the error as
`ERROR_SYSTEM_INTEGRITY_POLICY_VIOLATION`: an Application Control policy blocked
the DLL. Confirm the exact event in PowerShell:

```powershell
Get-WinEvent -FilterHashtable @{
  LogName='Microsoft-Windows-CodeIntegrity/Operational'
  StartTime=(Get-Date).AddMinutes(-10)
} | Where-Object Id -in 3033,3076,3077,3089 |
  Select-Object TimeCreated,Id,Message | Format-List
```

Rebuilding, deleting `Intermediate`, or reinstalling the VC runtime does not
solve an enforced signing policy. On an organization-managed machine, ask the
policy administrator for an approved signer or supplemental allow rule. Do not
disable enterprise security policy as a build workaround.

## Python reports connection refused

Verify in this order:

1. PIE is running. The sockets are created in `BeginPlay`.
2. The Unreal Output Log contains the listener for the expected protocol:

   ```text
   UDP 0.0.0.0:5005
   TCP 0.0.0.0:5006
   ```

3. Only one bridge/camera actor is using each port.
4. From WSL, run `ip route show default` and use the current address after
   `via`; it can change after reboot.
5. Check Windows Firewall only after confirming the listener and address.

HTTP Remote Control on port 30010 is the superseded prototype and is unrelated
to the active UDP/TCP listeners.

## The cone does not move

Check the pose actor and target:

```text
Controlled Actor: the intended traffic cone, not None
Mobility:         Movable
Simulate Physics: Off
```

Also confirm the UDP listener log and that Python receives ACKs. A sender
process running without applied ACKs proves only that it emitted datagrams.

With relative positioning enabled, Z=1 means one metre above the authored
location. Stopping PIE restores the authored state; that is expected.

## No camera frames arrive

Confirm:

- `Streaming Enabled` is on.
- The TCP listener log appears after PIE starts.
- Unreal logs `SIL camera client connected` after Python connects.
- `Follow Actor` and `Pose Bridge` reference the intended actors.
- `Require Applied Pose` is disabled for an image-only test.
- `Capture Only On New Pose` is disabled unless pose commands are arriving.

If both gating options are enabled, a new client can wait until Unreal applies
another distinct pose. Send one command before diagnosing TCP framing.

## Frames report `pose=unavailable`

This is valid while `Require Applied Pose` is disabled and no command has yet
been applied. It proves the image transport independently of motion. After the
first successful pose command, subsequent headers should contain a nonzero run
ID and an applied pose sequence.

## The viewpoint is wrong

Unreal cameras look along local positive X. Place and rotate `SimCamera` before
PIE using its frustum as the guide. At `BeginPlay`, it attaches to `Follow
Actor` with **Keep World Transform**, converting that authored world pose into
a fixed mount relative to the actor.

## JPEGs are white or otherwise uniform

First decide whether the problem exists before or after JPEG/TCP. Unreal logs
the first readback range:

```text
First camera readback RGB min/mean/max: ...
```

The same values appear in the camera actor diagnostics. Values near
`255 / 255 / 255` mean the render target is white before compression. A broad
range means capture works and display/color handling is the next boundary.

The working runtime target uses:

- `PF_B8G8R8A8` through `InitCustomFormat(..., false)`
- `RTF_RGBA8_SRGB`
- target gamma 2.2
- `Always Persist Rendering State` on the SceneCapture component

Directly assigning `RenderTargetFormat` at runtime does not invoke the editor
property-change callback that normally synchronizes the linear-gamma flag.
Explicit custom initialization removes that inconsistent state.

Explicit capture also needs persistent rendering state. Without a view state,
eye-adaptation/pre-exposure history cannot persist and Lumen cannot retain the
scene data it expects. In a physically lit level, Final Color LDR can otherwise
saturate to uniform white.

## OpenCV prints Qt font warnings

Messages such as `QFontDatabase: Cannot find font directory` concern the GUI
window's fonts. They do not modify the decoded JPEG. Use saved frames to verify
image content independently of WSLg/Qt display behavior.

## Camera rate is low or pauses

The POC intentionally prioritizes bounded latency:

- It keeps at most one partially sent TCP frame.
- It skips captures rather than building a backlog behind a slow client.
- GPU readback and JPEG encoding are synchronous.

Inspect captured/sent/skipped counters and the readback/JPEG timing diagnostics
on `SimCamera`. Also disable **Use Less CPU when in Background** in Unreal Editor
preferences when Python has focus.

A long first-frame delay can include TCP connection setup, rendering warm-up,
shader work, and OpenCV window initialization. Judge steady-state timing
separately from startup.
