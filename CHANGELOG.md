# Changelog

Notable changes to the SIL proof of concept are recorded here. Project release
versions, pose-protocol versions, and camera-protocol versions are independent.

## Unreleased

### Changed

- Reorganized the setup, architecture, protocol, and troubleshooting guidance.
- Removed the superseded Remote Control HTTP example.
- Added explicit local-artifact ignores, optional display dependencies, and
  Python protocol CI.
- Normalized the plugin display version to `0.3.2` without changing its
  monotonic integer version or runtime behavior.

## 0.3.2 - 2026-08-24

### Added

- Tagged JPEG camera frames over a persistent TCP connection.
- Camera frame metadata correlating images with applied pose commands.
- Python camera framing, validation, saving, display, and protocol tests.

### Fixed

- BGRA8/sRGB render-target initialization and explicit gamma handling.
- Persistent SceneCapture view state for exposure history and Lumen data.

## 0.2.0 - 2026-08-23

### Added

- Versioned UDP pose commands and applied acknowledgements.
- Run-scoped sequence numbers, newest-command-wins behavior, and Python tests.
- Unreal actor control in `TG_PrePhysics`.

## Initial prototype - 2026-08-22

- Demonstrated external pose control through Unreal Remote Control HTTP.
