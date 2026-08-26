# Wire protocols

All multibyte values use network byte order (big endian). The pose and camera
protocols are versioned independently from each other and from the plugin's
release version.

## Pose command

Each UDP command is exactly 56 bytes.

| Offset | Type | Meaning |
| ---: | --- | --- |
| 0 | 4 bytes | `SUDP` command magic |
| 4 | `uint8` | pose protocol version (`2`) |
| 5 | `uint8` | flags; bit 0 marks the beginning of a run |
| 6 | `uint16` | reserved; must be zero |
| 8 | `uint64` | nonzero run ID |
| 16 | `uint32` | sequence within that run |
| 20 | `uint64` | sender simulation time in nanoseconds |
| 28 | 3 x `float32` | X/Y/Z position in metres |
| 40 | 4 x `float32` | X/Y/Z/W quaternion |

Unreal rejects packets with the wrong length, magic, version, flags, reserved
value, non-finite pose values, zero run ID, or zero-length quaternion.

The sender uses a new random run ID for each invocation by default. Sequence
numbers are interpreted only inside that run. Unreal retains the newest valid
waiting sequence and applies at most one command per tick.

## Pose acknowledgement

Each UDP acknowledgement is exactly 20 bytes.

| Offset | Type | Meaning |
| ---: | --- | --- |
| 0 | 4 bytes | `SACK` acknowledgement magic |
| 4 | `uint8` | pose protocol version (`2`) |
| 5 | `uint8` | status code |
| 6 | `uint16` | reserved; zero |
| 8 | `uint64` | echoed run ID |
| 16 | `uint32` | echoed applied or rejected sequence |

Status codes:

| Value | Name | Meaning |
| ---: | --- | --- |
| 0 | Applied | The transform call succeeded on the Unreal game thread |
| 1 | Invalid packet | The datagram did not satisfy the wire contract |
| 2 | Rejected | The command was stale or otherwise not eligible to apply |
| 3 | Apply failed | Unreal could not apply the requested transform |

An Applied ACK confirms the actor transform call. It does not claim that a
camera frame containing that pose has already been rendered or delivered.

## Camera frame

Each TCP message contains one 64-byte header followed immediately by the
advertised number of JPEG bytes.

| Offset | Type | Meaning |
| ---: | --- | --- |
| 0 | 4 bytes | `SIMG` camera magic |
| 4 | `uint8` | camera protocol version (`1`) |
| 5 | `uint8` | encoding (`1` = JPEG) |
| 6 | `uint16` | header size (`64`) |
| 8 | `uint32` | flags; bit 0 means applied-pose metadata is valid |
| 12 | `uint32` | JPEG payload bytes following the header |
| 16 | `uint64` | applied run ID, or zero when unavailable |
| 24 | `uint32` | applied pose sequence, or zero |
| 28 | `uint16` | image width |
| 30 | `uint16` | image height |
| 32 | `uint64` | applied sender simulation time in nanoseconds, or zero |
| 40 | `uint64` | camera frame ID |
| 48 | `uint64` | camera actor tick ID |
| 56 | `uint64` | capture request time since camera `BeginPlay`, nanoseconds |

When the pose-valid flag is clear, the run, sequence, and applied simulation
time fields must all be zero. When it is set, the run ID must be nonzero.

TCP provides ordered bytes, not application messages. The Python receiver
therefore:

1. Reads exactly 64 header bytes.
2. Validates magic, versions, flags, dimensions, and configured size limits.
3. Reads exactly the advertised JPEG payload length.
4. Treats EOF during either read as an incomplete frame.

Unreal manually serializes each field. It never sends a native C++ structure,
so compiler padding and host endianness do not affect the contract.

## Coordinate conversion

- Wire positions are metres; Unreal world positions are centimetres.
- The bridge multiplies position by 100 at the boundary.
- Axes are X forward, Y right, Z up.
- Quaternion components are ordered X, Y, Z, W.

## Timestamp interpretation

The command timestamp belongs to the Python simulation timeline and is echoed
into camera metadata after that pose is applied. The camera capture timestamp
belongs to Unreal's monotonic timeline since `BeginPlay`.

Those clocks do not share an epoch. Their raw values provide ordering and
correlation, but cannot be subtracted to calculate end-to-end latency without
clock synchronization or an echoed timestamp measurement.
