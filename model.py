"""Modelo de dados do editor de ritmos CT-X800.

O projeto tem 12 elementos (Intro 1/2, Variações 1–4, Fills 1–4, Endings 1/2),
8 partes editáveis e notas em passos de 1/16. Cada parte tem um tom Casio
(número, program change e bank select MSB), volume, pan, reverb, chorus, mute e
"segue acorde".
"""
from __future__ import annotations

import json
import re
import unicodedata
from dataclasses import dataclass, field
from pathlib import Path

from tones import TONES_BY_NUMBER, Tone, get_tone

# ---------------------------------------------------------------- constantes

PPQ = 480
TICKS_PER_STEP = PPQ // 4

TIME_SIGNATURES = {
    "4/4": (4, 4),
    "3/4": (3, 4),
    "2/4": (2, 4),
    "6/8": (6, 8),
}


def steps_per_bar_for(signature: str) -> int:
    numerator, denominator = TIME_SIGNATURES.get(signature, (4, 4))
    return max(1, numerator * 16 // denominator)


SECTIONS = (
    {"id": "intro1", "name": "Intro 1", "element": 1, "bars": 4},
    {"id": "var1", "name": "Variação 1", "element": 2, "bars": 4},
    {"id": "var2", "name": "Variação 2", "element": 3, "bars": 4},
    {"id": "fill1", "name": "Fill 1", "element": 4, "bars": 1},
    {"id": "fill2", "name": "Fill 2", "element": 5, "bars": 1},
    {"id": "ending1", "name": "Ending 1", "element": 6, "bars": 4},
    {"id": "intro2", "name": "Intro 2", "element": 7, "bars": 4},
    {"id": "var3", "name": "Variação 3", "element": 8, "bars": 4},
    {"id": "var4", "name": "Variação 4", "element": 9, "bars": 4},
    {"id": "fill3", "name": "Fill 3", "element": 10, "bars": 1},
    {"id": "fill4", "name": "Fill 4", "element": 11, "bars": 1},
    {"id": "ending2", "name": "Ending 2", "element": 12, "bars": 4},
)
SECTION_BY_ID = {section["id"]: section for section in SECTIONS}

# Projetos da versão 1 usavam "intro" e "ending".
LEGACY_SECTION_IDS = {"intro": "intro1", "ending": "ending1"}
LEGACY_PART_IDS = {"drums": 2, "bass": 3, "strings": 6}

# ---------------------------------------------------------------- bateria
# Notas GM usadas na grade. O CT-X800 segue o mapa GM para bateria.
DRUM_ROWS = (
    (35, "Bumbo"),
    (36, "Bumbo 2"),
    (38, "Caixa"),
    (40, "Caixa el."),
    (37, "Aro"),
    (39, "Palmas"),
    (42, "HH fechado"),
    (44, "HH pedal"),
    (46, "HH aberto"),
    (49, "Crash"),
    (51, "Ride"),
    (53, "Ride bell"),
    (54, "Tamborim"),
    (56, "Cowbell"),
    (60, "Bongo H"),
    (61, "Bongo L"),
    (62, "Conga H"),
    (63, "Conga aberta"),
    (64, "Conga L"),
    (65, "Timbal H"),
    (66, "Timbal L"),
    (67, "Agogô H"),
    (68, "Agogô L"),
    (69, "Cabasa"),
    (70, "Maracas"),
    (75, "Claves"),
    (76, "Wood H"),
    (77, "Wood L"),
    (81, "Triângulo"),
)
DRUM_LABELS = dict(DRUM_ROWS)

# Extensão visível do piano roll por parte melódica.
MELODIC_RANGES = {
    3: (28, 55),   # Baixo: E1–G3
    4: (43, 82),   # Piano: G2–A#5
    5: (48, 71),   # Strings: C3–B4
    6: (48, 71),   # E. Piano
    7: (40, 67),   # Violão: E2–G4
    8: (48, 71),   # Pad/Coro
}

NOTE_NAMES = ("C", "C#", "D", "D#", "E", "F", "F#", "G", "G#", "A", "A#", "B")
BLACK_KEYS = {1, 3, 6, 8, 10}


def note_name(pitch: int) -> str:
    return f"{NOTE_NAMES[pitch % 12]}{pitch // 12 - 1}"


# ---------------------------------------------------------------- partes

@dataclass
class PartConfig:
    number: int
    name: str
    drum: bool
    channel: int
    tone_number: int
    volume: int = 100
    pan: int = 64
    reverb: int = 40
    chorus: int = 0
    mute: bool = False
    chord_sync: bool = False

    @property
    def tone(self) -> Tone:
        return get_tone(self.tone_number)

    @property
    def patch(self) -> int:
        return self.tone.program

    @property
    def bank(self) -> int:
        return self.tone.bank

    def to_dict(self) -> dict:
        return {
            "number": self.number,
            "name": self.name,
            "drum": self.drum,
            "channel": self.channel,
            "tone": self.tone_number,
            "volume": self.volume,
            "pan": self.pan,
            "reverb": self.reverb,
            "chorus": self.chorus,
            "mute": self.mute,
            "chord_sync": self.chord_sync,
        }

    @classmethod
    def from_dict(cls, data: dict, default: "PartConfig") -> "PartConfig":
        try:
            tone = int(data.get("tone", default.tone_number))
        except (TypeError, ValueError):
            tone = default.tone_number
        if tone not in TONES_BY_NUMBER:
            tone = default.tone_number
        return cls(
            number=default.number,
            name=str(data.get("name", default.name)) or default.name,
            drum=default.drum,
            channel=default.channel,
            tone_number=tone,
            volume=_clamp(int(data.get("volume", default.volume)), 0, 127),
            pan=_clamp(int(data.get("pan", default.pan)), 0, 127),
            reverb=_clamp(int(data.get("reverb", default.reverb)), 0, 127),
            chorus=_clamp(int(data.get("chorus", default.chorus)), 0, 127),
            mute=bool(data.get("mute", default.mute)),
            chord_sync=bool(data.get("chord_sync", default.chord_sync)),
        )


def default_parts() -> list[PartConfig]:
    return [
        PartConfig(1, "Percussão", True, 9, 588, volume=100, reverb=40,
                   chord_sync=False),
        PartConfig(2, "Bateria", True, 9, 565, volume=110, reverb=40,
                   chord_sync=False),
        PartConfig(3, "Baixo", False, 2, 139, volume=100, pan=64, reverb=30,
                   chord_sync=True),
        PartConfig(4, "Piano", False, 3, 2, volume=90, pan=64, reverb=50,
                   chord_sync=True),
        PartConfig(5, "Strings", False, 4, 184, volume=100, pan=64, reverb=50,
                   chord_sync=True),
        PartConfig(6, "E. Piano", False, 5, 25, volume=90, pan=64, reverb=40,
                   chord_sync=True),
        PartConfig(7, "Violão", False, 6, 90, volume=85, pan=64, reverb=40,
                   chord_sync=True),
        PartConfig(8, "Pad/Coro", False, 7, 318, volume=80, pan=64, reverb=50,
                   chord_sync=True),
    ]


def _clamp(value: int, minimum: int, maximum: int) -> int:
    return max(minimum, min(maximum, value))


# ---------------------------------------------------------------- notas

@dataclass
class Note:
    start: int              # passo de 1/16 desde o começo da seção
    pitch: int              # nota MIDI
    length: int = 1         # duração em passos de 1/16
    velocity: int = 100


# ---------------------------------------------------------------- projeto

@dataclass
class Project:
    name: str = "MEU RITMO"
    tempo: int = 90
    time_signature: str = "4/4"
    parts: list[PartConfig] = field(default_factory=list)
    sections: dict[str, dict[int, list[Note]]] = field(default_factory=dict)
    section_bars: dict[str, int] = field(default_factory=dict)
    path: Path | None = None
    format_version: int = 2

    def __post_init__(self) -> None:
        if self.time_signature not in TIME_SIGNATURES:
            self.time_signature = "4/4"
        if not self.parts:
            self.parts = default_parts()
        else:
            by_number = {part.number: part for part in self.parts}
            defaults = {part.number: part for part in default_parts()}
            self.parts = [
                by_number.get(number, defaults[number])
                for number in range(1, 9)
            ]
            for part in self.parts:
                part.volume = _clamp(int(part.volume), 0, 127)
                part.pan = _clamp(int(part.pan), 0, 127)
                part.reverb = _clamp(int(part.reverb), 0, 127)
                part.chorus = _clamp(int(part.chorus), 0, 127)
        for section in SECTIONS:
            self.section_bars.setdefault(section["id"], section["bars"])
            self.sections.setdefault(section["id"], {})
            for part in self.parts:
                self.sections[section["id"]].setdefault(part.number, [])
        for section_id in list(self.section_bars):
            if section_id not in SECTION_BY_ID:
                self.section_bars.pop(section_id, None)
        for part in self.parts:
            for section in SECTIONS:
                self.sections[section["id"]].setdefault(part.number, [])

    # ------------------------------------------------------------- utilidades
    @property
    def steps_per_bar(self) -> int:
        return steps_per_bar_for(self.time_signature)

    @property
    def beats_per_bar(self) -> int:
        return TIME_SIGNATURES[self.time_signature][0]

    @property
    def denominator(self) -> int:
        return TIME_SIGNATURES[self.time_signature][1]

    def part(self, number: int) -> PartConfig:
        for part in self.parts:
            if part.number == number:
                return part
        return self.parts[0]

    def notes(self, section_id: str, part_number: int) -> list[Note]:
        return self.sections[section_id][part_number]

    def bars(self, section_id: str) -> int:
        return _clamp(int(self.section_bars.get(
            section_id, SECTION_BY_ID[section_id]["bars"])), 1, 8)

    def steps(self, section_id: str) -> int:
        return self.bars(section_id) * self.steps_per_bar

    def section_note_count(self, section_id: str) -> int:
        return sum(len(self.sections[section_id][part.number])
                   for part in self.parts)

    def active_note_count(self, section_id: str) -> int:
        return sum(
            len(self.sections[section_id][part.number])
            for part in self.parts if not part.mute
        )

    def total_note_count(self) -> int:
        return sum(self.section_note_count(section["id"])
                   for section in SECTIONS)

    def is_empty(self) -> bool:
        return self.total_note_count() == 0

    def has_active_notes(self) -> bool:
        return any(self.active_note_count(section["id"]) > 0
                   for section in SECTIONS)

    def add_or_remove(self, section_id: str, part_number: int, start: int,
                      pitch: int, length: int = 1, velocity: int = 100) -> None:
        notes = self.notes(section_id, part_number)
        for note in list(notes):
            if note.start == start and note.pitch == pitch:
                notes.remove(note)
                return
        notes.append(Note(start, pitch, max(1, length), velocity))

    def remove_at(self, section_id: str, part_number: int, start: int,
                  pitch: int) -> None:
        notes = self.notes(section_id, part_number)
        for note in list(notes):
            if note.pitch == pitch and note.start <= start < note.start + note.length:
                notes.remove(note)

    def clear_part(self, section_id: str, part_number: int) -> None:
        self.sections[section_id][part_number].clear()

    def clear_section(self, section_id: str) -> None:
        for part in self.parts:
            self.sections[section_id][part.number].clear()

    def clip_section(self, section_id: str) -> None:
        limit = self.steps(section_id)
        for part in self.parts:
            notes = self.sections[section_id][part.number]
            for note in list(notes):
                if note.start >= limit:
                    notes.remove(note)
                elif note.start + note.length > limit:
                    note.length = max(1, limit - note.start)

    def clip_all(self) -> None:
        for section in SECTIONS:
            self.clip_section(section["id"])

    def set_bars(self, section_id: str, bars: int) -> None:
        self.section_bars[section_id] = _clamp(bars, 1, 8)
        self.clip_section(section_id)

    def set_time_signature(self, signature: str) -> None:
        if signature not in TIME_SIGNATURES:
            signature = "4/4"
        self.time_signature = signature
        self.clip_all()

    # ------------------------------------------------------------- persistência
    def to_dict(self) -> dict:
        return {
            "format": "ctx800-editor-2",
            "name": self.name,
            "tempo": self.tempo,
            "time_signature": self.time_signature,
            "parts": [part.to_dict() for part in self.parts],
            "sections": {
                section_id: {
                    "bars": self.bars(section_id),
                    "parts": {
                        str(part.number): [
                            {"start": n.start, "pitch": n.pitch,
                             "length": n.length, "velocity": n.velocity}
                            for n in parts.get(part.number, [])
                        ]
                        for part in self.parts
                    },
                }
                for section_id, parts in self.sections.items()
            },
        }

    def save(self, path: Path) -> None:
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps(self.to_dict(), indent=2, ensure_ascii=False),
                        encoding="utf-8")
        self.path = path

    @classmethod
    def load(cls, path: Path) -> "Project":
        data = json.loads(path.read_text(encoding="utf-8"))
        project = cls(
            name=str(data.get("name", "MEU RITMO")),
            tempo=_clamp(int(data.get("tempo", 90)), 20, 255),
            time_signature=str(data.get("time_signature", "4/4")),
            path=path,
        )
        version = 2 if data.get("format") == "ctx800-editor-2" else 1
        project.format_version = version

        if version == 2:
            stored_parts = data.get("parts") or []
            defaults = {part.number: part for part in default_parts()}
            parts: list[PartConfig] = []
            for item in stored_parts:
                try:
                    number = int(item.get("number", 0))
                except (TypeError, ValueError):
                    continue
                if number in defaults:
                    parts.append(PartConfig.from_dict(item, defaults[number]))
            if parts:
                project.parts = parts
                project.__post_init__()

        sections = data.get("sections") or {}
        for raw_section_id, section_data in sections.items():
            section_id = LEGACY_SECTION_IDS.get(raw_section_id, raw_section_id)
            if section_id not in project.sections:
                continue
            if not isinstance(section_data, dict):
                continue
            if "bars" in section_data:
                project.section_bars[section_id] = _clamp(
                    int(section_data.get("bars", project.bars(section_id))), 1, 8)
            part_data = section_data.get("parts", section_data)
            if not isinstance(part_data, dict):
                continue
            for raw_part_id, raw_notes in part_data.items():
                if raw_part_id in ("bars",):
                    continue
                part_number = _part_number_from_key(raw_part_id)
                if part_number is None or part_number not in project.sections[section_id]:
                    continue
                notes: list[Note] = []
                for item in raw_notes or []:
                    try:
                        notes.append(Note(
                            start=max(0, int(item["start"])),
                            pitch=_clamp(int(item["pitch"]), 0, 127),
                            length=max(1, int(item.get("length", 1))),
                            velocity=_clamp(int(item.get("velocity", 100)), 1, 127),
                        ))
                    except (KeyError, TypeError, ValueError):
                        continue
                project.sections[section_id][part_number] = notes
        project.clip_all()
        return project

    def display_name(self) -> str:
        # Remove acentos em vez de apagar a letra (AÇÃO -> ACAO).
        name = unicodedata.normalize("NFKD", self.name)
        name = name.encode("ascii", "ignore").decode("ascii")
        name = re.sub(r"[^A-Za-z0-9 ]", "", name).strip().upper()
        return name[:7] or "RITMO"


