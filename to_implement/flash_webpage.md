# Controlled Color Module Web Flasher

## Objective

Add a firmware-update workflow to the OpenRDK host webpage using approved,
prebuilt ESP32-C3 binaries. The first implementation supports only
`color_module` devices with valid firmware version reporting.

The host must never compile firmware. The browser asks the existing Python host
to perform the update, and the host uses the ESP32-C3 ROM bootloader protocol
through `esptool`.

## User-interface rule

The **Update** button is displayed only when all of the following are true:

1. A device is selected.
2. Its module type is exactly `color_module`.
3. It reports a recognized firmware version.
4. Its installed version differs from the current approved Color Module
   firmware version.
5. A compatible prebuilt firmware package is available on the host.

An up-to-date Color Module does not show the button. Other module types do not
show the button. Unknown or malformed device identities must not be flashable.

The red **Outdated** tag remains visible when an update is required. The update
button should appear beside that status or in the selected device's tools area.

## Source of truth for current firmware

Do not duplicate the current version independently in several JavaScript and
Python files. Add a host-side firmware catalog and return its status as part of
the device API response.

For example:

```json
{
  "module_type": "color_module",
  "installed_version": "legacy_1.0",
  "current_version": "1.1",
  "update_available": true,
  "update_supported": true
}
```

The webpage must trust this host-side result rather than compare versions by
itself.

## Prebuilt firmware package

Store only reviewed release artifacts in a host-controlled directory:

```text
prebuilt_firmware/
  color_module/
    1.1/
      manifest.json
      color_module.bin
      bootloader.bin
      partition-table.bin
```

The manifest should contain:

```json
{
  "module_type": "color_module",
  "firmware_version": "1.1",
  "expected_page": "color-studio",
  "expected_page_version": "1.1",
  "chip": "esp32c3",
  "flash_size": "2MB",
  "preserve_nvs": true,
  "images": [
    {
      "file": "color_module.bin",
      "offset": "0x10000",
      "sha256": "REQUIRED_SHA256"
    }
  ]
}
```

The normal update package should contain only the application image at
`0x10000`. Include the bootloader or partition table only when that release
actually changes them and explicitly lists their offsets and checksums.

Never include NVS, calibration data, an entire flash dump, eFuse operations,
secure-boot changes, flash-encryption changes, or a full-chip erase command.

## Runtime and platform detection

At host startup, detect and record:

- Operating system using `platform.system()`.
- Machine architecture using `platform.machine()`.
- Whether the host is Windows.
- Whether the host is Linux on Raspberry Pi. Confirm Raspberry Pi using
  `/proc/device-tree/model` when available; do not infer Pi solely from ARM.
- The Python executable used by the OpenRDK host.

Supported initial targets:

- Windows x86-64 running the OpenRDK Python host.
- Raspberry Pi OS on supported ARM architectures.

An unsupported operating system or architecture must disable flashing and
return a clear warning. Device monitoring and configuration must continue to
work normally.

## Finding `esptool`

Probe in this order:

1. Import `esptool` in the same Python environment running OpenRDK.
2. If the import succeeds, record its version and use that Python module.
3. Optionally recognize a configured standalone `esptool` executable, but do
   not silently select an arbitrary executable from an untrusted directory.
4. Run a harmless version check and require a supported `esptool` version.

The host must not install packages automatically from the webpage. If no valid
installation is found, flashing is disabled and the API returns platform-aware
instructions.

Windows guidance:

```powershell
<openrdk-python> -m pip install esptool
```

Raspberry Pi guidance:

```bash
<openrdk-python> -m pip install esptool
```

If OpenRDK uses a virtual environment, the warning must show that environment's
exact Python executable. Avoid recommending `sudo pip`. The preferred deployed
solution is to declare and install a pinned `esptool` runtime dependency as
part of the normal OpenRDK installation for each platform.

Example capability response:

```json
{
  "supported": false,
  "platform": "Windows x86_64",
  "esptool_found": false,
  "warning": "Firmware update is unavailable because esptool is not installed in C:\\path\\to\\python.exe."
}
```

## Host API

Add endpoints similar to:

- `GET /api/firmware/capabilities`
- `GET /api/devices/{serial}/firmware/status`
- `POST /api/devices/{serial}/firmware/update`
- `GET /api/firmware/jobs/{job_id}` or a WebSocket event for progress

The update request must identify only the selected device. The browser must not
supply an arbitrary binary path, chip name, flash offset, or command line.

The host resolves the approved package from its catalog and creates one update
job. Only one flash job may run at a time initially.

## Validation before releasing the port

Before starting an update, verify:

1. The selected registry entry still exists.
2. It is currently connected.
3. Its module type is `color_module`.
4. Its serial identity/MAC matches the user's selection.
5. It is outdated relative to the approved catalog.
6. The package manifest is valid.
7. Every binary exists within the approved firmware directory.
8. Every binary SHA-256 matches the manifest.
9. All offsets are from an allowlist.
10. NVS and calibration partitions are not targeted.
11. No telemetry or calibration operation is active.
12. `esptool` and the current platform are supported.

If any check fails, do not release the port and do not start flashing.

## Serial-port ownership and flashing sequence

Use a host-level exclusive flash lock and the existing per-device communication
coordination. The sequence is:

1. Ask the user to confirm the selected module name, MAC, installed version,
   and target version.
