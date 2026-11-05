#! python3
# -*- coding: utf-8 -*-
"""ARQ-T · Suelos automáticos. CPython / Python.NET / Revit 2025."""
import logging
import importlib.util
import os
import sys

import clr
clr.AddReference("RevitAPI")
clr.AddReference("RevitAPIUI")
clr.AddReference("System.Windows.Forms")
clr.AddReference("System.Drawing")

import Autodesk.Revit.DB as DB
from Autodesk.Revit.UI import TaskDialog
from System import Decimal, Single
from System.Drawing import Color, Font, FontStyle, Icon, Size, SizeF
import System.Windows.Forms as WF

def _load_local_module(name):
    # CPython's sys.modules survives command executions in pyRevit. Load these
    # two tool-owned modules from this button's current source, in dependency
    # order, instead of reusing an earlier module or a different extension copy.
    path = os.path.join(os.path.dirname(os.path.abspath(__file__)), name + ".py")
    spec = importlib.util.spec_from_file_location(name, path)
    module = importlib.util.module_from_spec(spec)
    previous = sys.modules.get(name)
    sys.modules[name] = module
    try:
        with open(path, "rb") as source:
            exec(compile(source.read(), path, "exec"), module.__dict__)
    except Exception:
        if previous is None:
            sys.modules.pop(name, None)
        else:
            sys.modules[name] = previous
        raise
    return module


geometry = _load_local_module("arqt_geometry")
floors = _load_local_module("arqt_floors")


GRAPHITE = Color.FromArgb(37, 39, 43)
ORANGE = Color.FromArgb(244, 119, 33)
BACKGROUND = Color.FromArgb(245, 246, 248)
BORDER = Color.FromArgb(226, 228, 232)
WHITE = Color.White


def number(value, decimals=2, signed=False):
    spec = ("+" if signed else "") + ".{}f".format(decimals)
    return format(value, spec).replace(".", ",")


def meters(internal):
    return DB.UnitUtils.ConvertFromInternalUnits(internal, DB.UnitTypeId.Meters)


def square_meters(internal):
    return DB.UnitUtils.ConvertFromInternalUnits(internal, DB.UnitTypeId.SquareMeters)


def label(text, bold=False, size=9):
    control = WF.Label()
    control.Text = text
    control.AutoSize = True
    control.ForeColor = GRAPHITE
    control.Font = Font("Century Gothic", Single(size), FontStyle.Bold if bold else FontStyle.Regular)
    control.Margin = WF.Padding(0, 4, 10, 7)
    return control


def button(text, primary=False):
    control = WF.Button()
    control.Text = text
    control.AutoSize = True
    control.MinimumSize = Size(135, 36)
    control.Padding = WF.Padding(12, 5, 12, 5)
    control.FlatStyle = WF.FlatStyle.Flat
    control.FlatAppearance.BorderSize = 0 if primary else 1
    control.FlatAppearance.BorderColor = BORDER
    control.BackColor = ORANGE if primary else WHITE
    control.ForeColor = GRAPHITE
    control.Cursor = WF.Cursors.Hand
    control.Margin = WF.Padding(0, 0, 10, 0)
    return control


def table(columns=1):
    control = WF.TableLayoutPanel()
    control.ColumnCount = columns
    for _ in range(columns):
        control.ColumnStyles.Add(WF.ColumnStyle(WF.SizeType.Percent, Single(100.0 / columns)))
    control.AutoSize = True
    control.AutoSizeMode = WF.AutoSizeMode.GrowAndShrink
    control.Dock = WF.DockStyle.Top
    control.Margin = WF.Padding(0)
    return control


def field(title, control):
    panel = table()
    panel.Margin = WF.Padding(0, 0, 14, 8)
    panel.Controls.Add(label(title, True, 8), 0, 0)
    control.Dock = WF.DockStyle.Top
    panel.Controls.Add(control, 0, 1)
    return panel


