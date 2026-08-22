# Firmware and Configuration Page Versioning

## Purpose

Every versioned firmware module must identify both its own firmware version and the exact configuration page version that can safely operate it. Firmware and configuration pages are a compatibility pair; neither side may assume that the newest available page is compatible with every firmware release.

The traction module is the temporary exception to these rules. It is the last module that has not yet been migrated to this versioning system. Until that migration is completed, traction firmware and its configuration tools may continue using their existing behavior.

## Versioned modules

These rules currently apply to:

- `color_module`
- `line_sensor_module`
- `distance_sensor_module`

The current versions are:

| Module | Firmware version | Page ID | Page version |
| --- | --- | --- | --- |
| Color module | `1.1` | `color-studio` | `1.1` |
| Line sensor module | `1.0` | `line-sensor` | `1.0` |
| Distance sensor module | `1.0` | `distance-sensor` | `1.0` |

## Required handshake identity

Every versioned module must return its identity during the module-query handshake in this format:

```text
<module_type>|<firmware_version>|<expected_page_id>|<expected_page_version>
```

Examples:

```text
color_module|1.1|color-studio|1.1
line_sensor_module|1.0|line-sensor|1.0
distance_sensor_module|1.0|distance-sensor|1.0
```

The same values must also be exposed by the module's information command, such as `GET INFO`, when that command is supported. The handshake and information response must never disagree.

## Firmware version rules

1. Any change to firmware functionality must increment or append the firmware version.
2. Functional changes include protocol changes, commands, configuration fields, telemetry fields, sensor behavior, calculations, calibration behavior, persistence formats, hardware behavior, and any bug fix that changes externally observable behavior.
3. A released firmware version must not be silently replaced with different behavior while retaining the same version identifier.
4. Source directories retained as historical or legacy copies must keep names that clearly identify the snapshot, even when the firmware's runtime identity follows the release identity required by the host.
5. Version strings must be updated consistently in every firmware location that reports identity, including handshake constants, `GET INFO`, `GET VERSION`, documentation, and packaged metadata.

## Configuration page version rules

1. Every module configuration page must declare a page ID and page version in its HTML metadata and body data attributes.
2. A page version identifies its supported firmware interface. If a firmware change makes the existing page incompatible or requires different behavior, a new page version must be created.
3. Firmware must explicitly name the useful, compatible page ID and page version in its handshake. It must not merely request the newest page.
4. More than one firmware version may point to one page version only when that page is fully compatible with all of them.
5. Older page versions required by supported legacy firmware must remain registered and available.
6. Existing historical pages must not be modified in ways that break the firmware versions that point to them. A new incompatible implementation requires a new page version.

## Host page-selection rules

1. The host must read the expected page ID and expected page version reported by the connected firmware.
2. The host must open the exact registered page matching that identity.
3. Outdated firmware is allowed to open its corresponding legacy page when that page is registered.
4. The host must not block a module merely because its firmware is older than the current release.
5. If the requested page ID or version is unavailable, unknown, or incompatible, the host must block access instead of silently opening a different page.
6. A manually requested page version must be validated using the same registry. Unknown versions must be rejected.
7. The page must independently compare its own page ID and version with the values reported by the selected module and show a compatibility warning or prevent unsafe operations if they do not match.

## Current and outdated status

1. The host must maintain the current firmware version for every versioned module type.
2. A module is `UpToDate` only when its reported firmware version exactly matches the current version registered by the host.
3. A missing, unknown, or different firmware version is `Outdated`.
4. Both current and outdated status must be visible in the main device list and, where practical, on the module configuration page.
5. `Outdated` is informational and does not itself prevent access. Access is blocked only when no compatible registered page exists or when the page identity does not match the firmware requirement.

## Adding a firmware release

For every functional firmware release:

1. Choose and record the new firmware version.
2. Determine whether an existing page remains fully compatible.
3. If necessary, create a new page version while retaining pages needed by legacy firmware.
4. Update the firmware handshake with the new firmware version and exact compatible page identity.
5. Update all firmware information/version commands with the same values.
6. Register the compatible page in the host page-version registry.
7. Update the host's current-version table so status flags are correct.
8. Update page metadata and compatibility checks.
9. Update documentation, mocks, parsers, and automated tests.
10. Verify that current firmware opens its current page, supported old firmware opens its legacy page, and an unknown page version is blocked.

## Traction module exception

The traction module is temporarily excluded because it is the last module without the complete firmware-to-page versioning system described here. Existing traction behavior must be preserved until it is deliberately migrated.

When traction is migrated, it must:

- report a firmware version and exact compatible page identity in its handshake;
- expose the same identity through its information response;
- register every supported traction configuration page version;
- use exact-match page routing with blocking for missing versions;
- display `UpToDate` or `Outdated` status using the same rules as the other modules; and
- be removed from this exception section.
