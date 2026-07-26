# Step 1 Status: Behavior and Fixture Freeze

Step 1 creates contracts and test material only. It contains no native runtime,
does not import `openrdk` in production, does not open serial ports, and does
not alter firmware or the standard host.

Delivered:

- `spec/PUBLIC_API.md`: Pi-side public SDK inventory.
- `spec/public_api_inventory.json`: machine-readable AST snapshot generated
  from the Pi-side standard host.
- `spec/RESULT_CONTRACTS.md`: Python result and failure contracts.
- `tests/fixtures/protocol_frames/v1.json`: valid and malformed frame cases for
  all four current module types.
- `tests/fixtures/lifecycle/v1.json`: connection, timeout, reconnect, shutdown,
  and ownership sequences.
- `tests/python/test_step1_contracts.py`: fixture validation.
- `tools/capture_standard_api.py`: read-only AST inventory helper.

Run validation:

```bash
cd /home/openrdk/Open-RDK/CHost
python3 -m unittest discover -s tests/python -v
python3 tools/capture_standard_api.py \
  --repo /home/openrdk/Open-RDK \
  --output /tmp/openrdk-standard-api.json
```

The `/tmp` output is diagnostic and is not used by the standard runtime.

Step 1 exit requirements:

- Fixtures validate.
- Every current module type has representative frames.
- Malformed-input and lifecycle fixtures exist.
- API inventory is reproducible without importing the standard runtime.
- `git status` shows no new firmware or standard-host modifications caused by
  Step 1.
