"""Misturador das 8 partes: instrumento, volume, pan, efeitos e mute."""
from __future__ import annotations

import gi

gi.require_version("Gtk", "4.0")
gi.require_version("Adw", "1")
gi.require_version("Pango", "1.0")

from gi.repository import Adw, Gtk, Pango  # noqa: E402

from instruments import InstrumentDialog  # noqa: E402
from tones import CATEGORY_LABELS  # noqa: E402


class MixerDialog(Adw.Window):
    def __init__(self, parent: Gtk.Window, project, on_changed) -> None:
        super().__init__(transient_for=parent, modal=True)
        self.set_destroy_with_parent(True)
        self.project = project
        self.on_changed = on_changed
        self._loading = False
        self._instrument_dialog = None
        self.set_title("Misturador")
        self.set_default_size(980, 560)

        toolbar = Adw.ToolbarView()
        header = Gtk.HeaderBar()
        close = Gtk.Button(label="Fechar")
        close.connect("clicked", lambda _b: self.close())
        header.pack_end(close)
        toolbar.add_top_bar(header)

        content = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=8)
        content.set_margin_top(12)
        content.set_margin_bottom(12)
        content.set_margin_start(12)
        content.set_margin_end(12)

        hint = Gtk.Label(
            label=("Ajuste as 8 partes. O instrumento usa os 600 tons do "
                   "CT-X800; a prévia no PC é uma aproximação GM."),
            xalign=0,
        )
        hint.set_wrap(True)
        hint.add_css_class("caption")
        hint.add_css_class("dim-label")
        content.append(hint)

        grid = Gtk.Grid(column_spacing=8, row_spacing=6)
        grid.set_column_homogeneous(False)
        headers = ("Parte", "Instrumento", "Vol", "Pan", "Reverb", "Chorus", "Mudo")
        for column, text in enumerate(headers):
            label = Gtk.Label(label=text, xalign=0)
            label.add_css_class("caption")
            label.add_css_class("dim-label")
            grid.attach(label, column, 0, 1, 1)

        self.rows: list[dict] = []
        for row_index, part in enumerate(project.parts, start=1):
            name = Gtk.Label(label=f"{part.number} · {part.name}", xalign=0)
            name.set_width_chars(13)
            grid.attach(name, 0, row_index, 1, 1)

            instrument = Gtk.Button()
            instrument.set_hexpand(True)
            instrument_label = Gtk.Label(label=part.tone.label, xalign=0)
            instrument_label.set_ellipsize(Pango.EllipsizeMode.END)
            instrument.set_child(instrument_label)
            instrument.set_tooltip_text("Trocar instrumento")
            instrument.connect(
                "clicked",
                lambda _b, p=part, label=instrument_label:
                self._choose_instrument(p, label),
            )
            grid.attach(instrument, 1, row_index, 1, 1)

            spins: dict[str, Gtk.SpinButton] = {}
            for column, attr in ((2, "volume"), (3, "pan"),
                                 (4, "reverb"), (5, "chorus")):
                spin = Gtk.SpinButton.new_with_range(0, 127, 1)
                spin.set_width_chars(4)
                spin.set_hexpand(False)
                spin.connect(
                    "value-changed",
                    lambda s, p=part, a=attr: self._value_changed(p, a, s),
                )
                grid.attach(spin, column, row_index, 1, 1)
                spins[attr] = spin

            mute = Gtk.Switch()
            mute.set_valign(Gtk.Align.CENTER)
            mute.connect(
                "notify::active",
                lambda s, p=part: self._mute_changed(p, s),
            )
            grid.attach(mute, 6, row_index, 1, 1)
            self.rows.append({
                "part": part, "instrument": instrument,
                "instrument_label": instrument_label,
                "spins": spins, "mute": mute,
            })

        self._loading = True
        try:
            for row in self.rows:
                part = row["part"]
                row["instrument_label"].set_text(part.tone.label)
                row["spins"]["volume"].set_value(part.volume)
                row["spins"]["pan"].set_value(part.pan)
                row["spins"]["reverb"].set_value(part.reverb)
                row["spins"]["chorus"].set_value(part.chorus)
                row["mute"].set_active(part.mute)
        finally:
            self._loading = False

        scroller = Gtk.ScrolledWindow()
        scroller.set_policy(Gtk.PolicyType.AUTOMATIC, Gtk.PolicyType.AUTOMATIC)
        scroller.set_child(grid)
        scroller.set_vexpand(True)
        scroller.add_css_class("card")
        content.append(scroller)
        toolbar.set_content(content)
        self.set_content(toolbar)

    def _choose_instrument(self, part, label: Gtk.Label) -> None:
        def selected(tone):
            if tone.drum != part.drum:
                return
            part.tone_number = tone.number
            label.set_text(tone.label)
            self.on_changed(part.number)

        dialog = InstrumentDialog(self, part, selected)
        self._instrument_dialog = dialog
        dialog.present()

    def _value_changed(self, part, attr: str, spin: Gtk.SpinButton) -> None:
        if self._loading:
            return
        value = int(spin.get_value())
        if getattr(part, attr) == value:
            return
        setattr(part, attr, value)
        self.on_changed(part.number)

    def _mute_changed(self, part, switch: Gtk.Switch) -> None:
        if self._loading:
            return
        part.mute = bool(switch.get_active())
        self.on_changed(part.number)
