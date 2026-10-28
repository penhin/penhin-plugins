"""Stable errors; never expose database paths or indexed text in tool errors."""


class KnowledgeError(Exception):
    def __init__(self, code: str) -> None:
        self.code = code
        super().__init__(code)
