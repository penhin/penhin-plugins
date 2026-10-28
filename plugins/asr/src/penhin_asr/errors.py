"""Stable errors that never contain audio, transcripts, or host paths."""


class AsrError(Exception):
    """A safe code for host diagnostics; input contents are deliberately omitted."""

    def __init__(self, code: str) -> None:
        self.code = code
        super().__init__(code)


class InvalidAudio(AsrError):
    pass


class AsrBusy(AsrError):
    def __init__(self) -> None:
        super().__init__("asr_busy")


class AsrClosed(AsrError):
    def __init__(self) -> None:
        super().__init__("asr_closed")


class AsrTimeout(AsrError):
    def __init__(self) -> None:
        super().__init__("asr_timeout")


class BackendError(AsrError):
    pass
