class RelayError(Exception):
    """Base error for openrdk SDK runtime and modules."""


class RuntimeNotStartedError(RelayError):
    """Raised when an operation requires a running runtime loop."""


class DeviceNotFoundError(RelayError):
    """Raised when the target serial number is missing from registry."""


class DeviceOfflineError(RelayError):
    """Raised when the target device exists but is currently offline."""


class UnsupportedModuleTypeError(RelayError):
    """Raised when no sanitized class exists for a discovered module type."""


class ModuleTypeMismatchError(RelayError):
    """Raised when creating a module class for a different firmware type."""


class CommandFailedError(RelayError):
    """Raised when firmware returns non-success for an SDK command."""

