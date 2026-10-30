"""Lista oficial de tons do Casio CT-X800 (600 timbres).

Os dados vêm da tabela "Tone List" do apêndice do manual do CT-X800:
número do tom, nome, program change, bank select MSB, categoria, DSP e
deslocamento de oitava. Tons com bank MSB 120 são kits de bateria.
"""
from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path


@dataclass(frozen=True)
class Tone:
    number: int
    name: str
    program: int
    bank: int
    category: int
    category_name: str
    dsp: bool = False
    octave: int = 0

    @property
    def drum(self) -> bool:
        return self.bank == 120

    @property
    def label(self) -> str:
        return f"{self.number:03d} — {self.name}"


CATEGORY_LABELS = {
    1: "Piano",
    2: "Piano elétrico",
    3: "Órgão",
    4: "Guitarra",
    5: "Baixo",
    6: "Strings",
    7: "Metais",
    8: "Sopro",
    9: "Flauta",
    10: "Synth",
    11: "Pad/Coro",
    12: "Étnico",
    13: "GM",
    14: "Bateria",
}

# Reserva mínima caso tones.json não seja encontrado (não deve acontecer).
_FALLBACK = (
    (1, "STAGE PIANO", 0, 1, 1, "PIANO", True, 0),
    (2, "GRAND PIANO", 0, 3, 1, "PIANO", False, 0),
    (139, "FINGERED BASS 1", 33, 6, 5, "BASS", False, -1),
    (184, "MELLOW STRINGS 1", 49, 1, 6, "STRINGS", False, 0),
    (565, "STANDARD SET 1", 0, 120, 14, "DRUM SET", False, 0),
    (588, "LATIN SET 1", 49, 120, 14, "DRUM SET", False, 0),
)


def _load_tones() -> tuple[Tone, ...]:
    path = Path(__file__).with_name("tones.json")
    try:
        raw = json.loads(path.read_text(encoding="utf-8"))
        tones = tuple(
            Tone(
                number=int(item["number"]),
                name=str(item["name"]),
                program=int(item["program"]),
                bank=int(item["bank"]),
                category=int(item.get("category", 0)),
                category_name=str(item.get("category_name", "")),
                dsp=bool(item.get("dsp", False)),
                octave=int(item.get("octave", 0)),
            )
            for item in raw
        )
        if tones:
            return tuple(sorted(tones, key=lambda t: t.number))
    except (OSError, ValueError, KeyError, TypeError):
        pass
    return tuple(Tone(*row) for row in _FALLBACK)


TONES = _load_tones()
TONES_BY_NUMBER = {tone.number: tone for tone in TONES}


def get_tone(number: int) -> Tone:
    return TONES_BY_NUMBER.get(number, TONES_BY_NUMBER[1])


def drum_tones() -> list[Tone]:
    return [tone for tone in TONES if tone.drum]


def melodic_tones() -> list[Tone]:
    return [tone for tone in TONES if not tone.drum]


def category_label(number: int) -> str:
    return CATEGORY_LABELS.get(number, f"Categoria {number}")
