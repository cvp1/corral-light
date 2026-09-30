#!/usr/bin/python3
"""tomlmini — the TOML this product reads and writes, on any Python it runs on.

`tomllib` where it exists (3.11+). On 3.9/3.10 a STRICT reader for the only
shapes Corral's own files have, which REFUSES anything else rather than
guessing:

    # a comment                  blank lines
    key = "a basic string"       key = 12        (trailing `# comment` allowed)
    [[name]]                     an array-of-tables header; the keys under it
                                 belong to that table, as in TOML

Refused on the strict path, loudly: `[table]` headers, arrays, inline tables,
literal ('...') and multi-line strings, floats, booleans, dates, dotted keys,
and a duplicate key within one table. What it accepts it parses exactly as
`tomllib` does — `test_tomlmini.py` holds the two to the same result on the
rig template and on role files.

Moved here from Corral Light's roles.py (DESIGN-5 S12) so a rig and a role
are read by one reader. `basic()` is the one emitter: a TOML basic string with
every control character escaped, so no value can end its line and start a key.
"""
import re

_KEY = r"[A-Za-z0-9_-]+"
_ASSIGN = re.compile(r"^(" + _KEY + r")\s*=\s*(.*)$")
_AOT = re.compile(r"^\[\[\s*(" + _KEY + r")\s*\]\]\s*(#.*)?$")
_INT = re.compile(r"^([+-]?(?:0|[1-9](?:_?[0-9])*))\s*(#.*)?$")
_ESC = {"b": "\b", "t": "\t", "n": "\n", "f": "\f", "r": "\r",
        '"': '"', "\\": "\\"}


def loads(text):
    """Parse `text`: tomllib where present, the strict reader otherwise.
    Raises ValueError (tomllib's error is one) on anything it will not read."""
    try:
        import tomllib                                  # 3.11+
    except ImportError:
        return loads_strict(text)
    return tomllib.loads(text)


def _basic(s, n):
    """One basic string starting at s[0] == '"' -> (value, rest)."""
    out, i = [], 1
    while i < len(s):
        ch = s[i]
        if ch == '"':
            return "".join(out), s[i + 1:]
        if ch == "\\":
            e = s[i + 1:i + 2]
            if e in _ESC:
                out.append(_ESC[e])
                i += 2
                continue
            width = {"u": 4, "U": 8}.get(e)
            hexd = s[i + 2:i + 2 + width] if width else ""
            if not width or not re.fullmatch(r"[0-9A-Fa-f]{%d}" % width, hexd):
                raise ValueError(f"line {n}: invalid escape in a string")
            cp = int(hexd, 16)
            if cp > 0x10FFFF or 0xD800 <= cp <= 0xDFFF:
                raise ValueError(f"line {n}: invalid unicode escape")
            out.append(chr(cp))
            i += 2 + width
            continue
        if (ord(ch) < 0x20 and ch != "\t") or ord(ch) == 0x7F:
            raise ValueError(f"line {n}: a control character in a string "
                             f"must be escaped")
        out.append(ch)
        i += 1
    raise ValueError(f"line {n}: unterminated string")


def _value(raw, n):
    if raw.startswith('"'):
        if raw.startswith('"""'):
            raise ValueError(f"line {n}: multi-line strings are not read here")
        val, rest = _basic(raw, n)
        rest = rest.strip()
        if rest and not rest.startswith("#"):
            raise ValueError(f"line {n}: unexpected text after the string")
        return val
    m = _INT.match(raw)
    if m:
        return int(m.group(1).replace("_", ""))
    raise ValueError(f"line {n}: value must be a quoted string or an integer")


def loads_strict(text):
    """The 3.9/3.10 reader. Always strict, whatever Python this is — the
    parity test calls it directly."""
    root, table = {}, None
    for n, raw in enumerate(text.splitlines(), 1):
        line = raw.strip()
        if not line or line.startswith("#"):
            continue
        if line.startswith("["):
            m = _AOT.match(line)
            if not m:
                raise ValueError(f"line {n}: only `[[name]]` table headers are "
                                 f"read here")
            name = m.group(1)
            arr = root.setdefault(name, [])
            if not isinstance(arr, list):
                raise ValueError(f"line {n}: {name!r} is already a key")
            table = {}
            arr.append(table)
            continue
        m = _ASSIGN.match(line)
        if not m:
            raise ValueError(f"line {n}: only `key = value` lines are read here")
        key, val = m.group(1), _value(m.group(2), n)
        into = root if table is None else table
        if key in into:
            raise ValueError(f"line {n}: duplicate key {key!r}")
        into[key] = val
    return root


def basic(value):
    """One TOML basic string; the whole injection defence (a newline must never
    be able to introduce a key)."""
    simple = {"\\": "\\\\", '"': '\\"', "\b": "\\b", "\t": "\\t",
              "\n": "\\n", "\f": "\\f", "\r": "\\r"}
    out = ['"']
    for ch in str(value):
        if ch in simple:
            out.append(simple[ch])
        elif ord(ch) < 0x20 or ord(ch) == 0x7F:
            out.append("\\u%04X" % ord(ch))
        else:
            out.append(ch)
    out.append('"')
    return "".join(out)
