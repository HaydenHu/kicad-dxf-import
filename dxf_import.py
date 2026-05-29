"""
KiCad DXF Import Plugin for KiCad 10.0+ (IPC API version)

Imports DXF files (AutoCAD R12-R2018+) into the current Pcbnew board
via KiCad's IPC API (kicad-python / kipy).

Supported entities: LINE, CIRCLE, ARC, LWPOLYLINE, POLYLINE(2D),
TEXT, MTEXT, ELLIPSE, SPLINE, DIMENSION, LEADER.

Requirements:
    pip install kicad-python

When launched as a KiCad IPC plugin (via plugin.json), KiCad sets the
KICAD_API_SOCKET environment variable automatically.

Standalone usage:
    python dxf_import.py path/to/file.dxf [options]
"""

from __future__ import annotations

import argparse
import math
import os
import sys
import traceback

# Ensure plugin directory is on sys.path for direct imports (e.g. dxf_reader)
_plugin_dir = os.path.dirname(os.path.abspath(__file__))
if _plugin_dir not in sys.path:
    sys.path.insert(0, _plugin_dir)

import wx

import kipy
from kipy import errors
from kipy.board_types import (
    AlignedDimension,
    BoardArc,
    BoardCircle,
    BoardSegment,
    BoardText,
    LeaderDimension,
    OrthogonalDimension,
    RadialDimension,
)
from kipy.common_types import GraphicAttributes, Text, Vector2
from kipy.util.board_layer import BoardLayer

from dxf_reader import (
    DxfArc,
    DxfCircle,
    DxfDimension,
    DxfEllipse,
    DxfEntity,
    DxfLeader,
    DxfLine,
    DxfLwPolyline,
    DxfMText,
    DxfPolyline,
    DxfReader,
    DxfSpline,
    DxfText,
    ellipse_to_arcs,
    polyline_points_from_bulges,
    spline_to_polyline,
)

__version__ = "2.0.0"
__author__ = "HaydenHu"
__license__ = "GPL-3.0"


# ── Constants ───────────────────────────────────────────────────

LAYER_NAMES = [
    "Edge.Cuts",
    "F.SilkS",
    "B.SilkS",
    "F.Fab",
    "B.Fab",
    "F.CrtYd",
    "B.CrtYd",
    "Eco1.User",
    "Eco2.User",
    "Dwgs.User",
    "Cmts.User",
    "User.1",
    "User.2",
    "User.3",
    "User.4",
    "User.5",
    "User.6",
    "User.7",
    "User.8",
    "User.9",
    "F.Cu",
    "B.Cu",
]


def _layer_id(name: str) -> int:
    """Map layer name string to BoardLayer enum value."""
    mapping = {
        "F.Cu": BoardLayer.BL_F_Cu,
        "B.Cu": BoardLayer.BL_B_Cu,
        "F.SilkS": BoardLayer.BL_F_SilkS,
        "B.SilkS": BoardLayer.BL_B_SilkS,
        "F.Mask": BoardLayer.BL_F_Mask,
        "B.Mask": BoardLayer.BL_B_Mask,
        "F.Paste": BoardLayer.BL_F_Paste,
        "B.Paste": BoardLayer.BL_B_Paste,
        "F.Adhes": BoardLayer.BL_F_Adhes,
        "B.Adhes": BoardLayer.BL_B_Adhes,
        "Edge.Cuts": BoardLayer.BL_Edge_Cuts,
        "F.CrtYd": BoardLayer.BL_F_CrtYd,
        "B.CrtYd": BoardLayer.BL_B_CrtYd,
        "F.Fab": BoardLayer.BL_F_Fab,
        "B.Fab": BoardLayer.BL_B_Fab,
        "Eco1.User": BoardLayer.BL_Eco1_User,
        "Eco2.User": BoardLayer.BL_Eco2_User,
        "Cmts.User": BoardLayer.BL_Cmts_User,
        "Dwgs.User": BoardLayer.BL_Dwgs_User,
        "User.1": BoardLayer.BL_User_1,
        "User.2": BoardLayer.BL_User_2,
        "User.3": BoardLayer.BL_User_3,
        "User.4": BoardLayer.BL_User_4,
        "User.5": BoardLayer.BL_User_5,
        "User.6": BoardLayer.BL_User_6,
        "User.7": BoardLayer.BL_User_7,
        "User.8": BoardLayer.BL_User_8,
        "User.9": BoardLayer.BL_User_9,
    }
    return int(mapping.get(name, BoardLayer.BL_Edge_Cuts))