class FloorsWindow(WF.Form):
    def __init__(self, doc, rooms, types, levels, source_level, phase, excluded, logger):
        super().__init__()
        self.doc, self.rooms, self.types, self.levels = doc, rooms, types, levels
        self.source_level, self.phase, self.logger = source_level, phase, logger
        self.selected = set()
        self._refreshing = False
        self.Text = "ARQ-T | Suelos automáticos"
        self.BackColor = BACKGROUND
        self.ForeColor = GRAPHITE
        self.Font = Font("Century Gothic", Single(9))  # Windows falls back if absent.
        self.AutoScaleDimensions = SizeF(96, 96)
        self.AutoScaleMode = WF.AutoScaleMode.Dpi
        self.StartPosition = WF.FormStartPosition.CenterParent
        self.ClientSize = Size(910, 930)
        self.MinimumSize = Size(780, 650)
        self.MinimizeBox = False
        self.ShowInTaskbar = False
        self.Shown += self._fit_screen
        self.Icon = Icon(os.path.join(os.path.dirname(__file__), "icon.ico"))

        actions = WF.FlowLayoutPanel()
        actions.Dock = WF.DockStyle.Bottom
        actions.Height = 68
        actions.Padding = WF.Padding(22, 14, 12, 12)
        actions.FlowDirection = WF.FlowDirection.RightToLeft
        actions.BackColor = WHITE
        self.generate = button("GENERAR SUELOS", True)
        self.generate.Enabled = False
        self.generate.Click += self._generate
        cancel = button("CANCELAR")
        cancel.DialogResult = WF.DialogResult.Cancel
        actions.Controls.Add(self.generate)
        actions.Controls.Add(cancel)
        self.CancelButton = cancel

        scroll = WF.Panel()
        scroll.Dock = WF.DockStyle.Fill
        scroll.AutoScroll = True
        scroll.Padding = WF.Padding(24, 18, 24, 12)
        body = table()
        body.BackColor = BACKGROUND
        scroll.Controls.Add(body)
        self.Controls.Add(scroll)
        self.Controls.Add(actions)

        header = label("ARQ-T  /  SUELOS AUTOMÁTICOS", True, 17)
        body.Controls.Add(header, 0, 0)
        body.Controls.Add(label("Pavimentos nativos a partir de las habitaciones del proyecto."), 0, 1)
        body.Controls.Add(label("01  MODO DE GENERACIÓN", True), 0, 2)
        cards = table(2)
        self.continuous = self._card("SUELO CONTINUO", "Un suelo por cada grupo de habitaciones conectadas.")
        self.per_room = self._card("POR HABITACIÓN", "Un suelo independiente para cada habitación seleccionada.")
        cards.Controls.Add(self.continuous, 0, 0)
        cards.Controls.Add(self.per_room, 1, 0)
        self.continuous.CheckedChanged += self._mode_changed
        self.per_room.CheckedChanged += self._mode_changed
        self.per_room.Checked = True
        body.Controls.Add(cards, 0, 3)

        body.Controls.Add(label("02  CONFIGURACIÓN", True), 0, 4)
        config = table(2)
        self.type_combo = WF.ComboBox()
        self.type_combo.DropDownStyle = WF.ComboBoxStyle.DropDownList
        self.type_combo.IntegralHeight = False
        self.type_combo.MaxDropDownItems = 12
        for item in types:
            self.type_combo.Items.Add(floors.type_name(item))
        self.type_combo.SelectedIndex = 0
        type_field = field("TIPO DE SUELO", self.type_combo)
        config.Controls.Add(type_field, 0, 0)
        config.SetColumnSpan(type_field, 2)
        origin = WF.TextBox()
        origin.ReadOnly = True
        origin.Text = "{} · fase {}".format(source_level.Name, phase.Name)
        origin.BackColor = BACKGROUND
        origin.TabStop = False
        source_field = field("NIVEL DE LAS HABITACIONES", origin)
        config.Controls.Add(source_field, 0, 1)
        config.SetColumnSpan(source_field, 2)
        self.level_combo = WF.ComboBox()
        self.level_combo.DropDownStyle = WF.ComboBoxStyle.DropDownList
        for level in levels:
            self.level_combo.Items.Add("{} ({} m)".format(level.Name, number(meters(level.Elevation), 3, True)))
        self.level_combo.SelectedIndex = next(i for i, level in enumerate(levels) if level.Id == source_level.Id)
        config.Controls.Add(field("NIVEL DE CREACIÓN", self.level_combo), 0, 2)
        self.offset = WF.NumericUpDown()
        self.offset.DecimalPlaces = 3
        self.offset.Minimum = Decimal(-100000000)
        self.offset.Maximum = Decimal(100000000)
        self.offset.Increment = Decimal(1)
        self.offset.ThousandsSeparator = True
        config.Controls.Add(field("DESFASE RESPECTO AL NIVEL · mm", self.offset), 1, 2)
        self.elevation = label("", True, 10)
        self.elevation.ForeColor = ORANGE
        config.Controls.Add(self.elevation, 0, 3)
        config.SetColumnSpan(self.elevation, 2)
        self.level_combo.SelectedIndexChanged += self._elevation_changed
        self.offset.ValueChanged += self._elevation_changed
        self._elevation_changed(None, None)
        body.Controls.Add(config, 0, 5)

        body.Controls.Add(label("03  HABITACIONES", True), 0, 6)
        search_row = table(2)
        self.search = WF.TextBox()
        self.search.AccessibleName = "Buscar habitaciones por nombre o número"
        self.search.TextChanged += self._filter
        search_row.Controls.Add(field("BUSCAR POR NOMBRE O NÚMERO", self.search), 0, 0)
        selection_actions = WF.FlowLayoutPanel()
        selection_actions.AutoSize = True
        selection_actions.AutoSizeMode = WF.AutoSizeMode.GrowAndShrink
        selection_actions.Dock = WF.DockStyle.Fill
        selection_actions.Padding = WF.Padding(0, 20, 0, 0)
        all_button, none_button = button("Seleccionar todas"), button("Deseleccionar todas")
        all_button.Click += self._select_all
        none_button.Click += self._select_none
        selection_actions.Controls.Add(all_button)
        selection_actions.Controls.Add(none_button)
        search_row.Controls.Add(selection_actions, 1, 0)
        body.Controls.Add(search_row, 0, 7)

        self.grid = WF.DataGridView()
        grid = self.grid
        grid.Dock = WF.DockStyle.Top
        grid.Height = 205
        grid.AllowUserToAddRows = False
        grid.AllowUserToDeleteRows = False
        grid.AllowUserToResizeRows = False
        grid.AutoGenerateColumns = False
        grid.AutoSizeColumnsMode = WF.DataGridViewAutoSizeColumnsMode.Fill
        grid.RowHeadersVisible = False
        grid.MultiSelect = False
        grid.SelectionMode = WF.DataGridViewSelectionMode.FullRowSelect
        grid.BackgroundColor = WHITE
        grid.BorderStyle = getattr(WF.BorderStyle, "None")
        grid.GridColor = BORDER
        grid.EnableHeadersVisualStyles = False
        grid.ColumnHeadersDefaultCellStyle.BackColor = GRAPHITE
        grid.ColumnHeadersDefaultCellStyle.ForeColor = WHITE
        grid.ColumnHeadersDefaultCellStyle.Font = Font(self.Font, FontStyle.Bold)
        grid.ColumnHeadersHeight = 36
        grid.ColumnHeadersHeightSizeMode = WF.DataGridViewColumnHeadersHeightSizeMode.DisableResizing
        grid.RowTemplate.Height = 32
        grid.DefaultCellStyle.SelectionBackColor = Color.FromArgb(255, 229, 207)
        grid.DefaultCellStyle.SelectionForeColor = GRAPHITE
        grid.AlternatingRowsDefaultCellStyle.BackColor = BACKGROUND
        selected_col = WF.DataGridViewCheckBoxColumn()
        selected_col.Name = "selected"
        selected_col.HeaderText = "✓"
        selected_col.FillWeight = Single(12)
        grid.Columns.Add(selected_col)
        for key, title, weight in (("number", "Número", 20), ("name", "Habitación", 50), ("area", "Superficie", 22)):
            column = WF.DataGridViewTextBoxColumn()
            column.Name, column.HeaderText = key, title
            column.FillWeight = Single(weight)
            column.ReadOnly = True
            column.SortMode = (WF.DataGridViewColumnSortMode.Automatic if key != "area"
                               else WF.DataGridViewColumnSortMode.NotSortable)
            grid.Columns.Add(column)
        grid.CurrentCellDirtyStateChanged += self._commit_checkbox
        grid.CellValueChanged += self._selection_changed
        body.Controls.Add(grid, 0, 8)
        self.summary = label("", True)
        body.Controls.Add(self.summary, 0, 9)
        if excluded:
            body.Controls.Add(label("{} habitaciones sin colocar, sin cerrar o sin superficie excluidas.".format(excluded)), 0, 10)
        body.Controls.Add(label("04  ACCIONES · Revisa y confirma la configuración antes de generar."), 0, 11)
        self._filter(None, None)

    def _fit_screen(self, sender, event):
        # Let Revit own process DPI awareness. Scale with WinForms, then fit the
        # working area; the body scrolls while the action buttons stay visible.
        area = WF.Screen.FromControl(self).WorkingArea
        self.Size = Size(min(self.Width, area.Width - 24), min(self.Height, area.Height - 24))
        self.Left = max(area.Left, min(self.Left, area.Right - self.Width))
        self.Top = max(area.Top, min(self.Top, area.Bottom - self.Height))

    def _card(self, title, description):
        control = WF.RadioButton()
        control.Appearance = WF.Appearance.Button
        control.FlatStyle = WF.FlatStyle.Flat
        control.FlatAppearance.BorderSize = 1
        control.FlatAppearance.BorderColor = BORDER
        control.FlatAppearance.CheckedBackColor = ORANGE
        control.Text = title + "\n\n" + description
        control.Font = Font(self.Font, FontStyle.Bold)
        control.Height = 88
        control.Dock = WF.DockStyle.Top
        control.Padding = WF.Padding(14, 8, 14, 8)
        control.Margin = WF.Padding(0, 3, 14, 16)
        control.BackColor = WHITE
        control.Cursor = WF.Cursors.Hand
        control.UseVisualStyleBackColor = False
        return control

    def _mode_changed(self, sender, event):
        for control in (self.continuous, self.per_room):
            control.BackColor = ORANGE if control.Checked else WHITE
            control.FlatAppearance.BorderColor = ORANGE if control.Checked else BORDER

    def _elevation_changed(self, sender, event):
        level = self.levels[self.level_combo.SelectedIndex]
        offset = float(str(self.offset.Value.ToString()).replace(",", "."))
        elevation = meters(level.Elevation) + offset / 1000.0
        self.elevation.Text = "COTA RESULTANTE   {} m".format(number(elevation, 3, True))

    def _filter(self, sender, event):
        self._refreshing = True
        try:
            query = self.search.Text.strip().casefold()
            self.grid.Rows.Clear()
            for room in sorted(self.rooms, key=lambda r: r.Number.casefold()):
                name = room.get_Parameter(DB.BuiltInParameter.ROOM_NAME).AsString() or ""
                if query and query not in (room.Number + " " + name).casefold():
                    continue
                index = self.grid.Rows.Add()
                row = self.grid.Rows[index]
                row.Tag = geometry.element_id(room)
                row.Cells[0].Value = geometry.element_id(room) in self.selected
                row.Cells[1].Value = room.Number
                row.Cells[2].Value = name
                row.Cells[3].Value = number(square_meters(room.Area)) + " m²"
        finally:
            self._refreshing = False
        self._update_summary()

    def _commit_checkbox(self, sender, event):
        if self.grid.IsCurrentCellDirty:
            self.grid.CommitEdit(WF.DataGridViewDataErrorContexts.Commit)

    def _selection_changed(self, sender, event):
        if self._refreshing or event.RowIndex < 0 or event.ColumnIndex != 0:
            return
        row = self.grid.Rows[event.RowIndex]
        if row.Tag is None:
            return
        key = int(row.Tag)
        if bool(row.Cells[0].Value):
            self.selected.add(key)
        else:
            self.selected.discard(key)
        self._update_summary()

    def _select_all(self, sender, event):
        self.selected = {geometry.element_id(room) for room in self.rooms}
        self._filter(None, None)

    def _select_none(self, sender, event):
        self.selected.clear()
        self._filter(None, None)

    def _update_summary(self):
        area = sum(room.Area for room in self.rooms if geometry.element_id(room) in self.selected)
        self.summary.Text = "Seleccionadas: {} de {}     ·     Superficie: {} m²".format(
            len(self.selected), len(self.rooms), number(square_meters(area)))
        self.generate.Enabled = bool(self.selected)

    def _generate(self, sender, event):
        self.grid.EndEdit()
        rooms = [room for room in self.rooms if geometry.element_id(room) in self.selected]
        if not rooms:
            return
        self.generate.Enabled = False
        self.UseWaitCursor = True
        stage = "Validar configuración"
        try:
            floor_type = self.types[self.type_combo.SelectedIndex]
            level = self.levels[self.level_combo.SelectedIndex]
            offset_mm = float(str(self.offset.Value.ToString()).replace(",", "."))
            offset = floors.validate_settings(self.doc, floor_type, level, offset_mm)
            mode = "continuous" if self.continuous.Checked else "rooms"
            stage = "Calcular suelo continuo" if mode == "continuous" else "Calcular suelos por habitación"
            prepared = geometry.prepare(self.doc, rooms, mode, self.phase)
            stage = "Comprobar posibles duplicados"
            duplicates = floors.likely_duplicates(self.doc, prepared, level, offset)
            area = sum(item["area"] for item in prepared)
            if mode == "continuous":
                self.logger.info("Geometría continua: %s", prepared[0]["diagnostics"])
            message = ("Modo: {}\nTipo: {}\nNivel de creación: {}\nDesfase: {} mm\n"
                       "Habitaciones seleccionadas: {}\nSuelos que se crearán: {}\n"
                       "Superficie aproximada: {} m²\n\nHabitaciones: {}")
            message = message.format("Suelo continuo" if mode == "continuous" else "Por habitación",
                                     floors.type_name(floor_type), level.Name, number(offset_mm, 3, True),
                                     len(rooms), len(prepared), number(square_meters(area)),
                                     ", ".join(room.Number for room in rooms))
            if mode == "continuous":
                message += "\n\nSuperficie añadida en pasos de puerta: {} m²".format(
                    number(square_meters(prepared[0]["diagnostics"]["added_area"])))
                diagnostics = prepared[0]["diagnostics"]
                if diagnostics.get("group_count", 1) > 1:
                    message += "\n\nGrupos independientes:"
                    for index, group in enumerate(diagnostics["groups"], 1):
                        message += "\nSuelo {} · habitaciones: {}".format(index, ", ".join(group))
                if diagnostics.get("cleanup_area", 0) > geometry.AREA_EPS:
                    message += "\nAjuste por tolerancia de Revit: {} m²".format(
                        number(square_meters(diagnostics["cleanup_area"]), 6))
                message += "\nHuecos interiores conservados: {} · {} m²".format(
                    diagnostics["preserved_holes"], number(square_meters(diagnostics["preserved_hole_area"])))
            if duplicates:
                message += "\n\nPOSIBLE DUPLICACIÓN: hay suelos en la misma cota que se solapan (ID {}).".format(
                    ", ".join(map(str, duplicates)))
            message += "\n\n¿Generar los suelos con esta configuración?"
            self.UseWaitCursor = False
            choice = WF.MessageBox.Show(self, message, "ARQ-T · Confirmar generación",
                                        WF.MessageBoxButtons.YesNo,
                                        WF.MessageBoxIcon.Warning if duplicates else WF.MessageBoxIcon.Question,
                                        WF.MessageBoxDefaultButton.Button2)
            if choice != WF.DialogResult.Yes:
                return
            self.UseWaitCursor = True
            stage = "Crear suelos en Revit"
            created, warnings = floors.create(self.doc, prepared, floor_type, level, offset_mm, self.logger)
            self.UseWaitCursor = False
            result = "Se han creado {} suelos.\nID: {}".format(len(created), ", ".join(str(e.Id.Value) for e in created))
            if warnings:
                result += "\n\nAvisos de Revit:\n" + "\n".join(dict.fromkeys(warnings))
            WF.MessageBox.Show(self, result, "ARQ-T · Generación completada", WF.MessageBoxButtons.OK,
                               WF.MessageBoxIcon.Warning if warnings else WF.MessageBoxIcon.Information)
            self.DialogResult = WF.DialogResult.OK
            self.Close()
        except Exception as exc:
            self.logger.exception("Error en Suelos automáticos: %s", stage)
            self.UseWaitCursor = False
            WF.MessageBox.Show(self, stage + "\n\n" + str(exc), "ARQ-T · No se han generado suelos",
                               WF.MessageBoxButtons.OK, WF.MessageBoxIcon.Warning)
        finally:
            self.UseWaitCursor = False
            self.generate.Enabled = bool(self.selected)