def _part_number_from_key(key) -> int | None:
    if isinstance(key, int):
        return key if 1 <= key <= 8 else None
    text = str(key)
    if text in LEGACY_PART_IDS:
        return LEGACY_PART_IDS[text]
    if text.startswith("part"):
        text = text[4:]
    try:
        number = int(text)
    except ValueError:
        return None
    return number if 1 <= number <= 8 else None


def empty_project() -> Project:
    """Projeto em branco com todos os elementos e partes."""
    return Project(name="MEU RITMO", tempo=90)


def default_project() -> Project:
    """Projeto de exemplo: worship simples com bateria, baixo e strings."""
    project = Project(name="WORSHIP", tempo=72)
    spb = project.steps_per_bar
    # Bateria: bumbo 1/3, caixa 3, chimbal em colcheias.
    drums = project.notes("var1", 2)
    for bar in range(4):
        base = bar * spb
        for step, pitch in ((0, 35), (spb // 2, 35), (spb // 2, 38)):
            drums.append(Note(base + step, pitch, 1, 100))
        for st in range(0, spb, 2):
            drums.append(Note(base + st, 42, 1, 80))
    # Baixo: fundamental por compasso.
    bass = project.notes("var1", 3)
    for bar, pitch in enumerate((36, 31, 33, 29)):
        bass.append(Note(bar * spb, pitch, max(1, spb - 4), 90))
    # Strings: acorde longo por compasso (C4 maior).
    strings = project.notes("var1", 5)
    for bar in range(4):
        for pitch in (60, 64, 67):
            strings.append(Note(bar * spb, pitch, spb, 90))
    return project
