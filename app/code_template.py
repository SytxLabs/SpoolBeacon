"""
Generic code template engine, used for both spool codes (AppSetting
'spool.code_template', default SB-{product_id}-{line_id}-{timestamp}-{seq:02d})
and print job codes (AppSetting 'print.code_template', default PJ-{date}-{seq:03d}).
{product_id}/{line_id} are only meaningful for spool codes — they resolve to
the given value (or 0) regardless of which template they're used in.

Supported variables:
  {product_id}        FilamentProduct ID
  {line_id}           PurchaseLine ID (0 if none)
  {timestamp}         YYYYMMDDHHmmSS
  {date}              YYYYMMDD
  {year}              YYYY
  {month}             MM
  {day}               DD
  {seq}               Sequence number within batch
  {seq:Nd}            Sequence number zero-padded to N digits (e.g. {seq:02d})
  {random}            Random 4-digit number
  {random:MIN-MAX}    Random integer in range (e.g. {random:10-50})
"""
import random
import re
from datetime import datetime

_VAR_RE = re.compile(r'\{(\w+)(?::([^}]*))?\}')

SPOOL_DEFAULT_TEMPLATE = "SB-{product_id}-{line_id}-{timestamp}-{seq:02d}"
PRINT_DEFAULT_TEMPLATE = "PJ-{date}-{seq:03d}"

AVAILABLE_VARS = [
    ("{product_id}", "FilamentProduct ID"),
    ("{line_id}", "PurchaseLine ID (0 if none)"),
    ("{timestamp}", "Timestamp YYYYMMDDHHmmSS"),
    ("{date}", "Date YYYYMMDD"),
    ("{year}", "Year YYYY"),
    ("{month}", "Month MM"),
    ("{day}", "Day DD"),
    ("{seq}", "Sequence number within batch"),
    ("{seq:02d}", "Sequence number zero-padded to 2 digits"),
    ("{random}", "Random 4-digit number"),
    ("{random:10-50}", "Random integer between 10 and 50"),
]


def generate_code(
    template: str,
    product_id: int,
    line_id: int | None,
    seq: int,
    now: datetime | None = None,
) -> str:
    if now is None:
        now = datetime.utcnow()

    def replace(m: re.Match) -> str:
        name = m.group(1)
        spec = m.group(2)

        if name == "product_id":
            return str(product_id)
        if name == "line_id":
            return str(line_id if line_id is not None else 0)
        if name == "timestamp":
            return now.strftime("%Y%m%d%H%M%S")
        if name == "date":
            return now.strftime("%Y%m%d")
        if name == "year":
            return now.strftime("%Y")
        if name == "month":
            return now.strftime("%m")
        if name == "day":
            return now.strftime("%d")
        if name == "seq":
            if spec:
                try:
                    return format(seq, spec)
                except (ValueError, TypeError):
                    pass
            return str(seq)
        if name == "random":
            if spec and "-" in spec:
                try:
                    lo, hi = spec.split("-", 1)
                    return str(random.randint(int(lo.strip()), int(hi.strip())))
                except (ValueError, TypeError):
                    pass
            return str(random.randint(1000, 9999))
        return m.group(0)

    return _VAR_RE.sub(replace, template)
