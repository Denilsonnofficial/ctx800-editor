#!/usr/bin/env python3
"""Editor gráfico de ritmos AC7 para o Casio CT-X800."""
from __future__ import annotations

import sys
import time
from pathlib import Path

import gi

gi.require_version("Gtk", "4.0")
gi.require_version("Adw", "1")
gi.require_version("Pango", "1.0")

from gi.repository import Adw, Gio, GLib, Gtk, Pango  # noqa: E402

from audio import AudioEngine  # noqa: E402
from editor import PatternEditor  # noqa: E402
from exporter import ExportError, ExportResult, export_ac7  # noqa: E402
from instruments import InstrumentDialog  # noqa: E402
from mixer import MixerDialog  # noqa: E402
from model import (  # noqa: E402
    SECTION_BY_ID, SECTIONS, TIME_SIGNATURES, Project, default_project,
    empty_project, steps_per_bar_for,
)
from tones import CATEGORY_LABELS, get_tone  # noqa: E402

APP_ID = "br.com.ctx800.Editor"
APP_NAME = "Editor de Ritmos CT-X800"
VERSION = "1.0.0"

NOTE_LENGTHS = (1, 2, 4, 8, 16)
NOTE_LENGTH_LABELS = ("1/16", "1/8", "1/4", "1/2", "1 compasso")
SIGNATURES = ("4/4", "3/4", "2/4", "6/8")


