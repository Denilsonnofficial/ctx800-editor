"""Diálogo de escolha de instrumento (tone) do CT-X800."""
from __future__ import annotations

import gi

gi.require_version("Gtk", "4.0")
gi.require_version("Adw", "1")
gi.require_version("Pango", "1.0")

from gi.repository import Adw, GLib, Gtk, Pango  # noqa: E402

from audio import AudioError, TonePreview  # noqa: E402
from tones import (  # noqa: E402
    CATEGORY_LABELS, Tone, category_label, drum_tones, melodic_tones,
)


class InstrumentDialog(Adw.Window):
    """Janela modal com busca e categorias dos 600 tons do teclado."""

    def __init__(self, parent: Gtk.Window, part, on_selected) -> None:
        super().__init__(transient_for=parent, modal=True)
        self.set_destroy_with_parent(True)
        self.set_title("Escolher instrumento")
        self.set_default_size(580, 680)
        self.part = part
        self.on_selected = on_selected
        self._tones = drum_tones() if part.drum else melodic_tones()
        self._category = None
        self._suppress_preview = True
        self._preview_timeout: int | None = None
        self.preview: TonePreview | None = None
        self._preview_error = ""
        try:
            self.preview = TonePreview()
        except Exception as exc:
            self._preview_error = str(exc)

        toolbar = Adw.ToolbarView()
        header = Gtk.HeaderBar()
        cancel = Gtk.Button(label="Cancelar")
        cancel.connect("clicked", lambda _b: self.close())
        header.pack_start(cancel)
        accept = Gtk.Button(label="Usar tom")
        accept.add_css_class("suggested-action")
        accept.connect("clicked", lambda _b: self._accept_selected())
        header.pack_end(accept)
        self._accept_button = accept

        listen = Gtk.Button.new_from_icon_name("audio-volume-high-symbolic")
        listen.set_tooltip_text("Ouvir o instrumento selecionado")
        listen.connect("clicked", lambda _b: self._play_selected_now())
        header.pack_end(listen)
        self._listen_button = listen
        toolbar.add_top_bar(header)

        content = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=8)
        content.set_margin_top(12)
        content.set_margin_bottom(12)
        content.set_margin_start(12)
        content.set_margin_end(12)

        hint = Gtk.Label(
            label=("Kit de bateria — clique em um nome para ouvir e use "
                   "'Usar tom' para aplicar"
                   if part.drum
                   else "Tons do CT-X800 — clique em um nome para ouvir e use "
                   "'Usar tom' para aplicar"),
            xalign=0,
        )
        hint.add_css_class("caption")
        hint.add_css_class("dim-label")
        content.append(hint)

        search_row = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=8)
        self.search = Gtk.SearchEntry()
        self.search.set_placeholder_text("Nome ou número (ex.: 184)")
        self.search.set_hexpand(True)
        self.search.connect("search-changed", lambda _e: self._invalidate())
        search_row.append(self.search)

        if not part.drum:
            category_names = ["Todas"] + [
                CATEGORY_LABELS[number]
                for number in sorted(CATEGORY_LABELS)
                if number != 14
            ]
            self.category_dropdown = Gtk.DropDown.new_from_strings(
                category_names)
            self.category_dropdown.set_selected(0)
            self.category_dropdown.connect(
                "notify::selected", lambda _d, _p: self._category_changed())
            search_row.append(self.category_dropdown)
        else:
            self.category_dropdown = None
        content.append(search_row)

        self.listbox = Gtk.ListBox()
        self.listbox.set_selection_mode(Gtk.SelectionMode.SINGLE)
        self.listbox.set_activate_on_single_click(True)
        self.listbox.set_filter_func(self._filter_row)
        self.listbox.connect("row-activated", self._on_row_activated)
        self.listbox.connect("row-selected", self._on_row_selected)
        self.rows: list[Gtk.ListBoxRow] = []
        selected_row = None
        for tone in self._tones:
            row = Gtk.ListBoxRow()
            row.tone = tone  # type: ignore[attr-defined]
            box = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=10)
            box.set_margin_top(4)
            box.set_margin_bottom(4)
            box.set_margin_start(8)
            box.set_margin_end(8)
            number = Gtk.Label(label=f"{tone.number:03d}")
            number.set_width_chars(4)
            number.add_css_class("monospace")
            number.add_css_class("dim-label")
            name = Gtk.Label(label=tone.name, xalign=0)
            name.set_hexpand(True)
            name.set_ellipsize(Pango.EllipsizeMode.END)
            category = Gtk.Label(label=category_label(tone.category), xalign=1)
            category.add_css_class("caption")
            category.add_css_class("dim-label")
            box.append(number)
            box.append(name)
            box.append(category)
            row.set_child(box)
            self.listbox.append(row)
            self.rows.append(row)
            if tone.number == part.tone_number:
                selected_row = row

        scroller = Gtk.ScrolledWindow()
        scroller.set_policy(Gtk.PolicyType.NEVER, Gtk.PolicyType.AUTOMATIC)
        scroller.set_child(self.listbox)
        scroller.set_vexpand(True)
        scroller.add_css_class("card")
        content.append(scroller)
        self._scroller = scroller
        self._selected_row = selected_row

        self.preview_status = Gtk.Label(
            label=("Clique em um nome para ouvir" if self.preview is not None
                   else f"Prévia de áudio indisponível: {self._preview_error}"),
            xalign=0,
        )
        self.preview_status.add_css_class("caption")
        self.preview_status.add_css_class("dim-label")
        self.preview_status.set_ellipsize(Pango.EllipsizeMode.END)
        content.append(self.preview_status)

        toolbar.set_content(content)
        self.set_content(toolbar)
        self.connect("close-request", self._on_close_request)

        if selected_row is not None:
            self.listbox.select_row(selected_row)
            GLib.idle_add(self._scroll_to_selected)
        self._update_accept()

    def _scroll_to_selected(self) -> bool:
        row = self._selected_row
        if row is None:
            self._suppress_preview = False
            return GLib.SOURCE_REMOVE
        self.listbox.select_row(row)
        row.grab_focus()
        allocation = row.get_allocation()
        adjustment = self._scroller.get_vadjustment()
        adjustment.set_value(max(0, allocation.y - 80))
        self._suppress_preview = False
        return GLib.SOURCE_REMOVE

    def _on_row_selected(self, _listbox, row: Gtk.ListBoxRow | None) -> None:
        self._update_accept()
        if self._suppress_preview or row is None or self.preview is None:
            return
        tone = getattr(row, "tone", None)
        if tone is None:
            return
        if self._preview_timeout is not None:
            try:
                GLib.source_remove(self._preview_timeout)
            except Exception:
                pass
        self._preview_timeout = GLib.timeout_add(
            120, self._play_preview, tone)

    def _play_preview(self, tone: Tone) -> bool:
        self._preview_timeout = None
        if self.preview is None:
            return GLib.SOURCE_REMOVE
        self.preview_status.set_text(f"Ouvindo: {tone.label}")
        try:
            self.preview.play(tone)
        except AudioError as exc:
            self.preview_status.set_text(f"Prévia indisponível: {exc}")
        return GLib.SOURCE_REMOVE

    def _play_now(self, tone: Tone) -> None:
        if self._preview_timeout is not None:
            try:
                GLib.source_remove(self._preview_timeout)
            except Exception:
                pass
            self._preview_timeout = None
        self._play_preview(tone)

    def _play_selected_now(self) -> None:
        row = self.listbox.get_selected_row()
        if row is None:
            return
        tone = getattr(row, "tone", None)
        if tone is not None:
            self._play_now(tone)

    def _on_close_request(self, *_args) -> bool:
        if self._preview_timeout is not None:
            try:
                GLib.source_remove(self._preview_timeout)
            except Exception:
                pass
            self._preview_timeout = None
        if self.preview is not None:
            self.preview.close()
        return False

    # ------------------------------------------------------------- filtros
    def _invalidate(self) -> None:
        self.listbox.invalidate_filter()
        self._update_accept()

    def _category_changed(self) -> None:
        index = self.category_dropdown.get_selected()
        if index in (Gtk.INVALID_LIST_POSITION, 0):
            self._category = None
        else:
            categories = [
                number for number in sorted(CATEGORY_LABELS) if number != 14
            ]
            self._category = categories[index - 1] if index - 1 < len(categories) else None
        self._invalidate()

    def _filter_row(self, row: Gtk.ListBoxRow) -> bool:
        tone: Tone = row.tone  # type: ignore[attr-defined]
        if self._category is not None and tone.category != self._category:
            return False
        query = self.search.get_text().strip().upper()
        if not query:
            return True
        if query.isdigit():
            return f"{tone.number:03d}".startswith(query)
        return query in tone.name.upper()

    def _update_accept(self) -> None:
        self._accept_button.set_sensitive(
            self.listbox.get_selected_row() is not None)

    def _on_row_activated(self, _listbox, row: Gtk.ListBoxRow) -> None:
        tone = getattr(row, "tone", None)
        if tone is not None:
            self._play_now(tone)

    def _accept_selected(self) -> None:
        row = self.listbox.get_selected_row()
        if row is not None:
            tone = getattr(row, "tone", None)
            if tone is not None:
                self._accept(tone)

    def _accept(self, tone: Tone) -> None:
        self.on_selected(tone)
        self.close()
