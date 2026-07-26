# Open-RDK Native C Host (`openrdkC`) Implementation Plan

## 1. Objective and non-negotiable compatibility rules

Build an optional native host runtime whose public entry point is:

```python
from openrdkC import CommsRuntimeC
```

The existing Python host remains the standard implementation:

```python
from openrdk import CommsRuntime
```

The native implementation must obey these rules:

1. Do not modify, replace, rename, or wrap `host/main/src/openrdk`.
2. Do not change the behavior of the `openrdk` package or its `openrdk` console command.
3. Install the native implementation as a separate Python distribution and import package named `openrdkC`.
4. Allow `openrdk` and `openrdkC` to be installed in the same Python environment.
5. Do not open the same serial device from both runtimes simultaneously.
6. Treat `protocol/protocol.md` and the current firmware as the protocol source of truth.
7. Reach behavioral parity before optimizing or extending the protocol.
8. Keep the Python-facing SDK convenient; native handles and pointers must not leak into normal user code.

## 2. Target architecture

```text
User program
    |
    | from openrdkC import CommsRuntimeC
    v
Python compatibility layer (openrdkC)
    |- CommsRuntimeC
    |- BaseModuleC
    |- TractionModuleC
    |- LineSensorModuleC
    |- ColorSensorModuleC
    |- DistanceSensorModuleC
    `- Python dictionaries / exceptions
    |
    | CPython extension calls (coarse-grained API)
    v
_openrdkC native extension
    |- Runtime ownership and lifecycle
    |- Per-device command queues
    |- In-memory device registry
    |- Typed telemetry snapshots
    `- Python object conversion at the API boundary
    |
    v
libopenrdkc
    |- udev discovery
    |- epoll/eventfd/timerfd event loop
    |- serial configuration and reconnect
    |- framing and sequence handling
    |- per-device protocol state machines
    |- bounded telemetry ring buffers
    |- keepalive and timeout scheduler
    `- asynchronous logging queue
```

The native runtime should use one event-loop thread for all serial ports. It may use one additional persistence/logging thread. It must not create one Python thread or one serial-reader thread per device.

## 3. Proposed isolated directory structure

Create all native-host work below `CHost`:

```text
CHost/
├── IMPLEMENTATION.md
├── README.md
├── LICENSE
├── pyproject.toml
├── CMakeLists.txt
├── cmake/
│   └── OpenRdkCOptions.cmake
├── include/
│   └── openrdkc/
│       ├── openrdkc.h
│       ├── errors.h
│       ├── protocol.h
│       ├── runtime.h
│       ├── device.h
│       ├── telemetry.h
│       └── version.h
├── src/
│   ├── core/
│   │   ├── runtime.c
│   │   ├── event_loop.c
│   │   ├── device.c
│   │   ├── registry.c
│   │   ├── command_queue.c
│   │   ├── telemetry_ring.c
│   │   ├── serial_linux.c
│   │   ├── udev_linux.c
│   │   ├── framing.c
│   │   ├── protocol.c
│   │   ├── logging.c
│   │   └── errors.c
│   └── python/
│       ├── module.c
│       ├── runtime_binding.c
│       ├── device_binding.c
│       ├── snapshot_binding.c
│       └── py_errors.c
├── python/
│   └── openrdkC/
│       ├── __init__.py
│       ├── runtime.py
│       ├── modules.py
│       ├── errors.py
│       ├── types.py
│       └── py.typed
├── tests/
│   ├── c/
│   │   ├── test_framing.c
│   │   ├── test_ring.c
│   │   ├── test_registry.c
│   │   └── test_state_machine.c
│   ├── python/
│   │   ├── test_imports.py
│   │   ├── test_runtime.py
│   │   ├── test_modules.py
│   │   ├── test_exceptions.py
│   │   └── test_python_parity.py
│   ├── fixtures/
│   │   └── protocol_frames/
│   └── hardware/
│       └── test_hardware_parity.py
├── benchmarks/
│   ├── bench_parser.c
│   ├── bench_runtime.py
│   └── compare_python_native.py
└── tools/
    ├── build_on_pi.sh
    ├── run_sanitizers.sh
    └── record_protocol_fixture.py
