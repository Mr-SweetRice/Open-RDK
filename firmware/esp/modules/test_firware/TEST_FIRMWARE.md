# Test Firware Documentation

This document is the required root-level documentation for all additions in `test_firware`.
From this point onward, every firmware addition/update must be recorded here.

## Mandatory Standards (obligatory instructions)

1. Do not alter existing function of communication unless solicited, if needed to do something solicited then ask.
2. Every add to the firmware must be documented in a markdown in the root of the firmware which include obligatory this very instruction.
3. Every firmware must keep the same general structure of directory, with a main dir with the code inside and a CMAKE list in the root, and any dependency in a componente directory.
4. Every new communication add to either the protocol or new communication function or call must be explicitly added to the markdown doc explaining what it does what the host expect to send and receive and what the module firmware expect to send and receive with that communication, function or call.
5. Every module firmware including test must be able to respond to the host test webview.

## Current Baseline

- Module name: `test_firware`
- Target: ESP32-C3
- Current structure:
  - `CMakeLists.txt`
  - `main/CMakeLists.txt`
  - `main/main.c`
  - `components/` (reserved for dependencies)

## Change Log

### 2026-04-11
- Created placeholder `test_firware` structure with required baseline files.
- Added this root markdown with mandatory standards and baseline structure.
