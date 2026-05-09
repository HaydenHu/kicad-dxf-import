"""
DXF File Reader - Pure Python parser for DXF R12-R2018+ format.

Handles the most common DXF entity types used for PCB outlines and
mechanical drawings: LINE, CIRCLE, ARC, LWPOLYLINE, POLYLINE (2D),
TEXT, MTEXT, ELLIPSE, and SPLINE.

DXF file structure:
  - HEADER section  (variables)
  - TABLES section  (layer names, line types, etc.)
  - BLOCKS section  (block definitions)
  - ENTITIES section (drawing entities) ← primary target
  - OBJECTS section (non-graphical objects, R2000+)

Each entity is a series of group-code/value pairs:
  group_code (int)
  value (str/float/int)
"""

from __future__ import annotations

import math
from dataclasses import dataclass, field
from typing import Any


@dataclass
class DxfEntity:
    """Base class for all DXF entities."""
    entity_type: str = field(default="", init=False)
    layer: str = "0"
    handle: str = ""
    color: int = 256  # BYLAYER

    def to_dict(self) -> dict[str, Any]:
        return {"type": self.entity_type, "layer": self.layer}


@dataclass
class DxfLine(DxfEntity):
    x1: float = 0.0
    y1: float = 0.0
    x2: float = 0.0
    y2: float = 0.0

    def __post_init__(self):
        self.entity_type = "LINE"


@dataclass
class DxfCircle(DxfEntity):
    cx: float = 0.0
    cy: float = 0.0
    radius: float = 0.0

    def __post_init__(self):
        self.entity_type = "CIRCLE"


@dataclass
class DxfArc(DxfEntity):
    cx: float = 0.0
    cy: float = 0.0
    radius: float = 0.0
    start_angle: float = 0.0
    end_angle: float = 0.0

    def __post_init__(self):
        self.entity_type = "ARC"


@dataclass
class DxfPolyline(DxfEntity):
    points: list[tuple[float, float]] = field(default_factory=list)
    bulges: list[float] = field(default_factory=list)
    closed: bool = False
    polyline_flags: int = 0

    def __post_init__(self):
        self.entity_type = "POLYLINE"


@dataclass
class DxfLwPolyline(DxfEntity):
    points: list[tuple[float, float]] = field(default_factory=list)
    bulges: list[float] = field(default_factory=list)
    closed: bool = False
    vertex_count: int = 0

    def __post_init__(self):
        self.entity_type = "LWPOLYLINE"


@dataclass
class DxfText(DxfEntity):
    x: float = 0.0
    y: float = 0.0
    height: float = 2.5
    text: str = ""
    rotation: float = 0.0
    style: str = "STANDARD"
    halign: int = 0  # 0=left, 1=center, 2=right
    valign: int = 0  # 0=baseline, 1=bottom, 2=middle, 3=top
    font_props: dict = field(default_factory=dict)

    def __post_init__(self):
        self.entity_type = "TEXT"


@dataclass
class DxfMText(DxfEntity):
    x: float = 0.0
    y: float = 0.0
    height: float = 2.5
    text: str = ""
    rotation: float = 0.0
    rect_width: float = 0.0
    attachment_point: int = 1
    font_props: dict = field(default_factory=dict)

    def __post_init__(self):
        self.entity_type = "MTEXT"


@dataclass
class DxfEllipse(DxfEntity):
    cx: float = 0.0
    cy: float = 0.0
    major_x: float = 1.0
    major_y: float = 0.0
    ratio: float = 1.0  # minor/major axis ratio
    start_param: float = 0.0
    end_param: float = 2.0 * math.pi

    def __post_init__(self):
        self.entity_type = "ELLIPSE"