```

The build must never write generated files into `host/main`.

## 4. Public Python API

The initial API should deliberately mirror the standard runtime while keeping distinct class names:

```python
from openrdkC import CommsRuntimeC

runtime = CommsRuntimeC(
    auto_start=True,
    enable_webview=False,
)

devices = runtime.list_devices()
motor = runtime.traction("AA:BB:CC:DD:EE:01")
line = runtime.line_sensor("AA:BB:CC:DD:EE:02")
color = runtime.color_sensor("AA:BB:CC:DD:EE:03")
distance = runtime.distance_sensor("AA:BB:CC:DD:EE:04")

motor.move(35)
sample = line.get_data()
name = color.get_color()
distance_mm = distance.get_data()["distance_mm"]

runtime.stop()
```

Export at least:

```python
__all__ = [
    "CommsRuntimeC",
    "BaseModuleC",
    "TractionModuleC",
    "LineSensorModuleC",
    "ColorSensorModuleC",
    "DistanceSensorModuleC",
    "OpenRdkCError",
    "DeviceNotFoundErrorC",
    "DeviceOfflineErrorC",
    "ModuleTypeMismatchErrorC",
    "CommandFailedErrorC",
]
```

Do not import `openrdk` from `openrdkC` to implement communications. Test utilities may compare results, but production runtime ownership must remain independent.

## 5. Stable native C API

Define a small, versioned, opaque-handle API. Do not expose internal structures.

```c
typedef struct ordkc_runtime ordkc_runtime_t;
typedef struct ordkc_device ordkc_device_t;

typedef enum {
    ORDKC_OK = 0,
    ORDKC_ERR_INVALID_ARGUMENT,
    ORDKC_ERR_NOT_RUNNING,
    ORDKC_ERR_DEVICE_NOT_FOUND,
    ORDKC_ERR_DEVICE_OFFLINE,
    ORDKC_ERR_MODULE_MISMATCH,
    ORDKC_ERR_TIMEOUT,
    ORDKC_ERR_IO,
    ORDKC_ERR_PROTOCOL,
    ORDKC_ERR_BUSY,
    ORDKC_ERR_SHUTTING_DOWN,
    ORDKC_ERR_NO_MEMORY
} ordkc_result_t;

typedef struct {
    const char *registry_path;
    const char *log_path;
    uint32_t baud_rate;
    uint32_t command_queue_capacity;
    uint32_t telemetry_ring_capacity;
} ordkc_runtime_config_t;

ordkc_result_t ordkc_runtime_create(
    const ordkc_runtime_config_t *config,
    ordkc_runtime_t **out_runtime);

ordkc_result_t ordkc_runtime_start(ordkc_runtime_t *runtime);
ordkc_result_t ordkc_runtime_stop(ordkc_runtime_t *runtime);
ordkc_result_t ordkc_runtime_join(ordkc_runtime_t *runtime, uint32_t timeout_ms);
void ordkc_runtime_destroy(ordkc_runtime_t *runtime);
```

Command operations:

```c
ordkc_result_t ordkc_send_command(
    ordkc_runtime_t *runtime,
    const char *serial_number,
    uint8_t message_type,
    const uint8_t *payload,
    size_t payload_len,
    uint32_t timeout_ms,
    uint8_t *response,
    size_t response_capacity,
    size_t *out_response_len);
```

Telemetry operations must support sequence-based reads:

```c
ordkc_result_t ordkc_wait_sample(
    ordkc_runtime_t *runtime,
    const char *serial_number,
    uint64_t after_sequence,
    uint32_t timeout_ms,
    ordkc_sample_t *out_sample);

ordkc_result_t ordkc_latest_sample(
    ordkc_runtime_t *runtime,
    const char *serial_number,
    ordkc_sample_t *out_sample);
