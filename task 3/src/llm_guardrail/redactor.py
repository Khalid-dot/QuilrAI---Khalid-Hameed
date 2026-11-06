"""Two bounded streaming transducers. Never release an unresolved PII candidate."""

import re
import string

REDACTED = "[REDACTED]"
EMAIL_CHARS = frozenset(string.ascii_letters + string.digits + ".!#$%&'*+/=?^_`{|}~@-")
EMAIL = re.compile(
    r"[A-Za-z0-9.!#$%&'*+/=?^_`{|}~-]+@"
    r"[A-Za-z0-9](?:[A-Za-z0-9-]*[A-Za-z0-9])?"
    r"(?:\.[A-Za-z0-9](?:[A-Za-z0-9-]*[A-Za-z0-9])?)+"
)
SSN = re.compile(r"(?<![0-9])[0-9]{3}-[0-9]{2}-[0-9]{4}(?![0-9])")
CARD = re.compile(r"(?<![0-9])(?:[0-9][ -]?){12,18}[0-9](?![0-9])")
DIGITS = frozenset(string.digits)


class StreamingRedactor:
    """Buffers email atoms and numeric runs, never the complete response.

    Oversized candidates are replaced once, then discarded through their boundary.
    This deliberately favors over-redaction over releasing an ambiguous prefix.
    """

    EMAIL_LIMIT = 320
    NUMBER_LIMIT = 128

    def __init__(self):
        self._email = ""
        self._number = ""
        self._drop_email = False
        self._drop_number = False
        self._finished = False
        self.peak_pending_chars = 0

    @property
    def pending_chars(self) -> int:
        return len(self._email) + len(self._number)

    def _numeric(self, text: str) -> str:
        output = []
        for char in text:
            numeric_char = char in DIGITS or char in " -"
            if self._drop_number:
                if numeric_char:
                    continue
                self._drop_number = False
            if self._number:
                if numeric_char:
                    if len(self._number) == self.NUMBER_LIMIT:
                        output.append(REDACTED)
                        self._number = ""
                        self._drop_number = True
                    else:
                        self._number += char
                    continue
                output.append(self._clean_number())
            if char in DIGITS:
                self._number = char
            else:
                output.append(char)
        return "".join(output)

    def _clean_number(self) -> str:
        result = CARD.sub(REDACTED, SSN.sub(REDACTED, self._number))
        self._number = ""
        return result

    def feed(self, text: str) -> str:
        if self._finished:
            raise RuntimeError("Redactor has already finished")
        output = []
        for char in text:
            if self._drop_email:
                if char in EMAIL_CHARS:
                    continue
                self._drop_email = False
            if char in EMAIL_CHARS:
                if len(self._email) == self.EMAIL_LIMIT:
                    output.append(self._numeric(REDACTED))
                    self._email = ""
                    self._drop_email = True
                else:
                    self._email += char
            else:
                if self._email:
                    output.append(self._numeric(EMAIL.sub(REDACTED, self._email)))
                    self._email = ""
                output.append(self._numeric(char))
            self.peak_pending_chars = max(self.peak_pending_chars, self.pending_chars)
        return "".join(output)

    def finish(self) -> str:
        """Flush only at trusted provider completion, never on failure/cancellation."""
        if self._finished:
            raise RuntimeError("Redactor has already finished")
        output = self._numeric(EMAIL.sub(REDACTED, self._email))
        self._email = ""
        if self._number:
            output += self._clean_number()
        self._drop_email = self._drop_number = False
        self._finished = True
        return output
