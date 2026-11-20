"""Diagnostics returned by the generation service."""


class GenerationError(ValueError):
    def __init__(self, code, message, path=None, **details):
        super().__init__(message)
        self.diagnostic = {"code": code, "message": message}
        if path is not None:
            self.diagnostic["path"] = str(path)
        if details:
            self.diagnostic["details"] = details
