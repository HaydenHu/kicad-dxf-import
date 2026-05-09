"""
KiCad DXF Import Plugin for KiCad 10.0+

Imports DXF files (AutoCAD R12-R2018+) into the current Pcbnew board.
Supported entities: LINE, CIRCLE, ARC, LWPOLYLINE, POLYLINE(2D),
TEXT, MTEXT, ELLIPSE, SPLINE.

Entities are converted to PCB_SHAPE / PCB_TEXT elements on the
selected KiCad layer.

Usage:
    Place dxf_import.py, dxf_reader.py, and icon.png in
    KiCad's scripting plugins folder.
    In Pcbnew: Tools > External Plugins > Import DXF
"""

from __future__ import annotations

import math
import os
import sys
from typing import Any

import pcbnew
import wx

from .dxf_reader import (
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
# Helper for font-aware text cleaning
_clean_mtext_ex = DxfReader._clean_mtext_ex


# ── Constants ───────────────────────────────────────────────────

LAYER_CHOICES = [
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


class DxfImportPlugin(pcbnew.ActionPlugin):
    """Action plugin for importing DXF files into Pcbnew."""

    def __init__(self):
        super().__init__()
        self.name = "Import DXF 1.0"
        self.category = "Import"
        self.description = (
            "Import DXF files (AutoCAD R12-R2018+) into the current PCB. "
            "Supports lines, circles, arcs, polylines, splines, ellipses, and text."
        )
        self.show_toolbar_button = True
        plugin_dir = os.path.dirname(os.path.abspath(__file__))
        self.icon_file_name = os.path.join(plugin_dir, "icon.png")

        self.board = None
        self.unit_scale = 1_000_000.0
        self.target_layer_id: int = pcbnew.Edge_Cuts
        self.dxf_file: str = ""
        self._line_width: float = 0.1
        self._text_layer_id: int = pcbnew.Eco1_User
        self._native_dims: bool = True  # Use KiCad native dimensions by default
        self._dim_layer_id: int = pcbnew.Dwgs_User  # Layer for native dimensions
        self._leaders_seen: int = 0

    def Run(self) -> None:
        """Entry point called by KiCad when the plugin action is invoked."""
        self.board = pcbnew.GetBoard()
        if not self.board:
            self._show_error("No board is open. Please open a board in Pcbnew first.")
            return

        # Step 1: Select DXF file
        if not self._select_file():
            return

        # Step 2: Parse the DXF file
        reader = DxfReader()
        try:
            entities = reader.read(self.dxf_file)
        except Exception as e:
            self._show_error(f"Failed to parse DXF file:\n{e}")
            return

        if not entities:
            self._show_info("The DXF file contains no supported entities.")
            return

        # Step 3: Show import settings dialog
        if not self._show_settings_dialog(entities):
            return  # user cancelled

        # Step 4: Collect dim/leader text to skip duplicate MTEXT imports
        dim_texts: set[str] = set()
        if self._native_dims:
            for e in entities:
                if e.entity_type in ("DIMENSION", "LEADER"):
                    t = getattr(e, 'text', '').strip()
                    if t:
                        dim_texts.add(t)
                        # Also add individual lines from multi-line text
                        for line in t.split('\n'):
                            dim_texts.add(line.strip())

        count = self._import_entities(entities, dim_texts)

        # Debug: check entity types
        type_counts = {}
        for e in entities:
            t = e.entity_type
            type_counts[t] = type_counts.get(t, 0) + 1
        print(f"DXF Import: entity types: {type_counts}")

        if count == 0:
            self._show_info("The DXF file contains no supported entities.")
            return

        # Step 5: Refresh the board view
        pcbnew.Refresh()

        dim_info = ""
        if hasattr(self, '_dimensions_added'):
            dim_info += f"\nDimensions: {self._dimensions_added}"
        dim_info += f"\nLeaders seen: {self._leaders_seen}"
        if hasattr(self, '_leaders_added'):
            dim_info += f", added: {self._leaders_added}"
        if hasattr(self, '_unknown_types') and self._unknown_types:
            dim_info += f"\nUnhandled types:\n{self._unknown_types}"
        if hasattr(self, '_leader_hooks_debug') and self._leader_hooks_debug:
            dim_info += f"\nLeader debug:\n{self._leader_hooks_debug[:300]}"
        leader_errs = getattr(self, '_leader_errors', '')
        if leader_errs:
            dim_info += f"\nLeader errors:\n{leader_errs[:400]}"
        if hasattr(self, '_dim_errors') and self._dim_errors:
            dim_info += f"\nErrors:\n{self._dim_errors[:500]}"

        self._show_info(
            f"Successfully imported {count} entities from DXF.\n"
            f"File: {os.path.basename(self.dxf_file)}\n"
            f"Layer: {self._layer_name_from_id(self.target_layer_id)}\n"
            f"Types: {type_counts}{dim_info}"
        )

    # ── Dialogs ──────────────────────────────────────────────

    def _select_file(self) -> bool:
        """Show a file open dialog for DXF selection."""
        dlg = wx.FileDialog(
            None,
            "Select DXF File",
            wildcard="DXF files (*.dxf)|*.dxf|All files (*.*)|*.*",
            style=wx.FD_OPEN | wx.FD_FILE_MUST_EXIST,
        )
        if dlg.ShowModal() == wx.ID_OK:
            self.dxf_file = dlg.GetPath()
            dlg.Destroy()
            return True
        dlg.Destroy()
        return False

    def _show_settings_dialog(self, entities: list[DxfEntity]) -> bool:
        """Show import settings: unit scale and target layer."""
        entity_types = set(e.entity_type for e in entities)
        entity_summary = ", ".join(sorted(entity_types))
        filename = os.path.basename(self.dxf_file)

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
        scale_text = wx.TextCtrl(panel, value="1.0", size=(80, -1))
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
        layer_choice = wx.Choice(panel, choices=LAYER_CHOICES)
        layer_choice.SetSelection(0)  # Edge.Cuts
        layer_sizer.Add(layer_choice, 0, wx.LEFT, 10)
        sizer.Add(layer_sizer, 0, wx.BOTTOM, 10)

        # Text layer
        text_sizer = wx.BoxSizer(wx.HORIZONTAL)
        text_sizer.Add(wx.StaticText(panel, label="Text/tables layer:"), 0,
                       wx.ALIGN_CENTER_VERTICAL | wx.LEFT, 10)
        text_layer_choice = wx.Choice(panel, choices=LAYER_CHOICES)
        eco_idx = LAYER_CHOICES.index("Eco1.User")
        text_layer_choice.SetSelection(eco_idx)
        text_sizer.Add(text_layer_choice, 0, wx.LEFT, 10)
        sizer.Add(text_sizer, 0, wx.BOTTOM, 10)

        # Line width
        width_sizer = wx.BoxSizer(wx.HORIZONTAL)
        width_sizer.Add(wx.StaticText(panel, label="Line width (mm):"), 0,
                        wx.ALIGN_CENTER_VERTICAL | wx.LEFT, 10)
        width_text = wx.TextCtrl(panel, value="0.1", size=(80, -1))
        width_sizer.Add(width_text, 0, wx.LEFT, 10)
        sizer.Add(width_sizer, 0, wx.BOTTOM, 10)

        # Native dimensions checkbox
        native_cb = wx.CheckBox(panel, label="Use KiCad native dimensions (DIMENSION/LEADER)")
        native_cb.SetValue(True)
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
        dim_layer_choice = wx.Choice(panel, choices=LAYER_CHOICES)
        # Default: Dwgs.User (User.Drawings)
        dwgs_idx = LAYER_CHOICES.index("Dwgs.User") if "Dwgs.User" in LAYER_CHOICES else 0
        dim_layer_choice.SetSelection(dwgs_idx)
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
                self.unit_scale = float(scale_text.GetValue()) * 1_000_000.0
            except ValueError:
                self.unit_scale = 1_000_000.0

            target_name = LAYER_CHOICES[layer_choice.GetSelection()]
            self.target_layer_id = self._get_layer_id(target_name)

            text_name = LAYER_CHOICES[text_layer_choice.GetSelection()]
            self._text_layer_id = self._get_layer_id(text_name)

            try:
                self._line_width = max(0, float(width_text.GetValue()))
            except ValueError:
                self._line_width = 0.1

            self._native_dims = native_cb.GetValue()

            dim_target_name = LAYER_CHOICES[dim_layer_choice.GetSelection()]
            self._dim_layer_id = self._get_layer_id(dim_target_name)

            # Map unit choice to KiCad DIM units
            dim_units = [pcbnew.DIM_UNITS_MODE_MM, pcbnew.DIM_UNITS_MODE_INCH,
                         pcbnew.DIM_UNITS_MODE_MILS, pcbnew.DIM_UNITS_MODE_MM,
                         pcbnew.DIM_UNITS_MODE_MM]
            self._dim_units_mode = dim_units[unit_choice.GetSelection()]

            dlg.Destroy()
            return True

        dlg.Destroy()
        return False

    # ── Entity to board conversion ───────────────────────────

    def _import_entities(self, entities: list[DxfEntity], dim_texts: set = None) -> int:
        count = 0
        w_nm = int(self._line_width * 1_000_000)

        for entity in entities:
            try:
                # Skip MTEXT whose text matches dimension/leader text when using native dims
                if dim_texts and entity.entity_type in ("MTEXT", "TEXT"):
                    txt = getattr(entity, 'text', '').strip()
                    if txt and (txt in dim_texts or any(line.strip() in dim_texts for line in txt.split('\n'))):
                        continue

                added = False
                if isinstance(entity, DxfLine):
                    added = self._add_line(entity, w_nm)
                elif isinstance(entity, DxfCircle):
                    added = self._add_circle(entity, w_nm)
                elif isinstance(entity, DxfArc):
                    added = self._add_arc(entity, w_nm)
                elif isinstance(entity, DxfLwPolyline):
                    added = self._add_lwpolyline(entity, w_nm)
                elif isinstance(entity, DxfPolyline):
                    added = self._add_polyline(entity, w_nm)
                elif isinstance(entity, DxfText):
                    added = self._add_text(entity)
                elif isinstance(entity, DxfMText):
                    added = self._add_mtext(entity)
                elif isinstance(entity, DxfEllipse):
                    added = self._add_ellipse(entity, w_nm)
                elif isinstance(entity, DxfSpline):
                    added = self._add_spline(entity, w_nm)
                elif entity.entity_type == "DIMENSION":
                    if self._native_dims:
                        added = self._add_dimension(entity, w_nm)
                    # Skip DIMENSION entity entirely (text is inline, lines are separate)
                elif entity.entity_type == "LEADER":
                    if self._native_dims:
                        added = self._add_leader(entity, w_nm)
                    self._leaders_seen = getattr(self, '_leaders_seen', 0) + 1
                    # Skip LEADER entity entirely (its text follows as MTEXT)
                else:
                    # Unknown entity type, count it anyway to track coverage
                    self._unknown_types = getattr(self, '_unknown_types', "")
                    self._unknown_types += f"  {entity.entity_type}={entity.__class__.__name__}\n"

                if added:
                    count += 1

            except Exception as e:
                print(f"DXF Import: Failed to add {entity.entity_type}: {e}")

        return count

    def _to_board_coord(self, dxf_val: float) -> int:
        return int(round(dxf_val * self.unit_scale))

    def _make_shape(self, shape_type: int, layer: int, width_nm: int) -> pcbnew.PCB_SHAPE:
        shape = pcbnew.PCB_SHAPE(self.board)
        shape.SetLayer(layer)
        shape.SetShape(shape_type)
        if width_nm > 0:
            shape.SetWidth(width_nm)
        return shape

    def _add_line(self, e: DxfLine, width_nm: int) -> bool:
        shape = self._make_shape(pcbnew.SHAPE_T_SEGMENT, self.target_layer_id, width_nm)
        shape.SetStart(pcbnew.VECTOR2I(self._to_board_coord(e.x1), self._to_board_coord(e.y1)))
        shape.SetEnd(pcbnew.VECTOR2I(self._to_board_coord(e.x2), self._to_board_coord(e.y2)))
        self.board.Add(shape)
        return True

    def _add_circle(self, e: DxfCircle, width_nm: int) -> bool:
        shape = self._make_shape(pcbnew.SHAPE_T_CIRCLE, self.target_layer_id, width_nm)
        shape.SetCenter(pcbnew.VECTOR2I(self._to_board_coord(e.cx), self._to_board_coord(e.cy)))
        shape.SetEnd(pcbnew.VECTOR2I(
            self._to_board_coord(e.cx + e.radius), self._to_board_coord(e.cy),
        ))
        self.board.Add(shape)
        return True

    def _add_arc(self, e: DxfArc, width_nm: int) -> bool:
        shape = self._make_shape(pcbnew.SHAPE_T_ARC, self.target_layer_id, width_nm)
        cx_nm = self._to_board_coord(e.cx)
        cy_nm = self._to_board_coord(e.cy)
        r_nm = self._to_board_coord(e.radius)

        sa_rad = math.radians(e.start_angle)
        ea_rad = math.radians(e.end_angle)
        sx = cx_nm + int(r_nm * math.cos(sa_rad))
        sy = cy_nm + int(r_nm * math.sin(sa_rad))
        ex = cx_nm + int(r_nm * math.cos(ea_rad))
        ey = cy_nm + int(r_nm * math.sin(ea_rad))

        mid = (sa_rad + ea_rad) / 2.0
        if e.end_angle < e.start_angle:
            mid += math.pi
        mx = cx_nm + int(r_nm * math.cos(mid))
        my = cy_nm + int(r_nm * math.sin(mid))

        shape.SetCenter(pcbnew.VECTOR2I(cx_nm, cy_nm))
        shape.SetArcGeometry(
            pcbnew.VECTOR2I(sx, sy),
            pcbnew.VECTOR2I(mx, my),
            pcbnew.VECTOR2I(ex, ey),
        )
        self.board.Add(shape)
        return True

    def _add_lwpolyline(self, e: DxfLwPolyline, width_nm: int) -> bool:
        if len(e.points) < 2:
            return False
        pts = polyline_points_from_bulges(e.points, e.bulges)
        self._add_polyline_points(pts, e.closed, width_nm)
        return True

    def _add_polyline(self, e: DxfPolyline, width_nm: int) -> bool:
        if len(e.points) < 2:
            return False
        pts = polyline_points_from_bulges(e.points, e.bulges)
        self._add_polyline_points(pts, e.closed, width_nm)
        return True

    def _add_polyline_points(self, pts, closed, width_nm):
        for i in range(len(pts) - 1):
            shape = self._make_shape(pcbnew.SHAPE_T_SEGMENT, self.target_layer_id, width_nm)
            shape.SetStart(pcbnew.VECTOR2I(
                self._to_board_coord(pts[i][0]), self._to_board_coord(pts[i][1]),
            ))
            shape.SetEnd(pcbnew.VECTOR2I(
                self._to_board_coord(pts[i + 1][0]), self._to_board_coord(pts[i + 1][1]),
            ))
            self.board.Add(shape)

        if closed and len(pts) > 2:
            shape = self._make_shape(pcbnew.SHAPE_T_SEGMENT, self.target_layer_id, width_nm)
            shape.SetStart(pcbnew.VECTOR2I(
                self._to_board_coord(pts[-1][0]), self._to_board_coord(pts[-1][1]),
            ))
            shape.SetEnd(pcbnew.VECTOR2I(
                self._to_board_coord(pts[0][0]), self._to_board_coord(pts[0][1]),
            ))
            self.board.Add(shape)

    def _add_text(self, e: DxfText) -> bool:
        if not e.text.strip():
            return False

        txt = pcbnew.PCB_TEXT(self.board)
        txt.SetLayer(self._text_layer_id)
        txt.SetText(e.text)
        txt.SetTextPos(pcbnew.VECTOR2I(self._to_board_coord(e.x), self._to_board_coord(e.y)))
        size = self._to_board_coord(e.height)
        txt.SetTextSize(pcbnew.VECTOR2I(size, size))
        txt.SetTextAngle(pcbnew.EDA_ANGLE(e.rotation, pcbnew.DEGREES_T))
        if "\n" in e.text:
            txt.SetMultilineAllowed(True)

        txt.SetHorizJustify(
            pcbnew.GR_TEXT_H_ALIGN_CENTER if e.halign == 1 else
            pcbnew.GR_TEXT_H_ALIGN_RIGHT if e.halign == 2 else
            pcbnew.GR_TEXT_H_ALIGN_LEFT
        )
        txt.SetVertJustify(
            pcbnew.GR_TEXT_V_ALIGN_CENTER if e.valign == 2 else
            pcbnew.GR_TEXT_V_ALIGN_TOP if e.valign == 3 else
            pcbnew.GR_TEXT_V_ALIGN_BOTTOM
        )
        self._apply_font_props(txt, e.font_props)
        self.board.Add(txt)
        return True

    def _add_mtext(self, e: DxfMText) -> bool:
        if not e.text.strip():
            return False

        txt = pcbnew.PCB_TEXT(self.board)
        txt.SetLayer(self._text_layer_id)
        txt.SetText(e.text)
        txt.SetTextPos(pcbnew.VECTOR2I(self._to_board_coord(e.x), self._to_board_coord(e.y)))
        size = self._to_board_coord(e.height)
        txt.SetTextSize(pcbnew.VECTOR2I(size, size))
        txt.SetTextAngle(pcbnew.EDA_ANGLE(e.rotation, pcbnew.DEGREES_T))
        if "\n" in e.text:
            txt.SetMultilineAllowed(True)

        ap = e.attachment_point
        txt.SetHorizJustify(
            pcbnew.GR_TEXT_H_ALIGN_LEFT if ap in (1, 4, 7) else
            pcbnew.GR_TEXT_H_ALIGN_RIGHT if ap in (3, 6, 9) else
            pcbnew.GR_TEXT_H_ALIGN_CENTER
        )
        txt.SetVertJustify(
            pcbnew.GR_TEXT_V_ALIGN_TOP if ap in (1, 2, 3) else
            pcbnew.GR_TEXT_V_ALIGN_BOTTOM if ap in (7, 8, 9) else
            pcbnew.GR_TEXT_V_ALIGN_CENTER
        )
        self._apply_font_props(txt, e.font_props)
        self.board.Add(txt)
        return True

    def _add_ellipse(self, e: DxfEllipse, width_nm: int) -> bool:
        major_len = math.hypot(e.major_x, e.major_y)
        if major_len < 1e-12:
            return False

        result = ellipse_to_arcs(
            e.cx, e.cy, e.major_x, e.major_y, e.ratio,
            e.start_param, e.end_param,
        )

        for item in result:
            if isinstance(item, tuple) and len(item) == 5:
                cx, cy, r, sa, ea = item
                self._add_raw_arc(cx, cy, r, sa, ea, width_nm)
            elif isinstance(item, tuple) and item[0] == "ELLIPSE_PTS":
                pts = item[1]
                for i in range(len(pts) - 1):
                    shape = self._make_shape(pcbnew.SHAPE_T_SEGMENT, self.target_layer_id, width_nm)
                    shape.SetStart(pcbnew.VECTOR2I(
                        self._to_board_coord(pts[i][0]), self._to_board_coord(pts[i][1]),
                    ))
                    shape.SetEnd(pcbnew.VECTOR2I(
                        self._to_board_coord(pts[i + 1][0]), self._to_board_coord(pts[i + 1][1]),
                    ))
                    self.board.Add(shape)

        return True

    def _add_raw_arc(self, cx, cy, r, start_deg, end_deg, width_nm):
        shape = self._make_shape(pcbnew.SHAPE_T_ARC, self.target_layer_id, width_nm)
        cx_nm = self._to_board_coord(cx)
        cy_nm = self._to_board_coord(cy)
        r_nm = self._to_board_coord(r)

        sa_rad = math.radians(start_deg)
        ea_rad = math.radians(end_deg)
        sx = cx_nm + int(r_nm * math.cos(sa_rad))
        sy = cy_nm + int(r_nm * math.sin(sa_rad))
        ex = cx_nm + int(r_nm * math.cos(ea_rad))
        ey = cy_nm + int(r_nm * math.sin(ea_rad))

        mid = (sa_rad + ea_rad) / 2.0
        if end_deg < start_deg:
            mid += math.pi
        mx = cx_nm + int(r_nm * math.cos(mid))
        my = cy_nm + int(r_nm * math.sin(mid))

        shape.SetCenter(pcbnew.VECTOR2I(cx_nm, cy_nm))
        shape.SetArcGeometry(pcbnew.VECTOR2I(sx, sy), pcbnew.VECTOR2I(mx, my), pcbnew.VECTOR2I(ex, ey))
        self.board.Add(shape)

    def _add_spline(self, e: DxfSpline, width_nm: int) -> bool:
        source_pts = e.fit_points if e.fit_points else e.control_points
        if len(source_pts) < 2:
            return False

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
            shape = self._make_shape(pcbnew.SHAPE_T_SEGMENT, self.target_layer_id, width_nm)
            shape.SetStart(pcbnew.VECTOR2I(
                self._to_board_coord(pts[i][0]), self._to_board_coord(pts[i][1]),
            ))
            shape.SetEnd(pcbnew.VECTOR2I(
                self._to_board_coord(pts[i + 1][0]), self._to_board_coord(pts[i + 1][1]),
            ))
            self.board.Add(shape)

        if e.flags & 1 and len(pts) > 2:
            shape = self._make_shape(pcbnew.SHAPE_T_SEGMENT, self.target_layer_id, width_nm)
            shape.SetStart(pcbnew.VECTOR2I(
                self._to_board_coord(pts[-1][0]), self._to_board_coord(pts[-1][1]),
            ))
            shape.SetEnd(pcbnew.VECTOR2I(
                self._to_board_coord(pts[0][0]), self._to_board_coord(pts[0][1]),
            ))
            self.board.Add(shape)

        return True

    # ── Dimension / Leader ───────────────────────────────────

    def _add_dimension(self, e, width_nm: int) -> bool:
        """Add DXF DIMENSION as KiCad native dimension element."""
        try:
            sx = self._to_board_coord(e.x_start)
            sy = self._to_board_coord(e.y_start)
            ex = self._to_board_coord(e.x_end)
            ey = self._to_board_coord(e.y_end)

            if hasattr(e, 'dim_type') and e.dim_type == "DIAMETRIC":
                dim = pcbnew.PCB_DIM_RADIAL(self.board)
            else:
                dim = pcbnew.PCB_DIM_ALIGNED(self.board)
                mx = (e.x_start + e.x_end) / 2.0
                my = (e.y_start + e.y_end) / 2.0
                h = int(math.hypot(
                    self._to_board_coord(e.x_text - mx),
                    self._to_board_coord(e.y_text - my),
                ))
                dx = e.x_end - e.x_start
                dy = e.y_end - e.y_start
                cross = dx * (e.y_text - my) - dy * (e.x_text - mx)
                if cross < 0:
                    h = -h
                dim.SetHeight(h)

            dim.SetStart(pcbnew.VECTOR2I(sx, sy))
            dim.SetEnd(pcbnew.VECTOR2I(ex, ey))
            dim.SetTextPos(pcbnew.VECTOR2I(
                self._to_board_coord(e.x_text),
                self._to_board_coord(e.y_text),
            ))
            dim.SetLayer(self._dim_layer_id)
            dim.SetUnitsMode(self._dim_units_mode)
            dim.SetUnitsFormat(pcbnew.DIM_UNITS_FORMAT_NO_SUFFIX)
            if e.text:
                dim.SetOverrideText(e.text)
                dim.SetOverrideTextEnabled(True)
            self._apply_font_props(dim, getattr(e, 'font_props', {}))
            self.board.Add(dim)
            self._dimensions_added = getattr(self, '_dimensions_added', 0) + 1
            return True
        except Exception as ex:
            import traceback
            msg = f"Dimension add failed:\n{traceback.format_exc()}"
            self._dim_errors = getattr(self, '_dim_errors', "") + msg + "\n---\n"
            return False

    def _add_leader(self, e, width_nm: int) -> bool:
        """Add DXF LEADER as KiCad PCB_DIM_LEADER element."""
        try:
            dim = pcbnew.PCB_DIM_LEADER(self.board)
            dim.SetLayer(self._dim_layer_id)

            hooks = getattr(e, 'hooks', [])
            if len(hooks) >= 2:
                # hooks[0] = text anchor, hooks[-1] = arrow tip
                # KiCad LEADER: SetStart=text, SetEnd=arrow, SetTextPos=text
                tx = self._to_board_coord(hooks[0][0])
                ty = self._to_board_coord(hooks[0][1])
                ax = self._to_board_coord(hooks[-1][0])
                ay = self._to_board_coord(hooks[-1][1])
                dim.SetStart(pcbnew.VECTOR2I(tx, ty))
                dim.SetEnd(pcbnew.VECTOR2I(ax, ay))
                dim.SetTextPos(pcbnew.VECTOR2I(tx, ty))
                # Store text pos for debug
                self._leader_tx = f"textPos={dim.GetTextPos()}"
            else:
                self._leader_hooks_debug = getattr(self, '_leader_hooks_debug', "") + \
                    f"NO HOOKS: hooks={hooks} tip=({e.x_tip},{e.y_tip})\n"
                dim.SetEnd(pcbnew.VECTOR2I(
                    self._to_board_coord(e.x_tip),
                    self._to_board_coord(e.y_tip),
                ))
            # Enable text override so we can set text later
            dim.SetOverrideText("\n")

            self.board.Add(dim)
            self._leaders_added = getattr(self, '_leaders_added', 0) + 1
            return True
        except Exception as ex:
            import traceback
            msg = f"Leader add failed:\n{traceback.format_exc()}"
            self._dim_errors = getattr(self, '_dim_errors', "") + msg + "\n---\n"
            return False

    # ── Font helper ──────────────────────────────────────────

    # AutoCAD Color Index (ACI) to RGB approximation
    # 0=BYBLOCK, 256=BYLAYER, 7=white/black, rest are standard colors
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

    # DXF font name -> system font name mapping
    _FONT_NAME_MAP = {
        "SimSun": "宋体",
        "SimHei": "黑体",
        "KaiTi": "楷体",
        "FangSong": "仿宋",
        "Microsoft YaHei": "微软雅黑",
    }

    @staticmethod
    def _apply_font_props(txt: pcbnew.PCB_TEXT, props: dict) -> None:
        """Apply extracted font properties to PCB_TEXT."""
        if not props:
            return
        if props.get("name"):
            try:
                font_name = props["name"]
                # Map DXF font names to system font names
                font_name = DxfImportPlugin._FONT_NAME_MAP.get(font_name, font_name)
                txt.SetUnresolvedFontName(font_name)
                emb = txt.GetEmbeddedFonts()
                txt.ResolveFont(emb)
            except Exception:
                pass
        if "bold" in props:
            try:
                txt.SetBold(props["bold"])
            except Exception:
                pass
        if "italic" in props:
            try:
                txt.SetItalic(props["italic"])
            except Exception:
                pass
        if "color" in props:
            try:
                ci = props["color"]
                if ci > 0 and ci != 256 and ci != 7:
                    rgb = DxfImportPlugin._ACI_COLORS.get(ci, (255, 255, 255))
                    c = pcbnew.COLOR4D()
                    c.FromCSSRGBA(rgb[0], rgb[1], rgb[2], 1.0)
                    txt.SetTextColor(c)
            except Exception:
                pass

    # ── Layer helpers ─────────────────────────────────────────

    @staticmethod
    def _get_layer_id(name: str) -> int:
        mapping = {
            "F.Cu": pcbnew.F_Cu,
            "B.Cu": pcbnew.B_Cu,
            "F.SilkS": pcbnew.F_SilkS,
            "B.SilkS": pcbnew.B_SilkS,
            "F.Mask": pcbnew.F_Mask,
            "B.Mask": pcbnew.B_Mask,
            "F.Paste": pcbnew.F_Paste,
            "B.Paste": pcbnew.B_Paste,
            "F.Adhes": pcbnew.F_Adhes,
            "B.Adhes": pcbnew.B_Adhes,
            "Edge.Cuts": pcbnew.Edge_Cuts,
            "F.CrtYd": pcbnew.F_CrtYd,
            "B.CrtYd": pcbnew.B_CrtYd,
            "F.Fab": pcbnew.F_Fab,
            "B.Fab": pcbnew.B_Fab,
            "Eco1.User": pcbnew.Eco1_User,
            "Eco2.User": pcbnew.Eco2_User,
            "Cmts.User": pcbnew.Cmts_User,
            "Dwgs.User": pcbnew.Dwgs_User,
            "User.1": pcbnew.User_1,
            "User.2": pcbnew.User_2,
            "User.3": pcbnew.User_3,
            "User.4": pcbnew.User_4,
            "User.5": pcbnew.User_5,
            "User.6": pcbnew.User_6,
            "User.7": pcbnew.User_7,
            "User.8": pcbnew.User_8,
            "User.9": pcbnew.User_9,
        }
        return mapping.get(name, pcbnew.Edge_Cuts)

    @staticmethod
    def _layer_name_from_id(layer_id: int) -> str:
        mapping_reverse = {
            pcbnew.F_Cu: "F.Cu",
            pcbnew.B_Cu: "B.Cu",
            pcbnew.F_SilkS: "F.SilkS",
            pcbnew.B_SilkS: "B.SilkS",
            pcbnew.F_Mask: "F.Mask",
            pcbnew.B_Mask: "B.Mask",
            pcbnew.F_Paste: "F.Paste",
            pcbnew.B_Paste: "B.Paste",
            pcbnew.F_Adhes: "F.Adhes",
            pcbnew.B_Adhes: "B.Adhes",
            pcbnew.Edge_Cuts: "Edge.Cuts",
            pcbnew.F_CrtYd: "F.CrtYd",
            pcbnew.B_CrtYd: "B.CrtYd",
            pcbnew.F_Fab: "F.Fab",
            pcbnew.B_Fab: "B.Fab",
            pcbnew.Eco1_User: "Eco1.User",
            pcbnew.Eco2_User: "Eco2.User",
            pcbnew.Cmts_User: "Cmts.User",
            pcbnew.Dwgs_User: "Dwgs.User",
            pcbnew.User_1: "User.1",
            pcbnew.User_2: "User.2",
            pcbnew.User_3: "User.3",
            pcbnew.User_4: "User.4",
            pcbnew.User_5: "User.5",
            pcbnew.User_6: "User.6",
            pcbnew.User_7: "User.7",
            pcbnew.User_8: "User.8",
            pcbnew.User_9: "User.9",
        }
        return mapping_reverse.get(layer_id, f"Layer #{layer_id}")

    @staticmethod
    def _show_error(msg: str) -> None:
        wx.MessageBox(msg, "DXF Import Error", wx.OK | wx.ICON_ERROR)

    @staticmethod
    def _show_info(msg: str) -> None:
        wx.MessageBox(msg, "DXF Import", wx.OK | wx.ICON_INFORMATION)


# Register the plugin so KiCad discovers it
DxfImportPlugin().register()