@dataclass
class DxfSpline(DxfEntity):
    degree: int = 3
    knots: list[float] = field(default_factory=list)
    control_points: list[tuple[float, float]] = field(default_factory=list)
    fit_points: list[tuple[float, float]] = field(default_factory=list)
    weights: list[float] = field(default_factory=list)
    knot_tol: float = 1e-10
    ctrl_tol: float = 1e-10
    fit_tol: float = 1e-10
    flags: int = 0
    # flags bit 0: closed, bit 1: periodic, bit 2: rational, bit 3: planar

    def __post_init__(self):
        self.entity_type = "SPLINE"


class DxfReader:
    """Parses a DXF file and returns a list of entity objects."""

    def __init__(self):
        self.entities: list[DxfEntity] = []
        self.layers: dict[str, dict[str, Any]] = {}
        self._tokens: list[tuple[int, str]] = []

    def _detect_dxf_encoding(self, filepath: str) -> str:
        """Detect DXF file encoding from $DWGCODEPAGE header.
        Falls back to gbk (common for Chinese CAD), then built-in heuristics,
        then utf-8."""
        import re
        import codecs
        cp_map = {
            # AutoCAD code pages -> Python encoding
            "ANSI_874": "cp874",      # Thai
            "ANSI_932": "shift_jis",  # Japanese
            "ANSI_936": "gbk",        # Chinese Simplified
            "ANSI_949": "euc_kr",     # Korean
            "ANSI_950": "big5",       # Chinese Traditional
            "ANSI_1250": "cp1250",    # Central/Eastern Europe
            "ANSI_1251": "cp1251",    # Cyrillic
            "ANSI_1252": "cp1252",    # Western Europe
            "ANSI_1253": "cp1253",    # Greek
            "ANSI_1254": "cp1254",    # Turkish
            "ANSI_1255": "cp1255",    # Hebrew
            "ANSI_1256": "cp1256",    # Arabic
            "ANSI_1257": "cp1257",    # Baltic
            "ANSI_1258": "cp1258",    # Vietnamese
            "UTF-8": "utf-8",
            "UTF8": "utf-8",
            "GB2312": "gbk",
            "BIG5": "big5",
            "SHIFT_JIS": "shift_jis",
            "EUC_KR": "euc_kr",
        }
        try:
            with open(filepath, "rb") as f:
                head = f.read(8192)
            idx = head.find(b"DWGCODEPAGE")
            if idx >= 0:
                m = re.search(rb"\n\s*3\s*\n\s*(\S+)", head[idx:])
                if m:
                    codepage = m.group(1).decode("ascii").strip()
                    if codepage in cp_map:
                        return cp_map[codepage]
                    # Try numeric code page
                    try:
                        return "cp" + str(int(codepage))
                    except ValueError:
                        pass
        except Exception:
            pass
        # Auto-detect: try encodings in order of likelihood
        for enc in ("gbk", "gb18030", "shift_jis", "euc_kr", "utf-8"):
            try:
                with open(filepath, "r", encoding=enc) as f:
                    f.read(4096)
                return enc
            except Exception:
                continue
        return "utf-8"

    def read(self, filepath: str) -> list[DxfEntity]:
        """Parse a DXF file and return all recognized entities."""
        self.entities = []
        self.layers = {}
        self._tokens = []

        encoding = self._detect_dxf_encoding(filepath)
        with open(filepath, "r", encoding=encoding, errors="replace") as f:
            content = f.read()

        self._tokenize(content)
        self._parse()

        return self.entities

    def _tokenize(self, content: str) -> None:
        """Convert DXF text into (group_code, value) tokens."""
        lines = content.splitlines()
        # Strip trailing whitespace but keep the lines
        lines = [line.rstrip() for line in lines]

        i = 0
        while i < len(lines):
            code_line = lines[i].strip()
            i += 1

            if not code_line:
                continue

            try:
                group_code = int(code_line)
            except ValueError:
                # Not a valid group code line, skip
                continue

            if i < len(lines):
                value = lines[i].rstrip()
                i += 1
            else:
                value = ""

            self._tokens.append((group_code, value))

    def _parse(self) -> None:
        """Walk through tokens and dispatch sections."""
        idx = 0
        while idx < len(self._tokens):
            code, value = self._tokens[idx]

            if code == 0 and value.upper() == "SECTION":
                idx += 1
                if idx < len(self._tokens):
                    _, section_name = self._tokens[idx]
                    idx += 1
                    idx = self._parse_section(section_name.upper(), idx)
            else:
                idx += 1

    def _parse_section(self, name: str, idx: int) -> int:
        """Dispatch to the appropriate section parser."""
        if name == "ENTITIES" or name == "BLOCKS":
            return self._parse_entities(idx)
        elif name == "TABLES":
            return self._parse_tables(idx)
        else:
            return self._skip_section(idx)

    def _skip_section(self, idx: int) -> int:
        """Skip a section by reading until ENDSEC."""
        while idx < len(self._tokens):
            code, value = self._tokens[idx]
            if code == 0 and value.upper() == "ENDSEC":
                return idx + 1
            idx += 1
        return idx

    def _parse_tables(self, idx: int) -> int:
        """Parse TABLES section, extracting layer definitions."""
        while idx < len(self._tokens):
            code, value = self._tokens[idx]

            if code == 0 and value.upper() == "ENDSEC":
                return idx + 1

            if code == 0 and value.upper() == "TABLE":
                idx += 1
                if idx < len(self._tokens):
                    _, table_type = self._tokens[idx]
                    if table_type.upper() == "LAYER":
                        idx += 1
                        idx = self._parse_layer_table(idx)
                        continue
                idx += 1
            else:
                idx += 1

        return idx

    def _parse_layer_table(self, idx: int) -> int:
        """Parse LAYER table entries."""
        while idx < len(self._tokens):
            code, value = self._tokens[idx]

            if code == 0 and value.upper() == "ENDTAB":
                return idx + 1

            if code == 0 and value.upper() == "LAYER":
                idx += 1
                layer_data: dict[str, Any] = {"name": "0", "color": 7, "frozen": False}
                while idx < len(self._tokens):
                    c, v = self._tokens[idx]
                    if c == 0:
                        break
                    if c == 2:   # layer name
                        layer_data["name"] = v
                    elif c == 62:  # color (negative = frozen)
                        color = int(v)
                        if color < 0:
                            layer_data["frozen"] = True
                            color = abs(color)
                        layer_data["color"] = color
                    elif c == 70:  # flags
                        layer_data["flags"] = int(v)
                    idx += 1
                self.layers[layer_data["name"]] = layer_data
            else:
                idx += 1

        return idx

    # ── Entity parsing ───────────────────────────────────────────

    def _parse_entities(self, idx: int) -> int:
        """Parse ENTITIES section."""
        while idx < len(self._tokens):
            code, value = self._tokens[idx]

            if code == 0 and value.upper() == "ENDSEC":
                return idx + 1

            if code == 0:
                entity_type = value.upper()
                idx += 1
                idx = self._parse_entity(entity_type, idx)
            else:
                idx += 1

        return idx

    def _parse_entity(self, etype: str, idx: int) -> int:
        """Parse a single entity based on its type."""
        handlers = {
            "LINE": self._parse_line,
            "CIRCLE": self._parse_circle,
            "ARC": self._parse_arc,
            "LWPOLYLINE": self._parse_lwpolyline,
            "POLYLINE": self._parse_polyline,
            "TEXT": self._parse_text,
            "MTEXT": self._parse_mtext,
            "ELLIPSE": self._parse_ellipse,
            "SPLINE": self._parse_spline,
        }
        handler = handlers.get(etype)
        if handler:
            return handler(idx)
        else:
            return self._skip_entity(idx)

    # Group codes for common entity attributes (not geometry)
    _ATTR_CODES = frozenset({5, 6, 8, 48, 60, 62, 67, 330, 347, 370, 390, 410})

    def _read_attrs(self, idx: int) -> tuple[int, dict[str, Any]]:
        """Read common entity attributes (layer, color, handle).

        Stops when it encounters a group code that is not a known
        attribute code (e.g. geometry codes 10-59).

        Returns (next_idx, attrs_dict).
        """
        attrs: dict[str, Any] = {}
        # Skip subclass marker (AcDbEntity, etc.)
        while idx < len(self._tokens) and self._tokens[idx][0] == 100:
            idx += 1
        while idx < len(self._tokens):
            code, value = self._tokens[idx]
            if code not in self._ATTR_CODES and code != 100:
                break
            if code == 5:   # handle
                attrs["handle"] = value
            elif code == 8:  # layer name
                attrs["layer"] = value
            elif code == 62:  # color
                attrs["color"] = int(value)
            idx += 1
            # Skip subclass markers that appear inline
            while idx < len(self._tokens) and self._tokens[idx][0] == 100:
                idx += 1
        attrs.setdefault("layer", "0")
        attrs.setdefault("handle", "")
        attrs.setdefault("color", 256)
        return idx, attrs

    def _parse_line(self, idx: int) -> int:
        idx, attrs = self._read_attrs(idx)
        line = DxfLine(**attrs)
        while idx < len(self._tokens):
            code, value = self._tokens[idx]
            if code == 0:
                break
            if code == 10:
                line.x1 = float(value)
            elif code == 20:
                line.y1 = float(value)
            elif code == 11:
                line.x2 = float(value)
            elif code == 21:
                line.y2 = float(value)
            idx += 1
        self.entities.append(line)
        return idx

    def _parse_circle(self, idx: int) -> int:
        idx, attrs = self._read_attrs(idx)
        circle = DxfCircle(**attrs)
        while idx < len(self._tokens):
            code, value = self._tokens[idx]
            if code == 0:
                break
            if code == 10:
                circle.cx = float(value)
            elif code == 20:
                circle.cy = float(value)
            elif code == 40:
                circle.radius = float(value)
            idx += 1
        self.entities.append(circle)
        return idx

    def _parse_arc(self, idx: int) -> int:
        idx, attrs = self._read_attrs(idx)
        arc = DxfArc(**attrs)
        while idx < len(self._tokens):
            code, value = self._tokens[idx]
            if code == 0:
                break
            if code == 10:
                arc.cx = float(value)
            elif code == 20:
                arc.cy = float(value)
            elif code == 40:
                arc.radius = float(value)
            elif code == 50:
                arc.start_angle = float(value)
            elif code == 51:
                arc.end_angle = float(value)
            idx += 1
        self.entities.append(arc)
        return idx

    def _parse_lwpolyline(self, idx: int) -> int:
        idx, attrs = self._read_attrs(idx)
        pline = DxfLwPolyline(**attrs)
        while idx < len(self._tokens):
            code, value = self._tokens[idx]
            if code == 0:
                break
            if code == 90:
                pline.vertex_count = int(value)
            elif code == 70:
                pline.closed = (int(value) & 1) != 0
            elif code == 10:
                # x of current vertex — read y (20), then optional bulge (42)
                x = float(value)
                y = 0.0
                bulge = 0.0
                look = idx + 1
                while look < len(self._tokens):
                    lc, lv = self._tokens[look]
                    if lc == 0 or lc == 10 or lc == 90:
                        break
                    if lc == 20:
                        y = float(lv)
                    elif lc == 42:
                        bulge = float(lv)
                    look += 1
                pline.points.append((x, y))
                pline.bulges.append(bulge)
            idx += 1
        self.entities.append(pline)
        return idx

    def _parse_polyline(self, idx: int) -> int:
        """Parse 2D POLYLINE (group code 0 = POLYLINE ... 0 = SEQEND)."""
        idx, attrs = self._read_attrs(idx)
        # Skip the polyline header attributes (70 = flags, etc.)
        pline = DxfPolyline(**attrs)
        while idx < len(self._tokens) and self._tokens[idx][0] != 0:
            code, value = self._tokens[idx]
            if code == 70:
                pline.polyline_flags = int(value)
                pline.closed = (pline.polyline_flags & 1) != 0
            idx += 1

        # Read VERTEX entities until SEQEND
        while idx < len(self._tokens):
            code, value = self._tokens[idx]
            if code == 0 and value.upper() == "SEQEND":
                idx += 1
                while idx < len(self._tokens) and self._tokens[idx][0] != 0:
                    idx += 1  # skip SEQEND attributes
                break
            if code == 0 and value.upper() == "VERTEX":
                idx += 1
                vx, vy = 0.0, 0.0
                bulge = 0.0
                while idx < len(self._tokens) and self._tokens[idx][0] != 0:
                    vc, vv = self._tokens[idx]
                    if vc == 10:
                        vx = float(vv)
                    elif vc == 20:
                        vy = float(vv)
                    elif vc == 42:
                        bulge = float(vv)
                    idx += 1
                pline.points.append((vx, vy))
                pline.bulges.append(bulge)
            else:
                idx += 1

        self.entities.append(pline)
        return idx

    def _parse_text(self, idx: int) -> int:
        idx, attrs = self._read_attrs(idx)
        text = DxfText(**attrs)
        while idx < len(self._tokens):
            code, value = self._tokens[idx]
            if code == 0:
                break
            if code == 10:
                text.x = float(value)
            elif code == 20:
                text.y = float(value)
            elif code == 40:
                text.height = float(value)
            elif code == 1:
                # Clean TEXT content, extract font properties
                cleaned, font_props = self._clean_mtext_ex(value)
                text.text = cleaned
                text.font_props = font_props
            elif code == 50:
                text.rotation = float(value)
            elif code == 7:
                text.style = value
            elif code == 72:
                text.halign = int(value)
            elif code == 73:
                text.valign = int(value)
            idx += 1
        self.entities.append(text)
        return idx

    def _parse_mtext(self, idx: int) -> int:
        idx, attrs = self._read_attrs(idx)
        mtext = DxfMText(**attrs)
        raw_text_parts: list[str] = []
        while idx < len(self._tokens):
            code, value = self._tokens[idx]
            if code == 0:
                break
            if code == 10:
                mtext.x = float(value)
            elif code == 20:
                mtext.y = float(value)
            elif code == 40:
                mtext.height = float(value)
            elif code == 50:
                mtext.rotation = float(value)
            elif code == 41:
                mtext.rect_width = float(value)
            elif code == 71:
                mtext.attachment_point = int(value)
            elif code == 1:
                raw_text_parts.append(value)
            elif code == 3:
                raw_text_parts.append(value)
            idx += 1
        # Strip DXF formatting codes, extract font properties
        text = "".join(raw_text_parts)
        cleaned_text, font_props = self._clean_mtext_ex(text)
        mtext.text = cleaned_text
        mtext.font_props = font_props
        self.entities.append(mtext)
        return idx

    @staticmethod
    def _parse_font_tag(text: str) -> tuple[dict, str]:
        """Extract font properties from \\f tag.
        Returns (props_dict, remaining_text).
        \\f may appear as \\x5cf (literal \\f) or \\x0c (formfeed from Python's \\f).
        Tag format: \\f<fontname>|b<0|1>|i<0|1>|c<color>|p<pitch>;
        """
        import re
        props = {}
        # Find \\f tag anywhere in text (skip leading format codes like \\A1;)
        m = re.search(r"[\x03\x0c].*?;", text)
        if not m:
            m = re.search(r"\x5cf[^;]*;", text)
        if m:
            raw = m.group(0)
            # Strip leader: \\x0c or \\x03 alone, or \\x5cf
            inner = raw
            if inner[0] in "\x03\x0c":
                inner = inner[1:]
            elif inner[:2] == "\x5cf":
                inner = inner[2:]
            inner = inner.rstrip(";")
            parts = inner.split("|")
            if parts:
                props["name"] = parts[0]
            for p in parts[1:]:
                if not p or len(p) < 2:
                    continue
                code = p[0].lower()
                val = p[1:]
                if code == "b":
                    props["bold"] = (val != "0")
                elif code == "i":
                    props["italic"] = (val != "0")
                elif code == "c":
                    try:
                        props["color"] = int(val)
                    except ValueError:
                        pass
                elif code == "p":
                    try:
                        props["pitch"] = int(val)
                    except ValueError:
                        pass
            # Remove only the font tag, keep text before it
            text = text[:m.start()] + text[m.end():]
        return props, text

    @staticmethod
    def _clean_mtext(text: str) -> str:
        """Remove DXF MTEXT formatting codes while preserving CJK text."""
        return DxfReader._clean_mtext_ex(text)[0]

    @staticmethod
    def _clean_mtext_ex(text: str) -> tuple[str, dict]:
        """Remove DXF MTEXT formatting codes, return (cleaned_text, font_props)."""
        import re

        # Extract font properties from \\f tag
        font_props, text = DxfReader._parse_font_tag(text)

        # Handle \\P paragraph break before format code removal
        text = text.replace("\x5cP", "\n").replace("\x0cP", "\n")

        # Unified format code removal with callback
        def _replace_code(m):
            full = m.group(0)
            leader = full[0]   # \x0c, \x03, or backslash
            letter = full[1]   # A-Za-z
            inner = full[2:-1]  # content between letter and ;

            # \x0cS or \x03S = \\f font tag starting with S, not \\S stack
            if leader != "\x5c" and letter == "S":
                return ""
            # \\S stack: upper^lower or upper/lower
            # Convert to two lines, lower line with overline
            if leader == "\x5c" and letter == "S":
                sep = "^" if "^" in inner else "/"
                upper, lower = inner.split(sep, 1)
                return upper + "\n~{" + lower + "}"
            return ""

        text = re.sub(r"[\x03\x0c\x5c][A-Za-z][^;]*;", _replace_code, text)

        # Handle \\U+XXXX Unicode escapes (e.g. \\U+2205 = diameter symbol)
        idx = 0
        while True:
            idx = text.find("\x5cU+", idx)
            if idx < 0:
                break
            hex_start = idx + 3
            if hex_start + 4 <= len(text):
                try:
                    codepoint = int(text[hex_start:hex_start + 4], 16)
                    text = text[:idx] + chr(codepoint) + text[hex_start + 4:]
                except (ValueError, OverflowError):
                    idx += 1
            else:
                idx += 1

        text = text.replace("{", "").replace("}", "")
        text = text.replace("\x5c~", " ")
        return text.strip(), font_props

    def _parse_ellipse(self, idx: int) -> int:
        idx, attrs = self._read_attrs(idx)
        ellipse = DxfEllipse(**attrs)
        while idx < len(self._tokens):
            code, value = self._tokens[idx]
            if code == 0:
                break
            if code == 10:
                ellipse.cx = float(value)
            elif code == 20:
                ellipse.cy = float(value)
            elif code == 11:
                ellipse.major_x = float(value)
            elif code == 21:
                ellipse.major_y = float(value)
            elif code == 40:
                ellipse.ratio = float(value)
            elif code == 41:
                ellipse.start_param = float(value)
            elif code == 42:
                ellipse.end_param = float(value)
            idx += 1
        self.entities.append(ellipse)
        return idx

    def _parse_spline(self, idx: int) -> int:
        idx, attrs = self._read_attrs(idx)
        spline = DxfSpline(**attrs)
        while idx < len(self._tokens):
            code, value = self._tokens[idx]
            if code == 0:
                break
            if code == 70:
                spline.flags = int(value)
            elif code == 71:
                spline.degree = int(value)
            elif code == 40:
                spline.knots.append(float(value))
            elif code == 41:
                spline.weights.append(float(value))
            elif code == 42:
                spline.knot_tol = float(value)
            elif code == 43:
                spline.ctrl_tol = float(value)
            elif code == 44:
                spline.fit_tol = float(value)
            elif code == 10:
                spline.control_points.append((float(value), 0.0))
            elif code == 20:
                if spline.control_points:
                    spline.control_points[-1] = (
                        spline.control_points[-1][0],
                        float(value),
                    )
            elif code == 11:
                spline.fit_points.append((float(value), 0.0))
            elif code == 21:
                if spline.fit_points:
                    spline.fit_points[-1] = (
                        spline.fit_points[-1][0],
                        float(value),
                    )
            idx += 1
        self.entities.append(spline)
        return idx

    def _skip_entity(self, idx: int) -> int:
        """Skip an unsupported entity by reading until next group code 0."""
        while idx < len(self._tokens):
            if self._tokens[idx][0] == 0:
                break
            idx += 1
        return idx


