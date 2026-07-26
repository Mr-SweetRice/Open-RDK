# openrdkC

`openrdkC` is an optional native host for Open-RDK.

The standard Python implementation remains unchanged and remains the default:

```python
from openrdk import CommsRuntime
```

The isolated native implementation uses:

```python
from openrdkC import CommsRuntimeC
```

Step 2 supplies only the independent build, native library, CPython extension,
and lifecycle scaffold. It does not discover devices, open serial ports, send
commands, import the standard runtime, or modify firmware.

## First-time development installation on Debian

Install the native compiler, ASan/UBSan toolchain, Python build tools, and the
isolated `CHost/.venv` with:

```bash
cd /home/openrdk/Open-RDK/CHost
tools/install_build_requirements_debian.sh
```

The system package list is maintained in `requirements-system-debian.txt`.
Python build packages are maintained in `requirements-build.txt`.

ASan is a mandatory source-validation dependency, not an `openrdkC` runtime
dependency. A built wheel does not need a compiler or sanitizer installed on
the robot that runs it.

## Build on the Pi

```bash
cd /home/openrdk/Open-RDK/CHost
.venv/bin/python -m build --wheel --no-isolation
.venv/bin/python -m pip install dist/openrdkc-*.whl
.venv/bin/python -c "from openrdkC import CommsRuntimeC; print(CommsRuntimeC)"
```

Run the mandatory UBSan and ASan validation with:

```bash
PATH="$PWD/.venv/bin:$PATH" tools/run_sanitizers.sh
```

The current Raspberry Pi 4 kernel uses a 39-bit virtual-address layout that
cannot initialize either GCC's or Clang's ASan allocator. The script reports
this as a failed mandatory validation rather than silently skipping ASan. Run
the same command on a compatible CI/Linux runner before accepting native C
changes.

Lifecycle scaffold:

```python
from openrdkC import CommsRuntimeC

with CommsRuntimeC() as runtime:
    assert runtime.is_running
```

Any device-facing method is intentionally deferred to later implementation
steps and must not silently delegate to `openrdk`.
