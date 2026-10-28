"""Stable public error codes; never retain decoder or backend diagnostics."""

CODES = frozenset(
    {
        "attachment_unavailable",
        "attachment_mismatch",
        "invalid_input",
        "unsupported_media",
        "invalid_media",
        "empty_media",
        "media_limit_exceeded",
        "decoder_not_installed",
        "worker_failed",
        "worker_start_failed",
        "invalid_backend_result",
        "backend_failed",
        "backend_not_installed",
        "backend_unavailable",
        "model_unavailable",
        "model_mismatch",
        "output_limit_exceeded",
        "project_not_enabled",
        "enablement_failed",
        "media_timeout",
        "media_busy",
        "media_closed",
    }
)


class MediaError(Exception):
    def __init__(self, code: str) -> None:
        self.code = code if code in CODES else "backend_failed"
        super().__init__(self.code)