# ── Geometry utility functions ──────────────────────────────────

def polyline_points_from_bulges(
    points: list[tuple[float, float]],
    bulges: list[float],
    segments: int = 36,
) -> list[tuple[float, float]]:
    """Expand a polyline with bulge factors into interpolated arc points.

    A bulge of 0 means a straight line segment.
    A bulge of b means an arc segment: b = tan(theta/4) where theta is the
    included angle (positive = CCW).

    Returns a list of interpolated points suitable for conversion to line segments.
    """
    if not points:
        return []

    result: list[tuple[float, float]] = [points[0]]

    for i in range(len(points) - 1):
        bulge = bulges[i] if i < len(bulges) else 0.0
        p1 = points[i]
        p2 = points[i + 1]

        if abs(bulge) < 1e-12:
            result.append(p2)
            continue

        # Calculate arc from bulge
        dx = p2[0] - p1[0]
        dy = p2[1] - p1[1]
        chord = math.hypot(dx, dy)
        if chord < 1e-12:
            result.append(p2)
            continue

        # Included angle
        theta = 4.0 * math.atan(bulge)
        radius = chord / (2.0 * math.sin(abs(theta) / 2.0))
        mid_x = (p1[0] + p2[0]) / 2.0
        mid_y = (p1[1] + p2[1]) / 2.0
        perp_x = -dy / chord
        perp_y = dx / chord
        sagitta = chord * abs(bulge) / 2.0
        center_sign = 1.0 if bulge > 0 else -1.0

        cx = mid_x + center_sign * perp_x * (radius - sagitta)
        cy = mid_y + center_sign * perp_y * (radius - sagitta)

        start_angle = math.atan2(p1[1] - cy, p1[0] - cx)
        end_angle = math.atan2(p2[1] - cy, p2[0] - cx)

        if bulge > 0 and end_angle <= start_angle:
            end_angle += 2.0 * math.pi
        elif bulge < 0 and end_angle >= start_angle:
            end_angle -= 2.0 * math.pi

        num_segs = max(1, int(segments * abs(theta) / (2.0 * math.pi)))
        for j in range(1, num_segs + 1):
            t = j / num_segs
            angle = start_angle + t * (end_angle - start_angle)
            result.append((cx + radius * math.cos(angle), cy + radius * math.sin(angle)))

    return result