# AutoCAD Color Index (ACI) to RGB approximation
_ACI_COLORS = {
    1:  (255, 0, 0),     2:  (255, 255, 0),   3:  (0, 255, 0),
    4:  (0, 255, 255),   5:  (0, 0, 255),     6:  (255, 0, 255),
    7:  (255, 255, 255), 8:  (128, 128, 128), 9:  (192, 192, 192),
    10: (255, 0, 0),    11: (255, 128, 128),  12: (166, 0, 0),
    13: (166, 83, 0),   14: (166, 116, 0),    15: (166, 150, 0),
    16: (150, 166, 0),  17: (116, 166, 0),    18: (83, 166, 0),
    20: (0, 166, 83),   21: (0, 166, 116),    22: (0, 166, 150),
    23: (0, 150, 166),  24: (0, 116, 166),    25: (0, 83, 166),
    30: (255, 128, 64), 31: (255, 179, 179),  40: (255, 212, 170),
    50: (255, 238, 170), 60: (255, 255, 170),  70: (230, 255, 170),
    80: (170, 255, 170), 90: (170, 255, 209), 100: (170, 255, 230),
    110: (170, 255, 255), 120: (170, 230, 255), 130: (170, 209, 255),
    140: (170, 170, 255), 150: (209, 170, 255), 160: (230, 170, 255),
    170: (255, 170, 255), 180: (255, 170, 230), 190: (255, 170, 209),
    200: (255, 170, 170), 210: (255, 128, 170), 220: (255, 85, 170),
    230: (255, 43, 170), 240: (255, 0, 170),   250: (128, 128, 128),
}

_FONT_NAME_MAP = {
    "SimSun": "\u5b8b\u4f53",
    "SimHei": "\u9ed1\u4f53",
    "KaiTi": "\u6977\u4f53",
    "FangSong": "\u4eff\u5b8b",
    "Microsoft YaHei": "\u5fae\u8f6f\u96c5\u9ed1",
}


