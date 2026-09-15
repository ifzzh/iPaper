import re
import unicodedata
from ipaper.metadata.model import encoded, fingerprint, stamp


class KeywordError(ValueError):
    def __init__(self, code, status=400):
        self.code, self.status = code, status
        super().__init__(code)


def label(value, maximum=64):
    if not isinstance(value, str):
        raise KeywordError("invalid_tag_name")
    value = re.sub(r"\s+", " ", unicodedata.normalize("NFKC", value)).strip()
    if not 1 <= len(value) <= maximum or any(
        unicodedata.category(c).startswith("C") for c in value
    ):
        raise KeywordError("invalid_tag_name")
    return value


def name_key(value):
    # C++, C# and C remain different names. Synonyms require an explicit mapping.
    return label(value).casefold()


TERMINAL = {
    "completed",
    "reused",
    "needs_content",
    "failed",
    "cancelled",
    "interrupted",
    "stale",
    "deleted",
}
METHOD_VERSION = "phrases-1"
PROMPT_VERSION = "keyword-enhance-1"