def ellipse_to_arcs(
    cx: float, cy: float,
    major_x: float, major_y: float,
    ratio: float,
    start_param: float, end_param: float,
    segments: int = 48,
) -> list[tuple[tuple[float, float], float, float, float, float]]:
    """Approximate an ellipse with a series of arc segments.

    Returns a list of (center_x, center_y, radius, start_angle, end_angle)
    for approximating arcs. For low eccentricity ellipses, this is more
    efficient than polyline approximation.
    """
    # For a circle (ratio ≈ 1), return one arc
    major_len = math.hypot(major_x, major_y)
    if abs(ratio - 1.0) < 0.001:
        angle = math.atan2(major_y, major_x)
        start_deg = math.degrees(start_param - angle)
        end_deg = math.degrees(end_param - angle)
        if end_param - start_param >= 2.0 * math.pi - 0.001:
            end_deg = start_deg + 360.0
        return [(cx, cy, major_len, start_deg, end_deg)]

    # For ellipses, approximate with arcs
    # We split into segments and fit arcs to each segment
    results: list[tuple[tuple[float, float], float, float, float, float]] = []
    rotation = math.atan2(major_y, major_x)
    minor_len = major_len * ratio
    num_segs = max(4, segments)
    span = end_param - start_param

    pts: list[tuple[float, float]] = []
    for i in range(num_segs + 1):
        t = start_param + span * i / num_segs
        rx = major_len * math.cos(t)
        ry = minor_len * math.sin(t)
        x = cx + rx * math.cos(rotation) - ry * math.sin(rotation)
        y = cy + rx * math.sin(rotation) + ry * math.cos(rotation)
        pts.append((x, y))

    # Convert to bulge-based arcs and expand
    # For simplicity, we'll just return polylines at the caller level.
    # Here we return the points for caller to handle.
    return [("ELLIPSE_PTS", pts)]