class DxfImport:
    """Import DXF entities into a KiCad board via the IPC API."""

    def __init__(
        self,
        board: kipy.board.Board,
        unit_scale: float = 1_000_000.0,
        target_layer: int = BoardLayer.BL_Edge_Cuts,
        text_layer: int = BoardLayer.BL_Eco1_User,
        line_width_nm: int = 100_000,
        native_dims: bool = True,
        dim_layer: int = BoardLayer.BL_Cmts_User,
        dim_units: int = 3,
    ):
        self.board = board
        self.unit_scale = unit_scale
        self.target_layer = target_layer
        self.text_layer = text_layer
        self.line_width_nm = line_width_nm
        self.native_dims = native_dims
        self.dim_layer = dim_layer
        self.dim_units = dim_units

    # ── Coordinate helpers ──────────────────────────────────────

    def _to_nm(self, val: float, negate_y: bool = False) -> int:
        """Convert DXF units to nanometers. Negate Y for DXF→KiCad flip."""
        v = int(round(val * self.unit_scale))
        return -v if negate_y else v

    def _vec(self, x: float, y: float) -> Vector2:
        return Vector2.from_xy(self._to_nm(x), self._to_nm(y, True))

    def _set_stroke(self, attrs: GraphicAttributes, width_nm: int) -> None:
        attrs.stroke.width = width_nm
        attrs.stroke.style = 0  # SLS_SOLID

    def _make_attrs(self, width_nm: int) -> GraphicAttributes:
        a = GraphicAttributes()
        self._set_stroke(a, width_nm)
        return a

    # ── Entity import ──────────────────────────────────────────

    def import_entities(self, entities: list[DxfEntity]) -> int:
        """Create board items for all supported DXF entities. Returns count."""
        items: list = []
        dim_points = self._collect_dim_points(entities)

        for entity in entities:
            try:
                if dim_points and entity.entity_type in ("MTEXT", "TEXT"):
                    pt = (round(entity.x, 2), round(entity.y, 2))
                    if pt in dim_points:
                        continue

                et = entity.entity_type
                if et == "LINE":
                    self._add_line(items, entity)
                elif et == "CIRCLE":
                    self._add_circle(items, entity)
                elif et == "ARC":
                    self._add_arc(items, entity)
                elif et == "LWPOLYLINE":
                    self._add_lwpolyline(items, entity)
                elif et == "POLYLINE":
                    self._add_polyline(items, entity)
                elif et == "TEXT":
                    self._add_text(items, entity)
                elif et == "MTEXT":
                    self._add_mtext(items, entity)
                elif et == "ELLIPSE":
                    self._add_ellipse(items, entity)
                elif et == "SPLINE":
                    self._add_spline(items, entity)
                elif et == "DIMENSION" and self.native_dims:
                    self._add_dimension(items, entity)
                elif et == "LEADER" and self.native_dims:
                    self._add_leader(items, entity)
            except Exception as e:
                print(f"DXF Import: Failed to add {entity.entity_type}: {e}")

        if items:
            self.board.create_items(items)

        return len(items)

    def _collect_dim_points(self, entities: list) -> set:
        points = set()
        for e in entities:
            if e.entity_type == "DIMENSION":
                points.add((round(e.x_text, 2), round(e.y_text, 2)))
                points.add((round(e.x_mid, 2), round(e.y_mid, 2)))
            elif e.entity_type == "LEADER":
                hooks = getattr(e, 'hooks', [])
                if hooks:
                    points.add((round(hooks[0][0], 2), round(hooks[0][1], 2)))
        return points

    # ── Shapes ─────────────────────────────────────────────────

    def _add_line(self, items: list, e: DxfLine) -> None:
        seg = BoardSegment()
        seg.layer = self.target_layer
        self._set_stroke(seg.attributes, self.line_width_nm)
        seg.start = self._vec(e.x1, e.y1)
        seg.end = self._vec(e.x2, e.y2)
        items.append(seg)

    def _add_circle(self, items: list, e: DxfCircle) -> None:
        circ = BoardCircle()
        circ.layer = self.target_layer
        circ.attributes = self._make_attrs(self.line_width_nm)
        circ.center = self._vec(e.cx, e.cy)
        circ.radius_point = self._vec(e.cx + e.radius, e.cy)
        items.append(circ)

    def _add_arc(self, items: list, e: DxfArc) -> None:
        arc = BoardArc()
        arc.layer = self.target_layer
        arc.attributes = self._make_attrs(self.line_width_nm)

        cx_nm = self._to_nm(e.cx)
        cy_nm = self._to_nm(e.cy, True)
        r_nm = self._to_nm(e.radius)

        sa_rad = math.radians(e.start_angle)
        ea_rad = math.radians(e.end_angle)
        # cy_nm already Y-flipped (negated), sin terms must subtract
        sx = cx_nm + int(r_nm * math.cos(sa_rad))
        sy = cy_nm - int(r_nm * math.sin(sa_rad))
        ex = cx_nm + int(r_nm * math.cos(ea_rad))
        ey = cy_nm - int(r_nm * math.sin(ea_rad))

        mid = (sa_rad + ea_rad) / 2.0
        if e.end_angle < e.start_angle:
            mid += math.pi
        mx = cx_nm + int(r_nm * math.cos(mid))
        my = cy_nm - int(r_nm * math.sin(mid))

        arc.start = Vector2.from_xy(sx, sy)
        arc.end = Vector2.from_xy(ex, ey)
        arc.mid = Vector2.from_xy(mx, my)
        items.append(arc)

    def _add_lwpolyline(self, items: list, e: DxfLwPolyline) -> None:
        if len(e.points) < 2:
            return
        pts = polyline_points_from_bulges(e.points, e.bulges)
        self._add_polyline_points(items, pts, e.closed)

    def _add_polyline(self, items: list, e: DxfPolyline) -> None:
        if len(e.points) < 2:
            return
        pts = polyline_points_from_bulges(e.points, e.bulges)
        self._add_polyline_points(items, pts, e.closed)

    def _add_polyline_points(self, items: list, pts: list, closed: bool) -> None:
        for i in range(len(pts) - 1):
            seg = BoardSegment()
            seg.layer = self.target_layer
            seg.attributes = self._make_attrs(self.line_width_nm)
            seg.start = self._vec(pts[i][0], pts[i][1])
            seg.end = self._vec(pts[i + 1][0], pts[i + 1][1])
            items.append(seg)

        if closed and len(pts) > 2:
            seg = BoardSegment()
            seg.layer = self.target_layer
            seg.attributes = self._make_attrs(self.line_width_nm)
            seg.start = self._vec(pts[-1][0], pts[-1][1])
            seg.end = self._vec(pts[0][0], pts[0][1])
            items.append(seg)

    def _add_text(self, items: list, e: DxfText) -> None:
        if not e.text.strip():
            return

        txt = BoardText()
        txt.layer = self.text_layer
        txt.value = e.text
        txt.position = self._vec(e.x, e.y)

        size_nm = self._to_nm(e.height)
        txt.attributes.size = Vector2.from_xy(size_nm, size_nm)
        txt.attributes.angle = e.rotation
        txt.attributes.multiline = "\n" in e.text
        if txt.attributes.multiline:
            txt.attributes.line_spacing = 1.0

        halign_map = {0: 0, 1: 1, 2: 2}
        txt.attributes.horizontal_alignment = halign_map.get(e.halign, 0)

        valign_map = {0: 2, 1: 2, 2: 1, 3: 0}
        txt.attributes.vertical_alignment = valign_map.get(e.valign, 2)

        self._apply_font_props(txt, e.font_props)
        items.append(txt)

    def _add_mtext(self, items: list, e: DxfMText) -> None:
        if not e.text.strip():
            return

        txt = BoardText()
        txt.layer = self.text_layer
        txt.value = e.text
        txt.position = self._vec(e.x, e.y)

        size_nm = self._to_nm(e.height)
        txt.attributes.size = Vector2.from_xy(size_nm, size_nm)
        txt.attributes.angle = e.rotation
        txt.attributes.multiline = "\n" in e.text
        if txt.attributes.multiline:
            txt.attributes.line_spacing = 1.0

        ap = e.attachment_point
        halign_map = {1: 0, 2: 1, 3: 2, 4: 0, 5: 1, 6: 2, 7: 0, 8: 1, 9: 2}
        valign_map = {1: 0, 2: 0, 3: 0, 4: 1, 5: 1, 6: 1, 7: 2, 8: 2, 9: 2}
        txt.attributes.horizontal_alignment = halign_map.get(ap, 1)
        txt.attributes.vertical_alignment = valign_map.get(ap, 1)

        self._apply_font_props(txt, e.font_props)
        items.append(txt)

    def _add_ellipse(self, items: list, e: DxfEllipse) -> None:
        major_len = math.hypot(e.major_x, e.major_y)
        if major_len < 1e-12:
            return

        result = ellipse_to_arcs(
            e.cx, e.cy, e.major_x, e.major_y, e.ratio,
            e.start_param, e.end_param,
        )

        for item in result:
            if isinstance(item, tuple) and len(item) == 5:
                cx, cy, r, sa, ea = item
                self._add_raw_arc(items, cx, cy, r, sa, ea)
            elif isinstance(item, tuple) and item[0] == "ELLIPSE_PTS":
                pts = item[1]
                for i in range(len(pts) - 1):
                    seg = BoardSegment()
                    seg.layer = self.target_layer
                    seg.attributes = self._make_attrs(self.line_width_nm)
                    seg.start = self._vec(pts[i][0], pts[i][1])
                    seg.end = self._vec(pts[i + 1][0], pts[i + 1][1])
                    items.append(seg)

    def _add_raw_arc(self, items: list, cx, cy, r, start_deg, end_deg) -> None:
        arc = BoardArc()
        arc.layer = self.target_layer
        arc.attributes = self._make_attrs(self.line_width_nm)

        cx_nm = self._to_nm(cx)
        cy_nm = self._to_nm(cy, True)
        r_nm = self._to_nm(r)

        sa_rad = math.radians(start_deg)
        ea_rad = math.radians(end_deg)
        sx = cx_nm + int(r_nm * math.cos(sa_rad))
        sy = cy_nm - int(r_nm * math.sin(sa_rad))
        ex = cx_nm + int(r_nm * math.cos(ea_rad))
        ey = cy_nm - int(r_nm * math.sin(ea_rad))

        mid = (sa_rad + ea_rad) / 2.0
        if end_deg < start_deg:
            mid += math.pi
        mx = cx_nm + int(r_nm * math.cos(mid))
        my = cy_nm - int(r_nm * math.sin(mid))

        arc.start = Vector2.from_xy(sx, sy)
        arc.end = Vector2.from_xy(ex, ey)
        arc.mid = Vector2.from_xy(mx, my)
        items.append(arc)

    def _add_spline(self, items: list, e: DxfSpline) -> None:
        source_pts = e.fit_points if e.fit_points else e.control_points
        if len(source_pts) < 2:
            return

        if e.fit_points:
            n = len(e.fit_points)
            pts: list[tuple[float, float]] = []
            segs = max(1, 50 // n)
            for i in range(n - 1):
                for j in range(segs):
                    t = j / segs
                    pts.append((
                        e.fit_points[i][0] + t * (e.fit_points[i + 1][0] - e.fit_points[i][0]),
                        e.fit_points[i][1] + t * (e.fit_points[i + 1][1] - e.fit_points[i][1]),
                    ))
            pts.append(e.fit_points[-1])
        else:
            pts = spline_to_polyline(e.control_points, e.knots, e.degree)

        for i in range(len(pts) - 1):
            seg = BoardSegment()
            seg.layer = self.target_layer
            seg.attributes = self._make_attrs(self.line_width_nm)
            seg.start = self._vec(pts[i][0], pts[i][1])
            seg.end = self._vec(pts[i + 1][0], pts[i + 1][1])
            items.append(seg)

        if e.flags & 1 and len(pts) > 2:
            seg = BoardSegment()
            seg.layer = self.target_layer
            seg.attributes = self._make_attrs(self.line_width_nm)
            seg.start = self._vec(pts[-1][0], pts[-1][1])
            seg.end = self._vec(pts[0][0], pts[0][1])
            items.append(seg)

    # ── Dimensions / Leaders ──────────────────────────────────

    def _add_dimension(self, items: list, e: DxfDimension) -> None:
        try:
            sx = self._to_nm(e.x_start)
            sy = self._to_nm(e.y_start, True)
            ex = self._to_nm(e.x_end)
            ey = self._to_nm(e.y_end, True)

            arrow_len = int(round(1.27 * 1_000_000))  # ~1.27mm in nm
            line_w = int(round(0.1 * 1_000_000))     # 0.1mm line thickness

            if getattr(e, 'dim_type', '') == "DIAMETRIC":
                dim = RadialDimension()
                center_x = (e.x_text + e.x_center) / 2.0
                center_y = (e.y_text + e.y_center) / 2.0
                radius = (e.measured / 2.0) if e.measured > 0 else 1.0
                sign = -1 if e.x_text < e.x_center else 1
                dim.center = self._vec(center_x, center_y)
                dim.radius_point = self._vec(center_x + sign * radius, center_y)
                dim.arrow_length = arrow_len
                dim.arrow_direction = 2  # DAD_OUTWARD
                dim.line_thickness = line_w
                dim.prefix = "\u2205"
            else:
                dx = abs(e.x_end - e.x_start)
                dy = abs(e.y_end - e.y_start)
                if dx < 1.0 or dy < 1.0:
                    dim = AlignedDimension()
                else:
                    dim = OrthogonalDimension()

                mx = (e.x_start + e.x_end) / 2.0
                my = (e.y_start + e.y_end) / 2.0
                h = self._to_nm(math.hypot(e.x_text - mx, e.y_text - my))
                if isinstance(dim, OrthogonalDimension):
                    if dx > dy:
                        h = self._to_nm(e.y_text - my)
                    else:
                        h = self._to_nm(e.x_text - mx)
                else:
                    if (e.x_end - e.x_start) * (e.y_text - my) - \
                       (e.y_end - e.y_start) * (e.x_text - mx) < 0:
                        h = -h
                dim.height = abs(h)
                dim.arrow_length = arrow_len
                dim.arrow_direction = 1 if h >= 0 else 2  # INWARD / OUTWARD
                dim.line_thickness = line_w

            dim.layer = self.dim_layer
            dim.start = Vector2.from_xy(sx, sy)
            dim.end = Vector2.from_xy(ex, ey)
            dim.text = Text()
            dim.text.position = self._vec(e.x_text, e.y_text)

            dim.unit = self.dim_units
            dim.unit_format = 2  # DUF_BARE_SUFFIX

            if hasattr(e, 'font_props') and e.font_props:
                self._apply_fontdim_props(dim.text, e.font_props)

            items.append(dim)
        except Exception as exc:
            print(f"DXF Import: Failed to add dimension: {exc}")

    def _add_leader(self, items: list, e: DxfLeader) -> None:
        try:
            dim = LeaderDimension()
            dim.layer = self.dim_layer

            arrow_len = int(round(1.27 * 1_000_000))  # ~1.27mm
            line_w = int(round(0.1 * 1_000_000))      # 0.1mm
            dim.arrow_length = arrow_len
            dim.arrow_direction = 2  # DAD_OUTWARD
            dim.line_thickness = line_w

            hooks = e.hooks
            if len(hooks) >= 2:
                tx = self._to_nm(hooks[0][0])
                ty = self._to_nm(hooks[0][1], True)
                ax = self._to_nm(hooks[-1][0])
                ay = self._to_nm(hooks[-1][1], True)
                dim.start = Vector2.from_xy(tx, ty)
                dim.end = Vector2.from_xy(ax, ay)
                dim.text = Text()
                dim.text.position = Vector2.from_xy(tx, ty)
            else:
                dim.end = self._vec(e.x_tip, e.y_tip)

            dim.override_text = "\n"
            dim.override_text_enabled = True

            items.append(dim)
        except Exception as exc:
            print(f"DXF Import: Failed to add leader: {exc}")

    # ── Font helpers ──────────────────────────────────────────

    @staticmethod
    def _apply_font_props(txt: BoardText, props: dict) -> None:
        if not props:
            return
        if props.get("name"):
            font_name = _FONT_NAME_MAP.get(props["name"], props["name"])
            txt.attributes.font_name = font_name
        if "bold" in props:
            txt.attributes.bold = bool(props["bold"])
        if "italic" in props:
            txt.attributes.italic = bool(props["italic"])

    @staticmethod
    def _apply_fontdim_props(text: Text, props: dict) -> None:
        if not props:
            return
        if props.get("name"):
            font_name = _FONT_NAME_MAP.get(props["name"], props["name"])
            text.attributes.font_name = font_name
        if "bold" in props:
            text.attributes.bold = bool(props["bold"])
        if "italic" in props:
            text.attributes.italic = bool(props["italic"])


# ── Settings dialog ─────────────────────────────────────────────

def _show_settings_dialog(
    dxf_path: str,
    entities: list,
    args: argparse.Namespace,
) -> dict | None:
    """Show wx import settings dialog. Returns settings dict or None if cancelled."""

    entity_types = sorted(set(e.entity_type for e in entities))
    entity_summary = ", ".join(entity_types)
    filename = os.path.basename(dxf_path)

    if not wx.GetApp():
        _ = wx.App(None)

    dlg = wx.Dialog(None, title="DXF Import Settings", size=(420, 360),
                    style=wx.DEFAULT_DIALOG_STYLE | wx.RESIZE_BORDER)
    panel = wx.Panel(dlg)

    sizer = wx.BoxSizer(wx.VERTICAL)

    # File info
    sizer.Add(wx.StaticText(panel, label=f"File: {filename}"), 0, wx.ALL, 10)
    sizer.Add(
        wx.StaticText(panel, label=f"Found: {len(entities)} entities ({entity_summary})"),
        0, wx.LEFT | wx.RIGHT | wx.BOTTOM, 10,
    )

    # Unit scale
    unit_choices = ["1 mm", "1 inch", "1 mil", "1 cm", "1 \u00b5m"]
    unit_values = [1.0, 25.4, 0.0254, 10.0, 0.001]

    unit_sizer = wx.BoxSizer(wx.HORIZONTAL)
    unit_sizer.Add(wx.StaticText(panel, label="1 DXF unit ="), 0,
                   wx.ALIGN_CENTER_VERTICAL | wx.LEFT, 10)
    unit_choice = wx.Choice(panel, choices=unit_choices)
    unit_choice.SetSelection(0)
    unit_sizer.Add(unit_choice, 0, wx.LEFT, 10)
    sizer.Add(unit_sizer, 0, wx.BOTTOM, 10)

    # Custom scale
    scale_sizer = wx.BoxSizer(wx.HORIZONTAL)
    scale_sizer.Add(wx.StaticText(panel, label="Scale factor (DXF units \u2192 mm):"), 0,
                    wx.ALIGN_CENTER_VERTICAL | wx.LEFT, 10)
    scale_text = wx.TextCtrl(panel, value=str(args.scale), size=(80, -1))
    scale_sizer.Add(scale_text, 0, wx.LEFT, 10)
    sizer.Add(scale_sizer, 0, wx.BOTTOM, 10)

    def on_unit_choice(event):
        idx = unit_choice.GetSelection()
        if 0 <= idx < len(unit_values):
            scale_text.SetValue(str(unit_values[idx]))

    unit_choice.Bind(wx.EVT_CHOICE, on_unit_choice)

    # Target layer
    layer_sizer = wx.BoxSizer(wx.HORIZONTAL)
    layer_sizer.Add(wx.StaticText(panel, label="Target layer:"), 0,
                    wx.ALIGN_CENTER_VERTICAL | wx.LEFT, 10)
    layer_choice = wx.Choice(panel, choices=LAYER_NAMES)
    try:
        layer_choice.SetSelection(LAYER_NAMES.index(args.layer))
    except ValueError:
        layer_choice.SetSelection(0)
    layer_sizer.Add(layer_choice, 0, wx.LEFT, 10)
    sizer.Add(layer_sizer, 0, wx.BOTTOM, 10)

    # Text layer
    text_sizer = wx.BoxSizer(wx.HORIZONTAL)
    text_sizer.Add(wx.StaticText(panel, label="Text/tables layer:"), 0,
                   wx.ALIGN_CENTER_VERTICAL | wx.LEFT, 10)
    text_layer_choice = wx.Choice(panel, choices=LAYER_NAMES)
    try:
        text_layer_choice.SetSelection(LAYER_NAMES.index(args.text_layer))
    except ValueError:
        text_layer_choice.SetSelection(LAYER_NAMES.index("Eco1.User"))
    text_sizer.Add(text_layer_choice, 0, wx.LEFT, 10)
    sizer.Add(text_sizer, 0, wx.BOTTOM, 10)

    # Line width
    width_sizer = wx.BoxSizer(wx.HORIZONTAL)
    width_sizer.Add(wx.StaticText(panel, label="Line width (mm):"), 0,
                    wx.ALIGN_CENTER_VERTICAL | wx.LEFT, 10)
    width_text = wx.TextCtrl(panel, value=str(args.line_width), size=(80, -1))
    width_sizer.Add(width_text, 0, wx.LEFT, 10)
    sizer.Add(width_sizer, 0, wx.BOTTOM, 10)

    # Native dimensions checkbox
    native_cb = wx.CheckBox(panel, label="Use KiCad native dimensions (DIMENSION/LEADER)")
    native_cb.SetValue(not args.no_native_dims)
    native_cb.SetToolTip(
        "When checked, DXF DIMENSION and LEADER entities are converted "
        "to KiCad native dimension objects. When unchecked, they remain "
        "as plain lines and text."
    )
    sizer.Add(native_cb, 0, wx.LEFT | wx.RIGHT, 10)

    # Dimension layer
    dim_layer_sizer = wx.BoxSizer(wx.HORIZONTAL)
    dim_layer_sizer.Add(wx.StaticText(panel, label="Dimension layer:"), 0,
                        wx.ALIGN_CENTER_VERTICAL | wx.LEFT, 10)
    dim_layer_choice = wx.Choice(panel, choices=LAYER_NAMES)
    try:
        dim_layer_choice.SetSelection(LAYER_NAMES.index(args.dim_layer))
    except ValueError:
        cmts_idx = LAYER_NAMES.index("Cmts.User") if "Cmts.User" in LAYER_NAMES else 0
        dim_layer_choice.SetSelection(cmts_idx)
    dim_layer_sizer.Add(dim_layer_choice, 0, wx.LEFT, 10)
    sizer.Add(dim_layer_sizer, 0, wx.BOTTOM, 10)

    # Buttons
    btn_sizer = wx.StdDialogButtonSizer()
    ok_btn = wx.Button(panel, wx.ID_OK, "Import")
    ok_btn.SetDefault()
    cancel_btn = wx.Button(panel, wx.ID_CANCEL, "Cancel")
    btn_sizer.AddButton(ok_btn)
    btn_sizer.AddButton(cancel_btn)
    btn_sizer.Realize()
    sizer.Add(btn_sizer, 0, wx.ALIGN_CENTER | wx.ALL, 10)

    panel.SetSizer(sizer)
    dlg.Fit()

    result = dlg.ShowModal()

    if result == wx.ID_OK:
        try:
            scale = float(scale_text.GetValue())
        except ValueError:
            scale = args.scale

        target_name = LAYER_NAMES[layer_choice.GetSelection()]
        text_name = LAYER_NAMES[text_layer_choice.GetSelection()]
        dim_target_name = LAYER_NAMES[dim_layer_choice.GetSelection()]

        try:
            line_w = max(0, float(width_text.GetValue()))
        except ValueError:
            line_w = args.line_width

        dim_units = [3, 1, 2, 3, 3]  # MM, INCH, MILS, MM, MM
        dim_unit = dim_units[unit_choice.GetSelection()]

        dlg.Destroy()
        return {
            "scale": scale,
            "target_layer": _layer_id(target_name),
            "text_layer": _layer_id(text_name),
            "line_width_mm": line_w,
            "native_dims": native_cb.GetValue(),
            "dim_layer": _layer_id(dim_target_name),
            "dim_units": dim_unit,
            "layer_name": target_name,
        }

    dlg.Destroy()
    return None


# ── CLI / IPC plugin entry point ───────────────────────────────

def main():
    """Main entry point: parse args, connect to KiCad, import DXF."""
    try:
        _main()
    except Exception as e:
        msg = f"DXF Import Plugin Error:\n\n{e}\n\n{traceback.format_exc()}"
        print(msg)
        try:
            if not wx.GetApp():
                _ = wx.App(None)
            wx.MessageBox(msg, "DXF Import Error", wx.OK | wx.ICON_ERROR)
        except Exception:
            pass
        sys.exit(1)


def _main():
    """Internal main with error handling wrapper."""
    parser = argparse.ArgumentParser(
        description="Import DXF files into a running KiCad instance via IPC API.",
    )
    parser.add_argument("dxf_file", nargs="?", help="Path to the DXF file to import")
    parser.add_argument(
        "--layer", default="Edge.Cuts", choices=LAYER_NAMES,
        help="Target layer for shapes",
    )
    parser.add_argument(
        "--text-layer", default="Eco1.User", choices=LAYER_NAMES,
        help="Layer for text/tables",
    )
    parser.add_argument(
        "--dim-layer", default="Cmts.User", choices=LAYER_NAMES,
        help="Layer for dimensions",
    )
    parser.add_argument(
        "--scale", type=float, default=1.0,
        help="Scale factor (DXF units to mm)",
    )
    parser.add_argument(
        "--line-width", type=float, default=0.1,
        help="Line width in mm",
    )
    parser.add_argument(
        "--no-native-dims", action="store_true",
        help="Convert DIMENSION/LEADER to plain shapes",
    )
    parser.add_argument(
        "--no-dialog", action="store_true",
        help="Skip the settings dialog (use command-line args only)",
    )
    parser.add_argument(
        "--socket", default=None,
        help="IPC API socket path (default: KICAD_API_SOCKET env)",
    )
    parser.add_argument(
        "--token", default=None,
        help="IPC API token (default: KICAD_API_TOKEN env)",
    )
    parser.add_argument(
        "--timeout", type=int, default=5000,
        help="IPC API timeout in ms",
    )
    args = parser.parse_args()

    # File selection via wx if no path given
    dxf_path = args.dxf_file
    if not dxf_path:
        if not wx.GetApp():
            _ = wx.App(None)
        dlg = wx.FileDialog(
            None, "Select DXF File",
            wildcard="DXF files (*.dxf)|*.dxf|All files (*.*)|*.*",
            style=wx.FD_OPEN | wx.FD_FILE_MUST_EXIST,
        )
        if dlg.ShowModal() == wx.ID_OK:
            dxf_path = dlg.GetPath()
        dlg.Destroy()
        if not dxf_path:
            print("No file selected.")
            return

    if not os.path.isfile(dxf_path):
        print(f"File not found: {dxf_path}")
        return

    # Connect to KiCad via IPC
    print("Connecting to KiCad via IPC API...")
    try:
        kicad = kipy.KiCad(
            socket_path=args.socket,
            kicad_token=args.token,
            timeout_ms=args.timeout,
        )
        kicad.ping()
    except errors.ConnectionError:
        print("Failed to connect to KiCad. Make sure KiCad is running.")
        sys.exit(1)
    except Exception as e:
        print(f"Failed to connect to KiCad: {e}")
        sys.exit(1)

    print(f"Connected to KiCad {kicad.get_version()}")

    try:
        board = kicad.get_board()
    except errors.ApiError:
        print("No board is open. Please open a board in Pcbnew first.")
        sys.exit(1)

    if board is None:
        print("No board is open. Please open a board in Pcbnew first.")
        sys.exit(1)

    # Parse DXF
    print(f"Parsing DXF file: {dxf_path}")
    reader = DxfReader()
    try:
        entities = reader.read(dxf_path)
    except Exception as e:
        print(f"Failed to parse DXF file: {e}")
        sys.exit(1)

    if not entities:
        print("The DXF file contains no supported entities.")
        return

    # Show settings dialog unless explicitly skipped
    if args.no_dialog:
        settings = {
            "scale": args.scale,
            "target_layer": _layer_id(args.layer),
            "text_layer": _layer_id(args.text_layer),
            "line_width_mm": args.line_width,
            "native_dims": not args.no_native_dims,
            "dim_layer": _layer_id(args.dim_layer),
            "dim_units": 3,
            "layer_name": args.layer,
        }
    else:
        settings = _show_settings_dialog(dxf_path, entities, args)
        if settings is None:
            print("Import cancelled.")
            return

    entity_types = sorted(set(e.entity_type for e in entities))
    print(f"Found {len(entities)} entities: {', '.join(entity_types)}")

    unit_scale = settings["scale"] * 1_000_000.0
    line_width_nm = int(round(settings["line_width_mm"] * 1_000_000))

    importer = DxfImport(
        board=board,
        unit_scale=unit_scale,
        target_layer=settings["target_layer"],
        text_layer=settings["text_layer"],
        line_width_nm=line_width_nm,
        native_dims=settings["native_dims"],
        dim_layer=settings["dim_layer"],
        dim_units=settings["dim_units"],
    )

    count = importer.import_entities(entities)

    print(f"\nSuccessfully imported {count} entities from DXF.")
    print(f"  File: {os.path.basename(dxf_path)}")
    print(f"  Layer: {settings['layer_name']}")


if __name__ == "__main__":
    main()
