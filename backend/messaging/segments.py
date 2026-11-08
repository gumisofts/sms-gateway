"""SMS segmentation: how many parts the network will bill a body as.

Mirrors what Android's ``SmsManager.divideMessage`` does for the common cases
(no national language shift tables): a body that fits the GSM 03.38 default
alphabet is sent as 7-bit text (160 chars single / 153 per part, extension
characters count twice); anything else (e.g. Amharic, emoji) is UCS-2
(70 single / 67 per part, counted in UTF-16 code units). The node reports the
real count after sending, which then replaces this estimate.
"""

GSM7_BASIC = frozenset(
    "@£$¥èéùìòÇ\nØø\rÅåΔ_ΦΓΛΩΠΨΣΘΞ\x1bÆæßÉ !\"#¤%&'()*+,-./0123456789:;<=>?"
    "¡ABCDEFGHIJKLMNOPQRSTUVWXYZÄÖÑÜ§¿abcdefghijklmnopqrstuvwxyzäöñüà"
)
GSM7_EXTENSION = frozenset("^{}\\[~]|€\f")

GSM7_SINGLE, GSM7_MULTI = 160, 153
UCS2_SINGLE, UCS2_MULTI = 70, 67


def is_gsm7(body: str) -> bool:
    return all(ch in GSM7_BASIC or ch in GSM7_EXTENSION for ch in body)


def _ceil_div(a: int, b: int) -> int:
    return -(-a // b)


def count_parts(body: str) -> int:
    """Number of SMS parts for ``body`` (at least 1, also for an empty body)."""
    if is_gsm7(body):
        units = sum(2 if ch in GSM7_EXTENSION else 1 for ch in body)
        single, multi = GSM7_SINGLE, GSM7_MULTI
    else:
        # UTF-16 code units: characters outside the BMP (emoji) take two.
        units = len(body.encode("utf-16-le")) // 2
        single, multi = UCS2_SINGLE, UCS2_MULTI
    if units <= single:
        return 1
    return _ceil_div(units, multi)