2. Stop telemetry and reject new commands for that device.
3. Mark the device as `updating` so normal polling does not reconnect.
4. Close and fully release the COM/TTY handle.
5. Start `esptool` using an argument list, never a shell-built command string.
6. Connect to the ROM bootloader using the selected device node.
7. Confirm that the detected chip is ESP32-C3.
8. Read and compare the hardware MAC with the selected registry MAC.
9. Flash only the images and offsets listed in the validated manifest.
10. Require write verification.
11. Hard-reset the module.
12. Release the flash lock.
13. Allow normal discovery to reconnect.
14. Wait for the module handshake.
15. Verify module type `color_module`, the same MAC, firmware version `1.1`,
    expected page `color-studio`, and page version `1.1`.

If the chip or MAC check fails, abort before writing anything.

## Progress and user feedback

Show these states inside the main bubble:

- Preparing
- Releasing device
- Entering bootloader
- Verifying ESP32-C3 and MAC
- Erasing application region
- Writing firmware with percentage
- Verifying write
- Resetting
- Waiting for reconnect
- Verifying installed version
- Update complete

Disable navigation and repeat update clicks while the selected device has an
active flash job. Do not claim success merely because `esptool` exited with
code zero; success requires the post-flash OpenRDK handshake and version check.

Errors must remain visible and include recovery guidance without exposing a
dangerous editable command line.

## Interrupted-update recovery

An interrupted application write normally leaves the module temporarily unable
to boot, but the ESP32-C3 ROM bootloader remains available. Preserve the failed
job record and display:

1. Keep the module connected.
2. Click **Retry Update**.
3. If automatic bootloader entry fails, hold BOOT, press and release RESET, then
   release BOOT and retry.

The retry workflow must be able to select the same physical COM/TTY device even
when the normal firmware handshake is unavailable. Recovery selection must
still verify the ESP32-C3 chip and MAC through `esptool` before writing.

Never label an interrupted device permanently bricked unless ROM bootloader
access has also been tested and failed.

## Windows considerations

- Accept only a concrete `COM` device associated with the selected registry
  entry.
- Handle COM ports numbered above 9 through the serial library correctly.
- Stop all OpenRDK readers before invoking `esptool` to prevent access-denied
  and port-in-use errors.
- Do not terminate unrelated Python or serial processes.
- Report another process owning the port as a recoverable failure.

## Raspberry Pi considerations

- Resolve and validate the concrete `/dev/ttyACM*` or `/dev/ttyUSB*` device.
- Prefer stable `/dev/serial/by-id/` paths when available.
- Verify that the OpenRDK service user belongs to the appropriate serial-access
  group, commonly `dialout`.
- Do not run the web server or flasher as root merely to obtain serial access.
- Account for udev renaming after the bootloader reset and match the device by
  stable identity/MAC rather than assuming the old path remains unchanged.

## Security boundaries

- No firmware upload control in the first version.
- No user-entered paths, offsets, chips, or `esptool` arguments.
- No shell execution.
- Resolve files and ensure they stay inside the approved firmware directory.
- Validate manifest schema and hashes before every update.
- Permit only `color_module` and `esp32c3`.
- Permit only allowlisted flash offsets.
- Never erase the full chip.
- Never write eFuses.
- Never overwrite NVS/calibration partitions.
- Record the initiating device, source and target versions, MAC, package hash,
  start/end time, outcome, and error.

## Implementation phases

### Phase 1: firmware catalog

- Create the approved package directory and manifest schema.
- Copy the reviewed Color Module 1.1 application binary.
- Generate and record SHA-256.
- Add catalog loading and validation tests.
- Make the catalog the shared source for current-version status.

### Phase 2: capability detection

- Implement Windows/Raspberry Pi platform detection.
- Probe the host Python environment for a supported `esptool`.
- Add the capability endpoint and clear installation warnings.
- Ensure missing `esptool` never affects normal OpenRDK operation.

### Phase 3: update status UI

- Add host-computed `current_version`, `update_available`, and
  `update_supported` fields.
- Show **Update** only for outdated Color Modules with a valid package.
- Add confirmation showing MAC and both versions.

### Phase 4: flash coordinator

- Implement exclusive flash jobs and device communication suspension.
- Validate chip and MAC before writing.
- Flash the application image without full erase.
- Capture structured progress and errors.
- Always restore normal device discovery in a `finally` path.

### Phase 5: reconnect verification and recovery

- Verify the post-flash handshake and reported version.
- Add retry support for devices whose application no longer boots.
- Provide BOOT/RESET recovery instructions.
- Test cable removal during preparation, erase, write, verify, reset, and
  reconnect stages.

## Acceptance criteria

- A current Color Module never shows **Update**.
- An outdated Color Module shows **Update** only when the host has a validated
  compatible package and `esptool` runtime.
- Non-color modules cannot call the update endpoint successfully.
- Missing or unsupported `esptool` disables flashing with a Windows- or
  Raspberry Pi-specific warning.
- The chip and MAC are verified before any write.
- Configuration and calibration remain unchanged after updating.
- The host cannot access the serial port concurrently with `esptool`.
- Progress is visible and repeat clicks cannot start parallel flashes.
- Interrupted flashing can be retried through the ROM bootloader.
- Success requires reconnecting as the same Color Module with firmware 1.1.
- Direct configuration pages and embedded Color Studio continue working.
