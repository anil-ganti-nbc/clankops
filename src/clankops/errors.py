"""Domain and storage errors."""


class ClankOpsError(Exception):
    """Base error for ClankOps."""


class NotFoundError(ClankOpsError):
    pass


class DuplicateError(ClankOpsError):
    pass


class InvalidTransitionError(ClankOpsError):
    pass


class AppendOnlyViolation(ClankOpsError):
    pass


class ValidationError(ClankOpsError):
    pass
