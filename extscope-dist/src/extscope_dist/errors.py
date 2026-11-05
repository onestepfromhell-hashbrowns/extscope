class DistError(Exception):
    """Any refusal. Carries a stable code so callers can branch without parsing text."""

    def __init__(self, code: str, detail: str = ""):
        super().__init__(f"{code}: {detail}" if detail else code)
        self.code = code
        self.detail = detail