class EditorWindow(Adw.ApplicationWindow):
    def __init__(self, **kwargs) -> None:
        super().__init__(**kwargs)
        self.set_default_size(1360, 860)
        self.set_size_request(1000, 600)
        try:
            self.set_icon_name("audio-x-generic")
        except Exception:
            pass

        self.project: Project = default_project()
        self.current_section = "var1"
        self.current_part_number = 2
        self.dirty = False
        self._loading = False
        self._part_loading = False
        self._section_loading = False
        self._playing = False
        self._starting = False
        self._tick_id: int | None = None
        self._play_started = 0.0
        self._allow_close = False
        self._last_export: ExportResult | None = None
        self._dialog = None
        self._mixer_dialog = None

        self.projects_dir = Path.home() / "ctx800" / "ritmos"
        self.exports_dir = Path(__file__).resolve().parent / "saidas"
        for folder in (self.projects_dir, self.exports_dir):
            try:
                folder.mkdir(parents=True, exist_ok=True)
            except OSError:
                pass

        self.audio: AudioEngine | None = None
        self._audio_error = ""
        try:
            self.audio = AudioEngine()
            self.audio.on_finished = self._on_audio_finished
        except Exception as exc:  # áudio é opcional; editar/exportar continua
            self._audio_error = str(exc)

        self._build_ui()
        self._install_actions()
        self._load_project_into_ui()
        self.connect("close-request", self._on_close_request)

    # ================================================================ UI
    def _build_ui(self) -> None:
        toolbar = Adw.ToolbarView()
        header = Gtk.HeaderBar()

        menu = Gio.Menu()
        menu.append("Novo projeto", "win.new")
        menu.append("Abrir projeto…", "win.open")
        file_section = Gio.Menu()
        file_section.append("Salvar", "win.save")
        file_section.append("Salvar como…", "win.save_as")
        menu.append_section(None, file_section)
        menu.append("Exportar AC7…", "win.export")
        edit_section = Gio.Menu()
        edit_section.append("Misturador…", "win.mixer")
        edit_section.append("Limpar seção atual", "win.clear_section")
        menu.append_section(None, edit_section)
        menu.append("Sobre", "win.about")

        menu_button = Gtk.MenuButton()
        menu_button.set_icon_name("open-menu-symbolic")
        menu_button.set_menu_model(menu)
        menu_button.set_tooltip_text("Menu")
        header.pack_start(menu_button)

        title_box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL,
                            valign=Gtk.Align.CENTER)
        title = Gtk.Label()
        title.set_markup(f"<b>{APP_NAME}</b>")
        title.set_ellipsize(Pango.EllipsizeMode.END)
        self.subtitle = Gtk.Label(xalign=0.5)
        self.subtitle.add_css_class("caption")
        self.subtitle.add_css_class("dim-label")
        self.subtitle.set_ellipsize(Pango.EllipsizeMode.END)
        title_box.append(title)
        title_box.append(self.subtitle)
        header.set_title_widget(title_box)

        self.play_button = Gtk.Button.new_from_icon_name(
            "media-playback-start-symbolic")
        self.play_button.set_tooltip_text("Tocar a seção (Espaço)")
        self.play_button.connect("clicked", lambda _b: self._toggle_play())
        header.pack_end(self.play_button)

        self.export_button = Gtk.Button(label="Exportar AC7")
        self.export_button.add_css_class("suggested-action")
        self.export_button.set_tooltip_text("Gerar o arquivo .AC7 para o CT-X800")
        self.export_button.set_focus_on_click(False)
        self.export_button.connect("clicked", lambda _b: self._export_clicked())
        header.pack_end(self.export_button)

        self.mixer_button = Gtk.Button.new_from_icon_name(
            "view-list-symbolic")
        self.mixer_button.set_tooltip_text("Misturador das 8 partes")
        self.mixer_button.set_focus_on_click(False)
        self.mixer_button.connect("clicked", lambda _b: self._open_mixer())
        header.pack_end(self.mixer_button)
        toolbar.add_top_bar(header)

        # ------------------------------------------------------- lateral
        sidebar = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=7)
        sidebar.set_margin_top(12)
        sidebar.set_margin_bottom(12)
        sidebar.set_margin_start(12)
        sidebar.set_margin_end(12)

        sidebar.append(self._field_label("NOME DO RITMO"))
        self.name_entry = Gtk.Entry()
        self.name_entry.set_max_length(20)
        self.name_entry.set_placeholder_text("MEU RITMO")
        self.name_entry.connect("changed", self._on_name_changed)
        sidebar.append(self.name_entry)
        name_hint = Gtk.Label(label="Até 7 caracteres aparecem no teclado.",
                              xalign=0)
        name_hint.add_css_class("caption")
        name_hint.add_css_class("dim-label")
        sidebar.append(name_hint)

        tempo_row = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=8)
        tempo_box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=3)
        tempo_box.set_hexpand(True)
        tempo_box.append(self._field_label("ANDAMENTO (BPM)"))
        self.tempo_spin = Gtk.SpinButton.new_with_range(20, 255, 1)
        self.tempo_spin.set_hexpand(True)
        self.tempo_spin.connect("value-changed", self._on_tempo_changed)
        tempo_box.append(self.tempo_spin)
        tempo_row.append(tempo_box)
        signature_box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=3)
        signature_box.append(self._field_label("COMPASSO"))
        self.signature_dropdown = Gtk.DropDown.new_from_strings(
            list(SIGNATURES))
        self.signature_dropdown.connect("notify::selected",
                                        self._on_signature_changed)
        signature_box.append(self.signature_dropdown)
        tempo_row.append(signature_box)
        sidebar.append(tempo_row)

        sidebar.append(Gtk.Separator())
        sidebar.append(self._field_label("SEÇÕES"))
        self.section_list = Gtk.ListBox()
        self.section_list.add_css_class("navigation-sidebar")
        self.section_list.set_selection_mode(Gtk.SelectionMode.SINGLE)
        self.section_list.connect("row-selected", self._on_section_selected)
        self.section_rows: dict[str, Gtk.ListBoxRow] = {}
        self.section_counts: dict[str, Gtk.Label] = {}
        for section in SECTIONS:
            row = Gtk.ListBoxRow()
            row.section_id = section["id"]  # type: ignore[attr-defined]
            box = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=8)
            box.set_margin_top(3)
            box.set_margin_bottom(3)
            box.set_margin_start(8)
            box.set_margin_end(8)
            name = Gtk.Label(label=section["name"], xalign=0)
            name.set_hexpand(True)
            count = Gtk.Label(label="vazia", xalign=1)
            count.add_css_class("caption")
            count.add_css_class("dim-label")
            box.append(name)
            box.append(count)
            row.set_child(box)
            self.section_list.append(row)
            self.section_rows[section["id"]] = row
            self.section_counts[section["id"]] = count
        section_scroll = Gtk.ScrolledWindow()
        section_scroll.set_policy(Gtk.PolicyType.NEVER,
                                  Gtk.PolicyType.ALWAYS)
        section_scroll.set_overlay_scrolling(False)
        section_scroll.set_child(self.section_list)
        section_scroll.set_min_content_height(168)
        section_scroll.set_max_content_height(280)
        section_scroll.set_propagate_natural_height(True)
        sidebar.append(section_scroll)

        bars_row = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=8)
        bars_label = self._field_label("COMPASSOS DA SEÇÃO")
        bars_label.set_hexpand(True)
        bars_row.append(bars_label)
        self.bars_spin = Gtk.SpinButton.new_with_range(1, 8, 1)
        self.bars_spin.set_width_chars(3)
        self.bars_spin.connect("value-changed", self._on_bars_changed)
        bars_row.append(self.bars_spin)
        sidebar.append(bars_row)

        sidebar.append(Gtk.Separator())
        sidebar.append(self._field_label("PARTE EDITADA"))
        self.part_dropdown = Gtk.DropDown.new_from_strings(
            [f"{part.number} · {part.name}" for part in self.project.parts])
        self.part_dropdown.connect("notify::selected", self._on_part_changed)
        sidebar.append(self.part_dropdown)

        sidebar.append(self._field_label("INSTRUMENTO (TOM CASIO)"))
        self.instrument_button = Gtk.Button()
        instrument_box = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL,
                                 spacing=8)
        instrument_text = Gtk.Box(orientation=Gtk.Orientation.VERTICAL,
                                  spacing=1)
        instrument_text.set_hexpand(True)
        self.instrument_name = Gtk.Label(xalign=0)
        self.instrument_name.set_ellipsize(Pango.EllipsizeMode.END)
        self.instrument_detail = Gtk.Label(xalign=0)
        self.instrument_detail.add_css_class("caption")
        self.instrument_detail.add_css_class("dim-label")
        self.instrument_detail.set_ellipsize(Pango.EllipsizeMode.END)
        instrument_text.append(self.instrument_name)
        instrument_text.append(self.instrument_detail)
        instrument_box.append(instrument_text)
        instrument_box.append(Gtk.Image.new_from_icon_name(
            "pan-down-symbolic"))
        self.instrument_button.set_child(instrument_box)
        self.instrument_button.set_tooltip_text(
            "Escolher entre os 600 tons do CT-X800")
        self.instrument_button.connect(
            "clicked", lambda _b: self._choose_instrument())
        sidebar.append(self.instrument_button)

        mixer_grid = Gtk.Grid(column_spacing=8, row_spacing=4)
        mixer_grid.set_column_homogeneous(True)
        for column, (label, attr) in enumerate((
            ("Volume", "volume"), ("Pan", "pan"),
        )):
            mixer_grid.attach(self._field_label(label), column * 2, 0, 1, 1)
            spin = Gtk.SpinButton.new_with_range(0, 127, 1)
            spin.set_hexpand(True)
            spin.connect("value-changed",
                         lambda s, a=attr: self._on_part_value(s, a))
            mixer_grid.attach(spin, column * 2 + 1, 0, 1, 1)
            setattr(self, f"{attr}_spin", spin)
        for column, (label, attr) in enumerate((
            ("Reverb", "reverb"), ("Chorus", "chorus"),
        )):
            mixer_grid.attach(self._field_label(label), column * 2, 1, 1, 1)
            spin = Gtk.SpinButton.new_with_range(0, 127, 1)
            spin.set_hexpand(True)
            spin.connect("value-changed",
                         lambda s, a=attr: self._on_part_value(s, a))
            mixer_grid.attach(spin, column * 2 + 1, 1, 1, 1)
            setattr(self, f"{attr}_spin", spin)
        sidebar.append(mixer_grid)

        switches = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=16)
        mute_box = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=6)
        mute_box.append(Gtk.Label(label="Mudo"))
        self.mute_switch = Gtk.Switch()
        self.mute_switch.set_valign(Gtk.Align.CENTER)
        self.mute_switch.connect("notify::active", self._on_mute_changed)
        mute_box.append(self.mute_switch)
        switches.append(mute_box)
        chord_box = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=6)
        chord_box.append(Gtk.Label(label="Segue acorde"))
        self.chord_switch = Gtk.Switch()
        self.chord_switch.set_valign(Gtk.Align.CENTER)
        self.chord_switch.connect("notify::active", self._on_chord_changed)
        chord_box.append(self.chord_switch)
        switches.append(chord_box)
        sidebar.append(switches)

        note_row = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=8)
        length_box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=3)
        length_box.set_hexpand(True)
        length_box.append(self._field_label("DURAÇÃO DA NOTA"))
        self.length_dropdown = Gtk.DropDown.new_from_strings(
            list(NOTE_LENGTH_LABELS))
        self.length_dropdown.set_selected(0)
        self.length_dropdown.connect("notify::selected",
                                     self._on_length_changed)
        length_box.append(self.length_dropdown)
        note_row.append(length_box)
        velocity_box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=3)
        velocity_box.append(self._field_label("INTENSIDADE"))
        self.velocity_spin = Gtk.SpinButton.new_with_range(1, 127, 1)
        self.velocity_spin.set_value(100)
        self.velocity_spin.set_width_chars(4)
        self.velocity_spin.connect("value-changed",
                                   self._on_velocity_changed)
        velocity_box.append(self.velocity_spin)
        note_row.append(velocity_box)
        sidebar.append(note_row)

        sidebar.append(Gtk.Separator())
        button_row = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=6)
        self.clear_button = Gtk.Button(label="Limpar parte")
        self.clear_button.set_focus_on_click(False)
        self.clear_button.connect("clicked", lambda _b: self._clear_clicked())
        button_row.append(self.clear_button)
        clear_section = Gtk.Button(label="Limpar seção")
        clear_section.set_focus_on_click(False)
        clear_section.connect("clicked",
                              lambda _b: self._clear_section_clicked())
        button_row.append(clear_section)
        sidebar.append(button_row)

        sidebar.set_valign(Gtk.Align.START)

        side_scroll = Gtk.ScrolledWindow()
        side_scroll.set_policy(Gtk.PolicyType.NEVER,
                               Gtk.PolicyType.AUTOMATIC)
        side_scroll.set_child(sidebar)
        side_scroll.set_size_request(330, -1)

        # ------------------------------------------------------- grade
        grid_area = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=8)
        grid_area.set_margin_top(12)
        grid_area.set_margin_bottom(10)
        grid_area.set_margin_start(12)
        grid_area.set_margin_end(12)

        grid_header = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=8)
        self.grid_title = Gtk.Label(xalign=0)
        self.grid_title.add_css_class("title-4")
        self.grid_title.set_hexpand(True)
        self.grid_info = Gtk.Label(xalign=1)
        self.grid_info.add_css_class("caption")
        self.grid_info.add_css_class("dim-label")
        self.grid_info.set_ellipsize(Pango.EllipsizeMode.END)
        grid_header.append(self.grid_title)
        grid_header.append(self.grid_info)
        grid_area.append(grid_header)

        self.editor = PatternEditor(on_changed=self._on_notes_changed)
        grid_scroll = Gtk.ScrolledWindow()
        grid_scroll.set_policy(Gtk.PolicyType.AUTOMATIC,
                               Gtk.PolicyType.AUTOMATIC)
        grid_scroll.set_child(self.editor)
        grid_scroll.set_hexpand(True)
        grid_scroll.set_vexpand(True)
        grid_scroll.add_css_class("card")
        grid_area.append(grid_scroll)

        paned = Gtk.Paned(orientation=Gtk.Orientation.HORIZONTAL)
        paned.set_start_child(side_scroll)
        paned.set_end_child(grid_area)
        paned.set_position(330)
        paned.set_resize_start_child(False)
        paned.set_shrink_start_child(False)
        paned.set_wide_handle(True)
        toolbar.set_content(paned)

        # ------------------------------------------------------- rodapé
        status = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=8)
        status.set_margin_top(5)
        status.set_margin_bottom(5)
        status.set_margin_start(12)
        status.set_margin_end(12)
        self.status_label = Gtk.Label(label="Pronto", xalign=0)
        self.status_label.set_hexpand(True)
        self.status_label.set_ellipsize(Pango.EllipsizeMode.END)
        self.status_label.add_css_class("caption")
        self.bpm_label = Gtk.Label(xalign=1)
        self.bpm_label.add_css_class("caption")
        self.bpm_label.add_css_class("dim-label")
        status.append(self.status_label)
        status.append(self.bpm_label)
        toolbar.add_bottom_bar(status)

        self.toast_overlay = Adw.ToastOverlay()
        self.toast_overlay.set_child(toolbar)
        self.set_content(self.toast_overlay)

    @staticmethod
    def _field_label(text: str) -> Gtk.Label:
        label = Gtk.Label(label=text, xalign=0)
        label.add_css_class("caption")
        label.add_css_class("dim-label")
        return label

    # ================================================================ ações
    def _install_actions(self) -> None:
        actions = {
            "new": self._new_clicked,
            "open": self._open_clicked,
            "save": self._save_clicked,
            "save_as": self._save_as_clicked,
            "export": self._export_clicked,
            "play": self._toggle_play,
            "mixer": self._open_mixer,
            "clear_section": self._clear_section_clicked,
            "about": self._about_clicked,
        }
        for name, callback in actions.items():
            action = Gio.SimpleAction.new(name, None)
            action.connect(
                "activate",
                lambda _action, _param, cb=callback: cb(),
            )
            self.add_action(action)

    # ================================================================ estado
    def _load_project_into_ui(self) -> None:
        if self._mixer_dialog is not None:
            try:
                self._mixer_dialog.close()
            except Exception:
                pass
            self._mixer_dialog = None
        self._loading = True
        try:
            self.current_section = (self._first_filled_section()
                                    or self.current_section)
            if self.current_section not in SECTION_BY_ID:
                self.current_section = "var1"
            self.current_part_number = 2
            self.name_entry.set_text(self.project.name)
            self.tempo_spin.set_value(self.project.tempo)
            self.signature_dropdown.set_selected(
                SIGNATURES.index(self.project.time_signature)
                if self.project.time_signature in SIGNATURES else 0)
            self.part_dropdown.set_selected(self.current_part_number - 1)
            self.length_dropdown.set_selected(0)
            self.velocity_spin.set_value(100)
            row = self.section_rows.get(self.current_section)
            if row is not None:
                self.section_list.select_row(row)
            self._update_bars_spin()
            self.editor.set_context(self.project, self.current_section,
                                    self.current_part_number)
            self.editor.set_note_length(1)
            self.editor.set_velocity(100)
            self._update_part_controls()
        finally:
            self._loading = False
        self.dirty = False
        self._stop_playback()
        self._update_titles()
        self._update_section_counts()
        self._update_grid_header()
        self._set_status("Pronto. Clique na grade para editar.")

    def _first_filled_section(self) -> str | None:
        for section in SECTIONS:
            if self.project.section_note_count(section["id"]) > 0:
                return section["id"]
        return None

    def _replace_project(self, project: Project) -> None:
        self.project = project
        self.current_section = self._first_filled_section() or "var1"
        self._load_project_into_ui()

    def _mark_dirty(self) -> None:
        if not self.dirty:
            self.dirty = True
            self._update_titles()

    def _update_titles(self) -> None:
        suffix = "  •  alterado" if self.dirty else ""
        self.subtitle.set_text(
            f"{self.project.name or 'Sem nome'}  •  {self.project.tempo} BPM{suffix}"
        )
        self.set_title(f"{self.project.name or 'Sem nome'} — Editor CT-X800")
        self.bpm_label.set_text(
            f"{self.project.tempo} BPM  •  {self.project.time_signature}  •  1/16")

    def _update_section_counts(self) -> None:
        for section in SECTIONS:
            count = self.project.section_note_count(section["id"])
            label = self.section_counts.get(section["id"])
            if label is None:
                continue
            if count == 0:
                label.set_text("vazia")
            else:
                label.set_text(f"{count} nota{'s' if count != 1 else ''}")

    def _update_grid_header(self) -> None:
        section = SECTION_BY_ID[self.current_section]
        part = self.project.part(self.current_part_number)
        notes = len(self.project.notes(self.current_section,
                                       self.current_part_number))
        self.grid_title.set_text(f"{section['name']}  —  {part.name}")
        detail = (f"{part.tone.label}  •  {notes} nota"
                  f"{'s' if notes != 1 else ''}  •  {self.project.bars(self.current_section)}"
                  f" compasso{'s' if self.project.bars(self.current_section) != 1 else ''}"
                  f"  •  {self.project.time_signature}")
        if part.mute:
            detail += "  •  MUDO"
        self.grid_info.set_text(detail)

    def _update_part_controls(self) -> None:
        part = self.project.part(self.current_part_number)
        self._part_loading = True
        try:
            self.instrument_name.set_text(part.tone.label)
            self.instrument_detail.set_text(
                f"{CATEGORY_LABELS.get(part.tone.category, part.tone.category_name)}"
                f"  •  program {part.tone.program} / bank {part.tone.bank}"
            )
            self.volume_spin.set_value(part.volume)
            self.pan_spin.set_value(part.pan)
            self.reverb_spin.set_value(part.reverb)
            self.chorus_spin.set_value(part.chorus)
            self.mute_switch.set_active(part.mute)
            self.chord_switch.set_active(part.chord_sync)
            self.chord_switch.set_sensitive(not part.drum)
            self.length_dropdown.set_sensitive(not part.drum)
            if part.drum:
                self.length_dropdown.set_selected(0)
                self.editor.set_note_length(1)
            else:
                index = self.length_dropdown.get_selected()
                if index == Gtk.INVALID_LIST_POSITION:
                    index = 0
                self.editor.set_note_length(NOTE_LENGTHS[index])
            self.editor.set_velocity(int(self.velocity_spin.get_value()))
        finally:
            self._part_loading = False

    def _update_bars_spin(self) -> None:
        self._section_loading = True
        try:
            self.bars_spin.set_value(self.project.bars(self.current_section))
        finally:
            self._section_loading = False

    def _set_status(self, text: str) -> None:
        self.status_label.set_text(text)

    def _toast(self, text: str) -> None:
        toast = Adw.Toast(title=text)
        toast.set_timeout(3)
        self.toast_overlay.add_toast(toast)

    # ================================================================ sinais
    def _on_name_changed(self, entry: Gtk.Entry) -> None:
        if self._loading:
            return
        text = entry.get_text().strip()
        self.project.name = text or "MEU RITMO"
        self._mark_dirty()
        self._update_titles()

    def _on_tempo_changed(self, spin: Gtk.SpinButton) -> None:
        if self._loading:
            return
        value = int(spin.get_value())
        if value == self.project.tempo:
            return
        self.project.tempo = value
        self._stop_playback()
        self._mark_dirty()
        self._update_titles()

    def _on_signature_changed(self, dropdown: Gtk.DropDown, _pspec) -> None:
        if self._loading:
            return
        index = dropdown.get_selected()
        if index == Gtk.INVALID_LIST_POSITION or index >= len(SIGNATURES):
            return
        signature = SIGNATURES[index]
        if signature == self.project.time_signature:
            return
        if self._signature_would_clip(signature):
            self._confirm(
                "Mudar o compasso?",
                "As notas que ficarem fora do novo tamanho das seções serão "
                "removidas.",
                "Mudar",
                lambda: self._apply_signature(signature),
                lambda: self._reset_signature_dropdown(),
            )
        else:
            self._apply_signature(signature)

    def _signature_would_clip(self, signature: str) -> bool:
        spb = steps_per_bar_for(signature)
        for section in SECTIONS:
            limit = self.project.bars(section["id"]) * spb
            for part in self.project.parts:
                for note in self.project.notes(section["id"], part.number):
                    if note.start >= limit or note.start + note.length > limit:
                        return True
        return False

    def _apply_signature(self, signature: str) -> None:
        self._stop_playback()
        self.project.set_time_signature(signature)
        self._loading = True
        try:
            self.signature_dropdown.set_selected(SIGNATURES.index(signature))
        finally:
            self._loading = False
        self._mark_dirty()
        self.editor.set_context(self.project, self.current_section,
                                self.current_part_number)
        self._update_bars_spin()
        self._update_section_counts()
        self._update_grid_header()
        self._set_status(f"Compasso alterado para {signature}.")

    def _reset_signature_dropdown(self) -> None:
        self._loading = True
        try:
            self.signature_dropdown.set_selected(
                SIGNATURES.index(self.project.time_signature))
        finally:
            self._loading = False

    def _on_section_selected(self, _listbox: Gtk.ListBox,
                             row: Gtk.ListBoxRow | None) -> None:
        if self._loading or row is None:
            return
        section_id = getattr(row, "section_id", None)
        if not section_id or section_id == self.current_section:
            return
        self.current_section = section_id
        self._stop_playback()
        self._update_bars_spin()
        self.editor.set_context(self.project, self.current_section,
                                self.current_part_number)
        self._update_grid_header()
        self._set_status("Pronto.")
        self.editor.grab_focus()

    def _on_bars_changed(self, spin: Gtk.SpinButton) -> None:
        if self._section_loading or self._loading:
            return
        new_bars = int(spin.get_value())
        old_bars = self.project.bars(self.current_section)
        if new_bars == old_bars:
            return
        limit = new_bars * self.project.steps_per_bar
        would_clip = any(
            note.start >= limit or note.start + note.length > limit
            for part in self.project.parts
            for note in self.project.notes(self.current_section, part.number)
        )
        if new_bars < old_bars and would_clip:
            self._confirm(
                "Reduzir a seção?",
                "As notas que ficarem fora do novo tamanho serão removidas.",
                "Reduzir",
                lambda: self._apply_bars(new_bars),
                lambda: self._reset_bars_spin(old_bars),
            )
        else:
            self._apply_bars(new_bars)

    def _apply_bars(self, bars: int) -> None:
        self._stop_playback()
        self.project.set_bars(self.current_section, bars)
        self._update_bars_spin()
        self._mark_dirty()
        self.editor.set_context(self.project, self.current_section,
                                self.current_part_number)
        self._update_section_counts()
        self._update_grid_header()
        self._set_status(f"Seção com {bars} compasso{'s' if bars != 1 else ''}.")

    def _reset_bars_spin(self, bars: int) -> None:
        self._section_loading = True
        try:
            self.bars_spin.set_value(bars)
        finally:
            self._section_loading = False

    def _on_part_changed(self, dropdown: Gtk.DropDown, _pspec) -> None:
        if self._loading:
            return
        index = dropdown.get_selected()
        if index == Gtk.INVALID_LIST_POSITION or not 0 <= index < len(
                self.project.parts):
            return
        self.current_part_number = self.project.parts[index].number
        self._stop_playback()
        self.editor.set_context(self.project, self.current_section,
                                self.current_part_number)
        self._update_part_controls()
        self._update_grid_header()
        self._set_status("Pronto.")
        self.editor.grab_focus()

    def _on_part_value(self, spin: Gtk.SpinButton, attr: str) -> None:
        if self._part_loading or self._loading:
            return
        part = self.project.part(self.current_part_number)
        value = int(spin.get_value())
        if getattr(part, attr) == value:
            return
        setattr(part, attr, value)
        self._stop_playback()
        self._mark_dirty()
        self._update_grid_header()

    def _on_mute_changed(self, switch: Gtk.Switch, _pspec) -> None:
        if self._part_loading or self._loading:
            return
        part = self.project.part(self.current_part_number)
        part.mute = bool(switch.get_active())
        self._stop_playback()
        self._mark_dirty()
        self.editor.queue_draw()
        self._update_grid_header()
        self._set_status("Parte muda." if part.mute else "Parte ativa.")

    def _on_chord_changed(self, switch: Gtk.Switch, _pspec) -> None:
        if self._part_loading or self._loading:
            return
        part = self.project.part(self.current_part_number)
        part.chord_sync = bool(switch.get_active())
        self._mark_dirty()

    def _on_length_changed(self, dropdown: Gtk.DropDown, _pspec) -> None:
        if self._part_loading or self._loading:
            return
        index = dropdown.get_selected()
        if index == Gtk.INVALID_LIST_POSITION:
            index = 0
        self.editor.set_note_length(NOTE_LENGTHS[index])

    def _on_velocity_changed(self, spin: Gtk.SpinButton) -> None:
        if self._part_loading or self._loading:
            return
        self.editor.set_velocity(int(spin.get_value()))

    def _on_notes_changed(self) -> None:
        self._stop_playback()
        self._mark_dirty()
        self._update_section_counts()
        self._update_grid_header()
        self._set_status("Alterado. Não esqueça de salvar.")

    def _choose_instrument(self) -> None:
        number = self.current_part_number
        part = self.project.part(number)
        dialog = InstrumentDialog(
            self, part,
            lambda tone: self._on_instrument_selected_for(number, tone),
        )
        self._dialog = dialog
        dialog.present()

    def _open_mixer(self) -> None:
        if self._mixer_dialog is not None:
            self._mixer_dialog.present()
            return
        dialog = MixerDialog(self, self.project, self._on_mixer_changed)
        self._mixer_dialog = dialog
        dialog.connect("close-request", self._on_mixer_closed)
        dialog.present()

    def _on_mixer_closed(self, *_args) -> bool:
        self._mixer_dialog = None
        return False

    def _on_mixer_changed(self, part_number: int) -> None:
        self._stop_playback()
        self._mark_dirty()
        if part_number == self.current_part_number:
            self._update_part_controls()
            self.editor.queue_draw()
        self._update_grid_header()
        self._set_status("Misturador alterado.")

    def _on_instrument_selected_for(self, number: int, tone) -> None:
        part = self.project.part(number)
        if part.drum != tone.drum:
            self._show_error(
                "Instrumento incompatível",
                "Partes de bateria usam kits de bateria; as demais usam tons "
                "melódicos.",
            )
            return
        part.tone_number = tone.number
        self._stop_playback()
        self._mark_dirty()
        if number == self.current_part_number:
            self._update_part_controls()
        self._update_grid_header()
        self._set_status(f"Instrumento: {tone.label}.")

    def _clear_clicked(self) -> None:
        section = SECTION_BY_ID[self.current_section]
        part = self.project.part(self.current_part_number)
        notes = self.project.notes(self.current_section,
                                   self.current_part_number)
        if not notes:
            self._toast("Esta parte já está vazia nesta seção.")
            return
        self._confirm(
            "Limpar a parte?",
            f"Apagar as {len(notes)} notas de {part.name} na seção "
            f"{section['name']}?",
            "Limpar",
            self._clear_part,
        )

    def _clear_part(self) -> None:
        self.project.clear_part(self.current_section, self.current_part_number)
        self._on_notes_changed()
        self.editor.queue_draw()
        self._toast("Parte limpa.")

    def _clear_section_clicked(self) -> None:
        section = SECTION_BY_ID[self.current_section]
        count = self.project.section_note_count(self.current_section)
        if count == 0:
            self._toast("Esta seção já está vazia.")
            return
        self._confirm(
            "Limpar a seção?",
            f"Apagar as {count} notas da seção {section['name']} em todas as "
            "partes?",
            "Limpar",
            self._clear_section,
        )

    def _clear_section(self) -> None:
        self.project.clear_section(self.current_section)
        self._on_notes_changed()
        self.editor.queue_draw()
        self._toast("Seção limpa.")

    # ================================================================ tocar
    def _toggle_play(self) -> None:
        if self._playing:
            self._stop_playback()
            return
        if self._starting:
            return
        if self.audio is None:
            self._show_error(
                "Áudio indisponível",
                f"Não foi possível iniciar o áudio:\n{self._audio_error}",
            )
            return
        if self.project.active_note_count(self.current_section) == 0:
            self._toast("A seção não tem notas em partes ativas.")
            return
        self._starting = True
        self.play_button.set_sensitive(False)
        self._set_status("Preparando o áudio…")
        GLib.idle_add(self._begin_playback)

    def _begin_playback(self) -> bool:
        self._starting = False
        self.play_button.set_sensitive(True)
        if self.audio is None:
            return GLib.SOURCE_REMOVE
        try:
            wav = self.audio.render(self.project, self.current_section)
            self.audio.play(wav, loop=True)
        except Exception as exc:
            self._show_error("Não foi possível tocar", str(exc))
            self._set_status("Pronto.")
            return GLib.SOURCE_REMOVE
        self._playing = True
        self._play_started = time.monotonic()
        self.play_button.set_icon_name("media-playback-stop-symbolic")
        self.play_button.set_tooltip_text("Parar (Espaço)")
        section = SECTION_BY_ID[self.current_section]
        self._set_status(f"Tocando {section['name']} em loop…")
        self._tick_id = GLib.timeout_add(30, self._on_tick)
        return GLib.SOURCE_REMOVE

    def _on_tick(self) -> bool:
        if not self._playing:
            self._tick_id = None
            return GLib.SOURCE_REMOVE
        steps = self.project.steps(self.current_section)
        step_seconds = 60.0 / max(20, self.project.tempo) / 4.0
        position = 0.0
        if self.audio is not None:
            position = self.audio.position()
        if position <= 0:
            position = time.monotonic() - self._play_started
        step = int(position / step_seconds) % max(1, steps)
        self.editor.set_playhead(step)
        return GLib.SOURCE_CONTINUE

    def _cancel_tick(self) -> None:
        if self._tick_id is not None:
            try:
                GLib.source_remove(self._tick_id)
            except Exception:
                pass
            self._tick_id = None

    def _stop_playback(self) -> None:
        self._starting = False
        self.play_button.set_sensitive(True)
        if self._playing:
            self._playing = False
            self._cancel_tick()
            self.editor.set_playhead(None)
            self.play_button.set_icon_name("media-playback-start-symbolic")
            self.play_button.set_tooltip_text("Tocar a seção (Espaço)")
        if self.audio is not None:
            self.audio.stop()

    def _on_audio_finished(self) -> None:
        if not self._playing:
            return
        self._playing = False
        self._cancel_tick()
        self.editor.set_playhead(None)
        self.play_button.set_icon_name("media-playback-start-symbolic")
        self.play_button.set_tooltip_text("Tocar a seção (Espaço)")
        self._set_status("Pronto.")

    # ================================================================ arquivos
    def _new_clicked(self) -> None:
        self._confirm_discard(self._new_project)

    def _new_project(self) -> None:
        self._stop_playback()
        self.project = empty_project()
        self.current_section = "var1"
        self.current_part_number = 2
        self._load_project_into_ui()
        self._toast("Novo projeto criado.")

    def _open_clicked(self) -> None:
        self._confirm_discard(self._choose_open_file)

    def _choose_open_file(self) -> None:
        dialog = Gtk.FileDialog()
        dialog.set_title("Abrir projeto do editor")
        dialog.set_modal(True)
        dialog.set_initial_folder(Gio.File.new_for_path(str(self.projects_dir)))
        filters = Gio.ListStore.new(Gtk.FileFilter)
        project_filter = Gtk.FileFilter()
        project_filter.set_name("Projetos do editor (*.json)")
        project_filter.add_pattern("*.json")
        filters.append(project_filter)
        dialog.set_filters(filters)
        dialog.set_default_filter(project_filter)
        self._dialog = dialog
        dialog.open(self, None, self._on_open_done)

    def _on_open_done(self, dialog: Gtk.FileDialog, result) -> None:
        try:
            file = dialog.open_finish(result)
        except GLib.Error:
            return
        if file is None or file.get_path() is None:
            return
        path = Path(file.get_path())
        try:
            project = Project.load(path)
        except Exception as exc:
            self._show_error("Não foi possível abrir o projeto",
                             f"{path}\n\n{exc}")
            return
        self._replace_project(project)
        self._toast(f"Projeto aberto: {path.name}")

    def _save_clicked(self) -> None:
        if self.project.path is None:
            self._save_as_clicked()
            return
        self._save_project(self.project.path)

    def _save_as_clicked(self) -> None:
        dialog = Gtk.FileDialog()
        dialog.set_title("Salvar projeto do editor")
        dialog.set_modal(True)
        dialog.set_initial_folder(Gio.File.new_for_path(str(self.projects_dir)))
        initial = self.project.path.name if self.project.path else \
            f"{self.project.display_name()}.ctx800.json"
        dialog.set_initial_name(initial)
        filters = Gio.ListStore.new(Gtk.FileFilter)
        project_filter = Gtk.FileFilter()
        project_filter.set_name("Projetos do editor (*.json)")
        project_filter.add_pattern("*.json")
        filters.append(project_filter)
        dialog.set_filters(filters)
        dialog.set_default_filter(project_filter)
        self._dialog = dialog
        dialog.save(self, None, self._on_save_done)

    def _on_save_done(self, dialog: Gtk.FileDialog, result) -> None:
        try:
            file = dialog.save_finish(result)
        except GLib.Error:
            return
        if file is None or file.get_path() is None:
            return
        path = Path(file.get_path())
        if path.suffix.lower() != ".json":
            path = path.with_name(path.name + ".ctx800.json")
        self._save_project(path)

    def _save_project(self, path: Path) -> None:
        try:
            self.project.save(path)
        except OSError as exc:
            self._show_error("Não foi possível salvar", f"{path}\n\n{exc}")
            return
        self.dirty = False
        self._update_titles()
        self._set_status(f"Salvo em {path}.")
        self._toast(f"Projeto salvo: {path.name}")

    def _export_clicked(self) -> None:
        if not self.project.has_active_notes():
            self._show_error(
                "Projeto sem notas ativas",
                "Adicione notas ou desmute alguma parte antes de exportar.",
            )
            return
        dialog = Gtk.FileDialog()
        dialog.set_title("Exportar ritmo AC7")
        dialog.set_modal(True)
        dialog.set_initial_folder(Gio.File.new_for_path(str(self.exports_dir)))
        dialog.set_initial_name(f"{self.project.display_name()}.AC7")
        filters = Gio.ListStore.new(Gtk.FileFilter)
        ac7_filter = Gtk.FileFilter()
        ac7_filter.set_name("Ritmo Casio (*.AC7)")
        ac7_filter.add_pattern("*.AC7")
        ac7_filter.add_pattern("*.ac7")
        filters.append(ac7_filter)
        dialog.set_filters(filters)
        dialog.set_default_filter(ac7_filter)
        self._dialog = dialog
        dialog.save(self, None, self._on_export_done)

    def _on_export_done(self, dialog: Gtk.FileDialog, result) -> None:
        try:
            file = dialog.save_finish(result)
        except GLib.Error:
            return
        if file is None or file.get_path() is None:
            return
        path = Path(file.get_path())
        if path.suffix.lower() != ".ac7":
            path = path.with_name(path.name + ".AC7")
        self._set_status("Exportando AC7…")
        try:
            export = export_ac7(self.project, path)
        except ExportError as exc:
            self._set_status("Pronto.")
            self._show_error("Não foi possível exportar", str(exc))
            return
        except Exception as exc:
            self._set_status("Pronto.")
            self._show_error("Erro inesperado na exportação", str(exc))
            return
        self._last_export = export
        self._set_status(f"AC7 exportado: {export.path} "
                         f"({export.size / 1024:.1f} KB)")
        self._show_export_success(export)

    def _show_export_success(self, export: ExportResult) -> None:
        body = (
            f"{export.path}\n\n"
            f"{export.size / 1024:.1f} KB  •  {export.tracks} faixas  •  "
            f"SHA-256 {export.sha256[:16]}…\n\n"
            "Copie o arquivo para a pasta MUSICDAT de um pendrive FAT32."
        )
        if export.warnings:
            body += "\n\n" + "\n".join(export.warnings)
        dialog = Adw.AlertDialog(heading="AC7 exportado", body=body)
        dialog.add_response("close", "Fechar")
        dialog.add_response("open", "Abrir pasta")
        dialog.set_response_appearance("open",
                                       Adw.ResponseAppearance.SUGGESTED)
        dialog.set_default_response("open")
        dialog.set_close_response("close")

        def done(dlg, result):
            try:
                response = dlg.choose_finish(result)
            except GLib.Error:
                return
            if response == "open":
                try:
                    Gio.AppInfo.launch_default_for_uri(
                        export.path.parent.as_uri(), None)
                except Exception as exc:
                    self._show_error("Não foi possível abrir a pasta", str(exc))

        self._dialog = dialog
        dialog.choose(self, None, done)

    # ================================================================ diálogos
    def _confirm(self, heading: str, body: str, action_label: str,
                 on_confirm, on_cancel=None) -> None:
        dialog = Adw.AlertDialog(heading=heading, body=body)
        dialog.add_response("cancel", "Cancelar")
        dialog.add_response("confirm", action_label)
        dialog.set_response_appearance("confirm",
                                       Adw.ResponseAppearance.DESTRUCTIVE)
        dialog.set_default_response("cancel")
        dialog.set_close_response("cancel")

        def done(dlg, result):
            try:
                response = dlg.choose_finish(result)
            except GLib.Error:
                response = "cancel"
            if response == "confirm":
                on_confirm()
            elif on_cancel is not None:
                on_cancel()

        self._dialog = dialog
        dialog.choose(self, None, done)

    def _confirm_discard(self, on_discard) -> None:
        if not self.dirty:
            on_discard()
            return
        dialog = Adw.AlertDialog(
            heading="Descartar alterações?",
            body="O projeto atual tem alterações que ainda não foram salvas.",
        )
        dialog.add_response("cancel", "Cancelar")
        dialog.add_response("discard", "Descartar")
        dialog.set_response_appearance("discard",
                                       Adw.ResponseAppearance.DESTRUCTIVE)
        dialog.set_default_response("cancel")
        dialog.set_close_response("cancel")

        def done(dlg, result):
            try:
                response = dlg.choose_finish(result)
            except GLib.Error:
                return
            if response == "discard":
                on_discard()

        self._dialog = dialog
        dialog.choose(self, None, done)

    def _show_error(self, heading: str, body: str) -> None:
        dialog = Adw.AlertDialog(heading=heading, body=body)
        dialog.add_response("close", "Fechar")
        dialog.set_default_response("close")
        dialog.set_close_response("close")
        self._dialog = dialog
        dialog.present(self)

    def _about_clicked(self) -> None:
        dialog = Adw.AboutDialog(
            application_name=APP_NAME,
            application_icon="audio-x-generic",
            developer_name="Projeto CT-X800",
            version=VERSION,
            comments=(
                "Editor gráfico de ritmos para o Casio CT-X800.\n\n"
                "12 seções, 8 partes, 600 tons do teclado, mixer por parte, "
                "prévia com SoundFont GM e exportação .AC7 para a pasta "
                "MUSICDAT do pendrive.\n\n"
                "O som da prévia no PC é uma aproximação; os timbres reais "
                "são os do teclado."
            ),
            license_type=Gtk.License.GPL_3_0,
        )
        dialog.add_credit_section(
            "AC7", ["ac7maker — https://github.com/michgz/ac7maker"])
        self._dialog = dialog
        dialog.present(self)

    # ================================================================ fechar
    def _on_close_request(self, _window) -> bool:
        if self._allow_close or not self.dirty:
            self._stop_playback()
            if self.audio is not None:
                self.audio.close()
            return False
        self._confirm_discard(self._force_close)
        return True

    def _force_close(self) -> None:
        self._allow_close = True
        self._stop_playback()
        if self.audio is not None:
            self.audio.close()
        self.close()


class EditorApplication(Adw.Application):
    def __init__(self) -> None:
        super().__init__(application_id=APP_ID,
                         flags=Gio.ApplicationFlags.DEFAULT_FLAGS)
        self.window: EditorWindow | None = None

    def do_startup(self) -> None:
        Adw.Application.do_startup(self)
        accelerators = {
            "win.new": ["<Primary>n"],
            "win.open": ["<Primary>o"],
            "win.save": ["<Primary>s"],
            "win.save_as": ["<Primary><Shift>s"],
            "win.export": ["<Primary>e"],
            "win.mixer": ["<Primary>m"],
            "win.play": ["space"],
        }
        for action, accels in accelerators.items():
            self.set_accels_for_action(action, accels)

    def do_activate(self) -> None:
        if self.window is None:
            self.window = EditorWindow(application=self)
        self.window.present()
        self.window.editor.grab_focus()

    def do_shutdown(self) -> None:
        if self.window is not None and self.window.audio is not None:
            self.window.audio.on_finished = None
            self.window.audio.close()
        Adw.Application.do_shutdown(self)


def main() -> int:
    app = EditorApplication()
    return app.run(sys.argv)


if __name__ == "__main__":
    raise SystemExit(main())