def spline_to_polyline(
    control_points: list[tuple[float, float]],
    knots: list[float],
    degree: int,
    segments: int = 50,
) -> list[tuple[float, float]]:
    """Convert a B-spline (NURBS without weights) to a polyline approximation.

    Uses de Boor's algorithm for evaluation.
    """
    if len(control_points) < degree + 1:
        return control_points[:]

    n = len(control_points) - 1
    # Clamp knots to valid range
    if len(knots) != n + degree + 2:
        # Generate uniform clamped knots
        knots = [0.0] * (degree + 1) + [
            i / (n - degree + 1) for i in range(1, n - degree + 1)
        ] + [1.0] * (degree + 1)

    t_min = knots[degree]
    t_max = knots[n + 1]
    if t_max <= t_min:
        t_max = t_min + 1.0

    result: list[tuple[float, float]] = []
    for i in range(segments + 1):
        t = t_min + (t_max - t_min) * i / segments
        pt = _de_boor(control_points, knots, degree, t)
        result.append(pt)

    return result


def _de_boor(
    control_points: list[tuple[float, float]],
    knots: list[float],
    degree: int,
    t: float,
) -> tuple[float, float]:
    """Evaluate a B-spline at parameter t using de Boor's algorithm."""
    n = len(control_points) - 1

    # Find knot span
    span = degree
    for i in range(degree, n + 1):
        if knots[i] <= t < knots[i + 1]:
            span = i
            break
    else:
        if t >= knots[n + 1]:
            span = n

    # Initialize d with control points in relevant span
    d: list[tuple[float, float]] = []
    for i in range(span - degree, span + 1):
        if 0 <= i <= n:
            d.append(control_points[i])
        else:
            d.append((0.0, 0.0))

    # de Boor recursion
    for r in range(1, degree + 1):
        for i in range(degree, r - 1, -1):
            idx = span - degree + i
            knot_left = knots[idx]
            knot_right = knots[idx + degree - r + 1]
            denom = knot_right - knot_left
            if abs(denom) < 1e-15:
                alpha = 0.0
            else:
                alpha = (t - knot_left) / denom

            pi = i
            pi_1 = i - 1
            if pi < len(d) and pi_1 >= 0:
                d[pi] = (
                    (1.0 - alpha) * d[pi_1][0] + alpha * d[pi][0],
                    (1.0 - alpha) * d[pi_1][1] + alpha * d[pi][1],
                )

    return d[degree]
