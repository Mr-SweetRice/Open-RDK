# Step 2 Status: Independent Native Build

Step 2 establishes an independent native package. It does not discover devices,
open serial ports, send commands, import the standard runtime in production, or
change firmware.

Delivered:

- `CMakeLists.txt`: builds `openrdkc_core` and `_openrdkC`.
- `pyproject.toml`: independent `scikit-build-core` wheel configuration.
- `include/openrdkc/`: versioned public C lifecycle API.
- `src/core/runtime.c`: allocation and idempotent lifecycle scaffold.
- `src/python/module.c`: CPython translation layer.
- `python/openrdkC/`: public `CommsRuntimeC` package.
- `tests/python/test_step2_imports.py`: import, lifecycle, context-manager,
  isolation, and explicit-not-implemented tests.

Built Pi artifact:

```text
dist/openrdkc-0.1.0-cp313-cp313-linux_aarch64.whl
```

Verified imports:

```python
from openrdk import CommsRuntime
from openrdkC import CommsRuntimeC
```

The two packages coexist in `CHost/.venv-coexist`. The standard host is supplied
read-only through `PYTHONPATH`; the native wheel is installed into that isolated
environment.

Run:

```bash
cd /home/openrdk/Open-RDK/CHost
PYTHONDONTWRITEBYTECODE=1 .venv/bin/python \
  -m unittest discover -s tests/python -v
```

Current boundary:

- `CommsRuntimeC.start`, `stop`, `ensure_running`, `is_running`, context-manager
  behavior, and `native_version` are implemented.
- `list_devices` deliberately raises `NotImplementedError`.
- No device-facing functionality is allowed before the later discovery and
  serial-runtime steps.