```

All public C functions must document thread safety, ownership, lifetime, timeout units, and whether output remains valid after the call.

## 6. Step-by-step implementation

### Step 1 — Freeze behavior and produce parity fixtures

1. Record the existing Python runtime’s public method inventory.
2. Record framed request/response fixtures for every firmware module.
3. Include valid, truncated, corrupted, duplicated, and out-of-sequence frames.
4. Record device disconnect/reconnect sequences.
5. Capture the exact dictionary structures returned by all Python module methods.
6. Create parity tests that can run against either `CommsRuntime` or `CommsRuntimeC`.
7. Do not begin optimization until these fixtures are checked in.

Acceptance condition: tests describe current behavior without modifying the standard host.

### Step 2 — Establish the independent build

1. Use CMake to build `libopenrdkc`.
2. Use `pyproject.toml` with `scikit-build-core` to build `_openrdkC`.
3. Set the distribution name to `openrdkC`.
4. Package the pure Python layer from `CHost/python/openrdkC`.
5. Confirm both imports work in one virtual environment:

   ```bash
   python -c "from openrdk import CommsRuntime"
   python -c "from openrdkC import CommsRuntimeC"
   ```

6. Build artifacts must go to `CHost/build` or an external build directory.

Acceptance condition: an empty native runtime imports without affecting `openrdk`.

### Step 3 — Implement framing as a standalone C library

Implement the protocol exactly as documented:

- Sync: `AA 55 AA 55`
- Payload length: `1..200`
- Message types: `CMD=0x01`, `TEST=0x02`, `TELEMETRY=0x03`, `CONTROL=0x04`
- Sequence: unsigned 24-bit big-endian with wraparound
- Control hello and module-query frames

Requirements:

1. The parser consumes arbitrary byte chunks.
2. Partial frames remain buffered.
3. Noise before sync is discarded safely.
4. Invalid lengths resynchronize without an unbounded scan.
5. The receive buffer is bounded.
6. Parsing performs no heap allocation in steady state.
7. Fuzz the parser with malformed input.

Acceptance condition: C parser outputs match every Python fixture and pass AddressSanitizer/UndefinedBehaviorSanitizer.

### Step 4 — Implement bounded native queues

Implement:

- Multi-producer/single-consumer command queue per device
- Bounded telemetry ring buffer per device
- Completion object using mutex + condition variable
- Superseding semantics for motor-output commands

Every queue must have an explicit overflow policy. Telemetry should drop the oldest frame and increment a counter. Commands must return `BUSY` rather than silently disappear.

Acceptance condition: queue tests pass under ThreadSanitizer without deadlocks or data races.

### Step 5 — Implement Linux serial and discovery

1. Enumerate eligible devices using `libudev`.
2. Open serial ports with `O_RDWR | O_NOCTTY | O_NONBLOCK | O_CLOEXEC`.
3. Configure baud and raw mode with `termios`.
4. Add each descriptor to one `epoll` instance.
5. Use `eventfd` to wake the loop for commands and shutdown.
6. Use `timerfd` or a monotonic deadline heap for keepalive, sync, and timeout scheduling.
7. Detect `HUP`, `ERR`, device removal, and short reads/writes.
8. Never retry forever inside one event-loop iteration.

Acceptance condition: repeated USB disconnect/reconnect does not leak descriptors, threads, or memory.

### Step 6 — Implement per-device state machines

Each device owns:

- Serial number and device node
- Module type and module ID
- Connection state
- Current message type
- TX and RX sequence counters
- Pending command
- Telemetry state and last timestamp
- Error counters
- Reconnect deadline
- Typed latest sample

Suggested states:

```text
DISCOVERED
OPENING
HANDSHAKE
QUERY_MODULE
ONLINE_CMD
ONLINE_CONTROL
ONLINE_TELEMETRY
RECONNECT_WAIT
REMOVED
STOPPING
```

State transitions must be explicit functions, not flags scattered through the event loop.

Acceptance condition: recorded lifecycle fixtures produce deterministic state transitions.

### Step 7 — Replace the hot JSON registry with memory

The runtime registry must live in native memory. Disk persistence is not synchronization.

1. Load persistent configuration once during startup.
2. Keep link status, telemetry state, sequence, and timestamps only in memory.
3. Mark persistent fields dirty when names or settings change.
4. Snapshot persistent data on a slow interval or explicit save.
5. Write atomically using temporary file, `fsync`, and `rename`.
6. Never read and parse the registry JSON in the telemetry loop.
7. Expose an immutable device snapshot to Python.

Use a separate native-host registry path by default, for example:

```text
~/.local/state/openrdkC/devices.json
```

Do not reuse the live standard-host registry while both packages are installed.

Acceptance condition: zero filesystem access occurs in the steady-state frame path.

### Step 8 — Add typed telemetry decoders

Create typed C structures for:

- Traction telemetry
- Line sensor `LS` data
- Color `DATA/TEL`, `CFG`, `INFO`, `CAL`, and `PATCH`
- Distance sensor `DS` data

Preserve:

- Raw frame text for diagnostics
- Native monotonic receive timestamp
- Firmware timestamp
- Absolute native sample sequence
- Health flags

Parsing failures must leave the previous valid sample intact and increment a diagnostic counter.

Acceptance condition: native-to-Python dictionaries exactly match parity fixtures.

### Step 9 — Build the CPython translation layer

The extension module should be named `_openrdkC`; users import `openrdkC`.

Rules:

1. Release the GIL around blocking native calls.
2. Reacquire it only to create Python results or exceptions.
3. Never invoke Python callbacks directly from the event-loop thread.
4. Return complete snapshots rather than crossing the boundary per byte or field.
5. Convert native error codes into `openrdkC.errors`.
6. Use capsules or extension types to own runtime handles.
7. `close()`, context-manager exit, and object deallocation must be idempotent.
8. Register interpreter-shutdown handling, but require explicit `stop()` in documentation.

Example:

```c
Py_BEGIN_ALLOW_THREADS
result = ordkc_wait_sample(runtime, serial, after_seq, timeout_ms, &sample);
Py_END_ALLOW_THREADS
```

Acceptance condition: Python threads and FastAPI remain responsive while native waits are active.

### Step 10 — Implement `CommsRuntimeC`

Implement lifecycle first:

```python
class CommsRuntimeC:
    def __init__(self, *, auto_start=True, registry_path=None, log_path=None):
        ...

    def start(self) -> "CommsRuntimeC":
        ...

    def stop(self, timeout_sec=2.0) -> None:
        ...

    def __enter__(self):
        return self.start()

    def __exit__(self, exc_type, exc, tb):
        self.stop()
