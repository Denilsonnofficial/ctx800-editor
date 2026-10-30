"""Grade de edição (bateria e piano roll) desenhada com Cairo/GTK4."""
from __future__ import annotations

import math

import gi

gi.require_version("Gtk", "4.0")
gi.require_version("Adw", "1")
gi.require_version("PangoCairo", "1.0")

from gi.repository import Adw, Gdk, Gtk, Pango, PangoCairo  # noqa: E402

from model import (  # noqa: E402
    BLACK_KEYS, DRUM_LABELS, DRUM_ROWS, MELODIC_RANGES, Note, Project,
    note_name,
)

LABEL_W_DRUM = 118
LABEL_W_MELODIC = 64
HEADER_H = 26
FILL_CELL_W = 28
BAR_CELL_W = 11
DRUM_ROW_H = 24
MELODIC_ROW_H = 18


def _rgb(rgba: Gdk.RGBA) -> tuple[float, float, float]:
    return (rgba.red, rgba.green, rgba.blue)


class PatternEditor(Gtk.DrawingArea):
    """Grade de passos de 1/16.

    Bateria: linhas fixas de notas GM, uma nota por célula.
    Melódicas: piano roll de duas oitavas, notas com duração escolhida.
    """

    def __init__(self, on_changed=None) -> None:
        super().__init__()
        self.project: Project | None = None
        self.section_id: str | None = None
        self.part_number: int | None = None
        self.note_length = 1
        self.velocity = 100
        self.on_changed = on_changed
        self._playhead: int | None = None
        self._hover: tuple[int, int] | None = None
        self._dark = False
        self._rows: list[int] = []

        self.set_draw_func(self._draw)
        self.set_hexpand(True)
        self.set_vexpand(True)
        self.set_focusable(True)

        try:
            self.set_cursor(Gdk.Cursor.new_from_name("crosshair", None))
        except Exception:
            pass

        click = Gtk.GestureClick()
        click.set_button(0)
        click.connect("pressed", self._on_click)
        self.add_controller(click)

        motion = Gtk.EventControllerMotion()
        motion.connect("motion", self._on_motion)
        motion.connect("leave", self._on_leave)
        self.add_controller(motion)

        try:
            manager = Adw.StyleManager.get_default()
            self._dark = manager.get_dark()
            manager.connect("notify::dark", lambda *_args: self.queue_draw())
        except Exception:
            self._dark = False

    # ------------------------------------------------------------- contexto
    def set_context(self, project: Project, section_id: str,
                    part_number: int) -> None:
        self.project = project
        self.section_id = section_id
        self.part_number = part_number
        self._playhead = None
        self._hover = None
        self._update_rows()
        self._update_size()
        self.queue_draw()

    def set_note_length(self, length: int) -> None:
        self.note_length = max(1, int(length))

    def set_velocity(self, velocity: int) -> None:
        self.velocity = max(1, min(127, int(velocity)))

    def refresh(self) -> None:
        self._update_rows()
        self._update_size()
        self.queue_draw()

    def set_playhead(self, step: int | None) -> None:
        if step != self._playhead:
            self._playhead = step
            self.queue_draw()

    # ------------------------------------------------------------- geometria
    @property
    def part(self):
        if not self.project or self.part_number is None:
            return None
        return self.project.part(self.part_number)

    @property
    def is_drum(self) -> bool:
        return bool(self.part and self.part.drum)

    @property
    def steps(self) -> int:
        if not self.project or not self.section_id:
            return 0
        return self.project.steps(self.section_id)

    @property
    def label_w(self) -> int:
        return LABEL_W_DRUM if self.is_drum else LABEL_W_MELODIC

    @property
    def cell_w(self) -> int:
        # Compassos de 1 bar ficam mais largos; seções de 4 barras cabem
        # praticamente inteiras na janela padrão.
        spb = self.project.steps_per_bar if self.project else 16
        return FILL_CELL_W if self.steps <= spb else BAR_CELL_W

    @property
    def row_h(self) -> int:
        return DRUM_ROW_H if self.is_drum else MELODIC_ROW_H

    def _update_rows(self) -> None:
        if self.part_number is None:
            self._rows = []
            return
        if self.is_drum:
            self._rows = [pitch for pitch, _name in DRUM_ROWS]
        else:
            low, high = MELODIC_RANGES.get(self.part_number, (48, 71))
            self._rows = list(range(high, low - 1, -1))

    def _update_size(self) -> None:
        rows = max(1, len(self._rows))
        width = self.label_w + max(1, self.steps) * self.cell_w
        height = HEADER_H + rows * self.row_h
        self.set_content_width(width)
        self.set_content_height(height)
        self.queue_resize()

    def _cell_at(self, x: float, y: float) -> tuple[int, int, int] | None:
        if not self._rows or not self.project:
            return None
        if x < self.label_w or y < HEADER_H:
            return None
        step = int((x - self.label_w) // self.cell_w)
        row = int((y - HEADER_H) // self.row_h)
        if step < 0 or step >= self.steps or row < 0 or row >= len(self._rows):
            return None
        return step, row, self._rows[row]

    def _cell_rect(self, step: int, row: int) -> tuple[float, float, float, float]:
        x = self.label_w + step * self.cell_w
        y = HEADER_H + row * self.row_h
        return x, y, self.cell_w, self.row_h

    # ------------------------------------------------------------- interação
    def _on_click(self, gesture, _n_press: int, x: float, y: float) -> None:
        if (not self.project or not self.section_id
                or self.part_number is None):
            return
        button = gesture.get_current_button()
        if button not in (1, 3):
            return
        cell = self._cell_at(x, y)
        if cell is None:
            return
        self.grab_focus()
        step, _row, pitch = cell
        notes = self.project.notes(self.section_id, self.part_number)

        existing = None
        for note in notes:
            if note.pitch == pitch and note.start <= step < note.start + note.length:
                existing = note
                break

        changed = False
        if existing is not None:
            if button == 1 or button == 3:
                notes.remove(existing)
                changed = True
        elif button == 1:
            if self.is_drum:
                notes.append(Note(step, pitch, 1, self.velocity))
            else:
                length = min(self.note_length, self.steps - step)
                end = step + length
                for note in list(notes):
                    if (note.pitch == pitch and note.start < end
                            and note.start + note.length > step):
                        notes.remove(note)
                notes.append(Note(step, pitch, length, self.velocity))
            changed = True

        if changed:
            self.queue_draw()
            if self.on_changed:
                self.on_changed()

    def _on_motion(self, _controller, x: float, y: float) -> None:
        cell = self._cell_at(x, y)
        hover = None if cell is None else (cell[0], cell[1])
        if hover != self._hover:
            self._hover = hover
            self.queue_draw()

    def _on_leave(self, _controller) -> None:
        if self._hover is not None:
            self._hover = None
            self.queue_draw()

    # ------------------------------------------------------------- cores
    def _lookup(self, name: str, fallback: tuple[float, float, float]):
        try:
            found, color = self.get_style_context().lookup_color(name)
            if found:
                return _rgb(color)
        except Exception:
            pass
        return fallback

    def _palette(self):
        try:
            self._dark = Adw.StyleManager.get_default().get_dark()
        except Exception:
            pass
        if self._dark:
            bg = self._lookup("window_bg_color", (0.13, 0.13, 0.15))
            header = self._lookup("headerbar_bg_color", (0.17, 0.17, 0.20))
            row_alt = (0.16, 0.16, 0.18)
            black_key = (0.105, 0.105, 0.12)
            grid_minor = (0.23, 0.23, 0.26)
            grid_beat = (0.33, 0.33, 0.37)
            grid_bar = (0.50, 0.50, 0.55)
            text = self._lookup("window_fg_color", (0.90, 0.90, 0.92))
            dim = (0.60, 0.60, 0.65)
        else:
            bg = self._lookup("window_bg_color", (1.0, 1.0, 1.0))
            header = self._lookup("headerbar_bg_color", (0.95, 0.95, 0.96))
            row_alt = (0.965, 0.965, 0.975)
            black_key = (0.905, 0.905, 0.925)
            grid_minor = (0.87, 0.87, 0.895)
            grid_beat = (0.76, 0.76, 0.79)
            grid_bar = (0.55, 0.55, 0.60)
            text = self._lookup("window_fg_color", (0.13, 0.13, 0.16))
            dim = (0.45, 0.45, 0.50)
        accent = self._lookup("accent_bg_color", (0.20, 0.50, 0.85))
        drum = self._lookup("warning_bg_color", (0.90, 0.52, 0.15))
        danger = self._lookup("destructive_bg_color", (0.85, 0.20, 0.20))
        return {
            "bg": bg, "header": header, "row_alt": row_alt,
            "black_key": black_key, "grid_minor": grid_minor,
            "grid_beat": grid_beat, "grid_bar": grid_bar,
            "text": text, "dim": dim, "accent": accent,
            "drum": drum, "danger": danger,
        }

    # ------------------------------------------------------------- desenho
    def _draw(self, _area, cr, width: int, height: int) -> None:
        if (not self.project or not self.section_id
                or self.part_number is None or not self._rows):
            return
        p = self._palette()
        steps = self.steps
        rows = len(self._rows)
        label_w = self.label_w
        row_h = self.row_h
        cell_w = self.cell_w
        grid_w = steps * cell_w
        grid_h = rows * row_h
        spb = self.project.steps_per_bar
        beat_step = 4 if self.project.denominator == 4 else 6
        bar_count = max(1, steps // max(1, spb))

        # Fundo.
        cr.set_source_rgb(*p["bg"])
        cr.rectangle(0, 0, width, height)
        cr.fill()

        # Fundo alternado das linhas / teclas pretas.
        for row, pitch in enumerate(self._rows):
            y = HEADER_H + row * row_h
            if self.is_drum:
                if row % 2:
                    cr.set_source_rgb(*p["row_alt"])
                    cr.rectangle(label_w, y, grid_w, row_h)
                    cr.fill()
            elif pitch % 12 in BLACK_KEYS:
                cr.set_source_rgb(*p["black_key"])
                cr.rectangle(label_w, y, grid_w, row_h)
                cr.fill()

        # Compassos alternados (bem sutil).
        for bar in range(bar_count):
            if bar % 2:
                cr.set_source_rgba(*p["header"], 0.55 if self._dark else 0.45)
                cr.rectangle(label_w + bar * spb * cell_w, HEADER_H,
                             spb * cell_w, grid_h)
                cr.fill()

        # Linhas verticais.
        for step in range(steps + 1):
            x = label_w + step * cell_w
            if step % spb == 0:
                cr.set_source_rgb(*p["grid_bar"])
                cr.set_line_width(2.0)
            elif step % 4 == 0:
                cr.set_source_rgb(*p["grid_beat"])
                cr.set_line_width(1.0)
            else:
                cr.set_source_rgba(*p["grid_minor"], 0.65)
                cr.set_line_width(0.7)
            cr.move_to(x, HEADER_H)
            cr.line_to(x, HEADER_H + grid_h)
            cr.stroke()

        # Linhas horizontais.
        for row in range(rows + 1):
            y = HEADER_H + row * row_h
            cr.set_source_rgba(*p["grid_minor"], 0.75)
            cr.set_line_width(0.7)
            cr.move_to(label_w, y)
            cr.line_to(label_w + grid_w, y)
            cr.stroke()

        # Cabeçalho.
        cr.set_source_rgb(*p["header"])
        cr.rectangle(0, 0, width, HEADER_H)
        cr.fill()
        cr.set_source_rgba(*p["grid_bar"], 0.8)
        cr.set_line_width(1.0)
        cr.move_to(0, HEADER_H)
        cr.line_to(width, HEADER_H)
        cr.stroke()

        cr.set_source_rgb(*p["text"])
        cr.select_font_face("Sans", 0, 0)
        cr.set_font_size(10)
        cr.move_to(10, HEADER_H - 8)
        cr.show_text("Instrumento" if self.is_drum else "Nota")
        for bar in range(bar_count):
            x = label_w + bar * spb * cell_w
            cr.set_source_rgb(*p["text"])
            cr.select_font_face("Sans", 1, 0)
            cr.set_font_size(10)
            cr.move_to(x + 6, HEADER_H - 8)
            cr.show_text(str(bar + 1))
            cr.select_font_face("Sans", 0, 0)
            cr.set_source_rgba(*p["dim"], 0.9)
            cr.set_font_size(8)
            for beat in range(1, max(1, spb // beat_step)):
                bx = x + beat * beat_step * cell_w
                cr.move_to(bx + 3, HEADER_H - 8)
                cr.show_text(f"{bar + 1}.{beat + 1}")

        # Nomes das linhas.
        for row, pitch in enumerate(self._rows):
            y = HEADER_H + row * row_h
            if self.is_drum:
                label = DRUM_LABELS.get(pitch, str(pitch))
            else:
                label = note_name(pitch) if pitch % 12 == 0 else ""
            if not label:
                continue
            layout = self.create_pango_layout(label)
            desc = Pango.FontDescription("Sans 9")
            layout.set_font_description(desc)
            layout.set_ellipsize(Pango.EllipsizeMode.END)
            layout.set_width(max(10, label_w - 14) * Pango.SCALE)
            cr.set_source_rgb(*p["text"])
            cr.move_to(8, y + (row_h - 14) / 2)
            PangoCairo.show_layout(cr, layout)

        # Célula sob o mouse.
        if self._hover is not None:
            hx, hy = self._hover
            x, y, w, h = self._cell_rect(hx, hy)
            cr.set_source_rgba(*p["accent"], 0.14)
            cr.rectangle(x + 1, y + 1, w - 2, h - 2)
            cr.fill()
            cr.set_source_rgba(*p["accent"], 0.55)
            cr.set_line_width(1.0)
            cr.rectangle(x + 1, y + 1, w - 2, h - 2)
            cr.stroke()

        # Notas.
        notes = self.project.notes(self.section_id, self.part_number)
        for note in notes:
            try:
                row = self._rows.index(note.pitch)
            except ValueError:
                continue
            x, y, _w, h = self._cell_rect(note.start, row)
            length = max(1, min(note.length, steps - note.start))
            w = length * cell_w
            color = p["drum"] if self.is_drum else p["accent"]
            alpha = 0.55 + 0.45 * (max(1, min(127, note.velocity)) / 127.0)
            if self.part is not None and self.part.mute:
                alpha *= 0.35
            self._rounded_rect(cr, x + 2, y + 2, max(4, w - 3), h - 4, 3.5)
            cr.set_source_rgba(*color, alpha)
            cr.fill_preserve()
            cr.set_source_rgba(0, 0, 0, 0.20)
            cr.set_line_width(0.8)
            cr.stroke()

        # Playhead.
        if self._playhead is not None and 0 <= self._playhead < steps:
            x = label_w + self._playhead * cell_w
            cr.set_source_rgba(*p["danger"], 0.16)
            cr.rectangle(x, HEADER_H, cell_w, grid_h)
            cr.fill()
            cr.set_source_rgba(*p["danger"], 0.95)
            cr.set_line_width(2.0)
            cr.move_to(x + 1, HEADER_H)
            cr.line_to(x + 1, HEADER_H + grid_h)
            cr.stroke()

    @staticmethod
    def _rounded_rect(cr, x: float, y: float, w: float, h: float, r: float) -> None:
        r = min(r, w / 2, h / 2)
        cr.new_sub_path()
        cr.arc(x + w - r, y + r, r, -math.pi / 2, 0)
        cr.arc(x + w - r, y + h - r, r, 0, math.pi / 2)
        cr.arc(x + r, y + h - r, r, math.pi / 2, math.pi)
        cr.arc(x + r, y + r, r, math.pi, 3 * math.pi / 2)
        cr.close_path()