def main(uiapp):
    if sys.implementation.name != "cpython":
        TaskDialog.Show("ARQ-T", "Este botón requiere CPython. Comprueba el encabezado #! python3 y el motor de pyRevit.")
        return
    try:
        from pyrevit import script
        logger = script.get_logger()
    except Exception:
        logger = logging.getLogger("ARQ-T.SuelosAutomaticos")
    window = owner = None
    try:
        if uiapp.ActiveUIDocument is None:
            raise ValueError("Abre un proyecto y una vista de planta.")
        doc = uiapp.ActiveUIDocument.Document
        choices = floors.model_choices(doc, doc.ActiveView)
        logger.info("CPython %s; WinForms; Revit %s", sys.version, uiapp.Application.VersionNumber)
        logger.info("Módulos del botón: %s; %s", geometry.__file__, floors.__file__)
        window = FloorsWindow(doc, *choices, logger)
        owner = WF.NativeWindow()
        owner.AssignHandle(uiapp.MainWindowHandle)
        window.ShowDialog(owner)
    except Exception as exc:
        logger.exception("No se puede abrir Suelos automáticos")
        TaskDialog.Show("ARQ-T · Suelos automáticos", str(exc))
    finally:
        if window is not None:
            window.Dispose()
        if owner is not None:
            owner.ReleaseHandle()


if __name__ == "__main__":
    main(__revit__)