```

Then implement:

- `list_devices()`
- `get_device(serial)`
- `require_device(serial)`
- `wait_online(serial)`
- `module(serial)`
- `traction(serial)`
- `line_sensor(serial)`
- `color_sensor(serial)`
- `distance_sensor(serial)`

Do not start or import the standard runtime internally.

Acceptance condition: basic SDK programs can switch only their import and runtime class name.

### Step 11 — Implement module wrappers

Build wrappers in this order:

1. `BaseModuleC`
2. `LineSensorModuleC`
3. `DistanceSensorModuleC`
4. `ColorSensorModuleC`
5. `TractionModuleC`

Traction comes last because command superseding, CONTROL mode, PID operations, and safe stop behavior carry the highest risk.

Return Python dictionaries for compatibility, but optionally expose immutable typed snapshot objects for high-rate loops:

```python
sample = sensor.latest_sample_native()
position = sample.position
values = sample.values
data = sample.to_dict()
```

Acceptance condition: module-level parity tests pass against real devices.

### Step 12 — Add process-level serial ownership

Prevent two runtimes from controlling one port:

1. Use `flock()` on a per-device lock file or the serial descriptor.
2. Include PID and runtime name in diagnostic lock metadata.
3. Return a clear `DEVICE_BUSY` error.
4. Never steal a port from the standard runtime.

The user chooses one runtime per hardware session:

```python
# Standard
from openrdk import CommsRuntime

