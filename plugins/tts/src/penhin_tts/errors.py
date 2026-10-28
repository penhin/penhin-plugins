"""Stable errors which do not disclose text, audio or local paths."""


class TtsError(Exception):
    def __init__(self, code: str):
        self.code = code
        super().__init__(code)


class InvalidText(TtsError):
    pass


class BackendError(TtsError):
    pass


class TtsBusy(TtsError):
    def __init__(self):
        super().__init__("tts_busy")


class TtsClosed(TtsError):
    def __init__(self):
        super().__init__("tts_closed")


class TtsTimeout(TtsError):
    def __init__(self):
        super().__init__("tts_timeout")