# Or native
from openrdkC import CommsRuntimeC
```

Acceptance condition: starting both runtimes cannot cause multiple access to a serial port.

### Step 13 — Add an optional native webview adapter

Do not copy or modify the standard webview initially.

After SDK parity:

1. Add `openrdkC.webview` as an optional adapter.
2. Keep FastAPI in Python.
3. Read native device snapshots and send native commands through `CommsRuntimeC`.
4. Bind every endpoint to an explicit serial number.
5. Do not poll devices merely for display telemetry.
6. Ensure configuration commands affect only the requested serial.

Acceptance condition: native webview tests verify strict per-device isolation.

### Step 14 — Test failure and shutdown behavior

Test at minimum:

- Stop before start
- Repeated start/stop
- Python exception during a command
- Interpreter shutdown with an active runtime
- USB removal during read and write
- Partial frame followed by disconnect
- Sequence wraparound
- Queue overflow
- Command timeout
- Telemetry timeout
- Device reappears under a different `/dev/ttyACM*`
- Two devices of every supported module type
- Standard and native runtimes competing for one device

Acceptance condition: sanitizer runs and 24-hour soak test pass.

### Step 15 — Benchmark before declaring success

Measure both hosts with identical firmware and workloads:

- 1, 2, 4, 8, and 16 devices
- CMD, CONTROL, and TELEMETRY modes
- Webview disabled and enabled
- CPU utilization
- Resident memory
- Median, p95, p99, and maximum command latency
- Telemetry sample age
- Dropped frames
- Reconnect time
- Registry disk writes

Do not use parser microbenchmarks as the primary success metric.

Suggested acceptance targets:

- No behavioral regressions
- At least 50% lower transport CPU with four active telemetry devices
- p99 host dispatch jitter below 2 ms under the target workload
- No steady-state registry reads or writes
- No unbounded queues or allocations
- No device cross-control

## 7. Packaging

Suggested `pyproject.toml` direction:

```toml
[build-system]
requires = ["scikit-build-core>=0.10"]
build-backend = "scikit_build_core.build"

[project]
name = "openrdkC"
version = "0.1.0"
requires-python = ">=3.11"

[tool.scikit-build]
cmake.source-dir = "."
wheel.packages = ["python/openrdkC"]
```

Build on the Pi first:

```bash
cd /home/openrdk/Open-RDK/CHost
python3 -m venv .venv
. .venv/bin/activate
python -m pip install --upgrade pip build
python -m build
python -m pip install dist/openrdkc-*.whl
python -c "from openrdkC import CommsRuntimeC; print(CommsRuntimeC)"
```

Later, produce wheels for each supported Python and ARM architecture combination. Do not copy `.so` files manually into `host/main/src/openrdk`.

## 8. Development phases

### Phase A — Foundation

- Independent package imports
- C framing library
- Sanitizer and fixture tests
- No hardware control

### Phase B — Read-only hardware runtime

- udev discovery
- serial event loop
- handshake/module query
- telemetry receive
- in-memory registry

### Phase C — Commands and module parity

- CMD and CONTROL queues
- module wrappers
- calibration/configuration
- reconnection and safe shutdown

### Phase D — Product integration

- optional native webview adapter
- packaging and ARM wheels
- parity and performance reports
- long-duration hardware tests

The standard Python host remains production-default until Phase D passes all parity, isolation, and soak tests.

## 9. Definition of done

`CHost` is complete only when:

1. `from openrdk import CommsRuntime` remains unchanged.
2. `from openrdkC import CommsRuntimeC` works independently.
3. Both packages can be installed together.
4. Both implementations pass the shared behavioral suite.
5. The native runtime never performs steady-state JSON registry polling.
6. The native runtime uses bounded queues and buffers.
7. Blocking native calls release the GIL.
8. Every module operation is scoped to an explicit serial.
9. Competing runtime ownership produces a safe error.
10. Sanitizers and the 24-hour soak test pass.
11. Benchmarks show a meaningful system-level improvement.
12. No files under `host/main/src/openrdk` were required to change.

