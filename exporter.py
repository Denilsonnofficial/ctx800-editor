"""Exportação do projeto para um arquivo .AC7 do Casio CT-X800.

Fluxo: um MIDI por seção/parte (mido) -> JSON do ac7maker -> executa o
ac7maker -> valida o AC7 gerado.
"""
from __future__ import annotations

import hashlib
import json
import shutil
import subprocess
import sys
import tempfile
from dataclasses import dataclass, field
from pathlib import Path

import mido
from mido import Message, MetaMessage, MidiFile, MidiTrack, bpm2tempo

from model import PPQ, SECTIONS, TICKS_PER_STEP, Project

MAX_SIZE = 64 * 1024

AC7MAKER_CANDIDATES = (
    Path(__file__).resolve().parent / "vendor" / "ac7maker" / "ac7maker.py",
    Path(__file__).resolve().parent.parent / "ac7maker" / "ac7maker.py",
)

ELEMENTS_JSON = tuple(
    {"element": section["element"], "_comment": section["name"]}
    for section in SECTIONS
)


class ExportError(Exception):
    """Erro amigável durante a exportação."""


@dataclass
class ExportResult:
    path: Path
    size: int
    sha256: str
    tracks: int
    elements: int
    warnings: list[str] = field(default_factory=list)


def _ac7maker() -> Path:
    for candidate in AC7MAKER_CANDIDATES:
        if candidate.is_file():
            return candidate
    raise ExportError(
        "ac7maker não encontrado. Reinstale o programa ou coloque "
        "ac7maker.py em vendor/ac7maker/."
    )


def _parts_definition(project: Project) -> list[dict]:
    parts: list[dict] = []
    for part in project.parts:
        parts.append({
            "part": part.number,
            "_comment": part.name,
            "patch": part.tone.program,
            "bank_msb": part.tone.bank,
            "volume": 0 if part.mute else part.volume,
            "pan": max(-64, min(63, part.pan - 64)),
            "reverb_send": part.reverb,
            "chorus_send": part.chorus,
        })
    return parts


def _new_midi(project: Project, total_ticks: int) -> MidiFile:
    mid = MidiFile(ticks_per_beat=PPQ)
    conductor = MidiTrack()
    mid.tracks.append(conductor)
    conductor.append(MetaMessage(
        "time_signature", numerator=project.beats_per_bar,
        denominator=project.denominator, time=0))
    conductor.append(MetaMessage("set_tempo", tempo=bpm2tempo(project.tempo),
                                 time=0))
    conductor.append(MetaMessage("marker", text="end", time=total_ticks))
    return mid


def _write_part_midi(project: Project, section_id: str, part,
                     path: Path) -> None:
    """MIDI de uma única parte. Uma faixa por arquivo evita colisão de canal."""
    total_ticks = project.steps(section_id) * TICKS_PER_STEP
    mid = _new_midi(project, total_ticks)
    track = MidiTrack()
    mid.tracks.append(track)
    channel = part.channel
    events: list[tuple[int, int, Message]] = []
    for note in project.notes(section_id, part.number):
        start = max(0, note.start) * TICKS_PER_STEP
        end = min((note.start + max(1, note.length)) * TICKS_PER_STEP,
                  total_ticks)
        if end <= start:
            continue
        velocity = max(1, min(127, note.velocity))
        events.append((start, 1, Message(
            "note_on", channel=channel, note=note.pitch,
            velocity=velocity)))
        events.append((end, 0, Message(
            "note_off", channel=channel, note=note.pitch, velocity=0)))
    events.sort(key=lambda item: (item[0], item[1]))
    last = 0
    for tick, _priority, message in events:
        track.append(message.copy(time=max(0, tick - last)))
        last = tick
    track.append(MetaMessage("end_of_track",
                             time=max(0, total_ticks - last)))
    mid.save(path)


def _write_timing_midi(project: Project, section_id: str, path: Path) -> None:
    """MIDI só com regência/marcador, para preservar o tamanho de seção vazia."""
    total_ticks = project.steps(section_id) * TICKS_PER_STEP
    mid = _new_midi(project, total_ticks)
    mid.tracks[0].append(MetaMessage("end_of_track", time=0))
    mid.save(path)


def export_ac7(project: Project, output: Path) -> ExportResult:
    if not project.has_active_notes():
        raise ExportError(
            "O projeto não tem notas em partes ativas. Adicione notas ou "
            "desmute alguma parte antes de exportar."
        )
    if not 20 <= int(project.tempo) <= 255:
        raise ExportError("O andamento precisa estar entre 20 e 255 BPM.")
    ac7maker = _ac7maker()

    output = Path(output)
    if output.suffix.lower() != ".ac7":
        output = (output.with_suffix(output.suffix + ".AC7")
                  if output.suffix else output.with_suffix(".AC7"))
    output.parent.mkdir(parents=True, exist_ok=True)

    warnings: list[str] = []
    empty_sections: list[str] = []
    track_count = 0

    with tempfile.TemporaryDirectory(prefix="ctx800_export_") as tmp_name:
        tmp = Path(tmp_name)
        tracks: list[dict] = []
        for section in SECTIONS:
            active_parts = [
                part for part in project.parts
                if not part.mute
                and project.notes(section["id"], part.number)
            ]
            if not active_parts:
                empty_sections.append(section["name"])
                filename = f"timing_{section['id']}.mid"
                _write_timing_midi(project, section["id"], tmp / filename)
                part = project.parts[0]
                tracks.append({
                    "part": part.number,
                    "element": section["element"],
                    "source_file": filename,
                    "source_channel": part.channel + 1,
                    "chord_sync": 0,
                })
                continue

            for part in active_parts:
                filename = f"sec_{section['id']}_p{part.number}.mid"
                _write_part_midi(project, section["id"], part,
                                 tmp / filename)
                track_count += 1
                tracks.append({
                    "part": part.number,
                    "element": section["element"],
                    "source_file": filename,
                    "source_channel": part.channel + 1,
                    "chord_sync": 0 if part.drum else int(part.chord_sync),
                })

        data = {
            "name": project.display_name(),
            "target_model": "CT-X800",
            "target_family": "CT-X",
            "input_dir": str(tmp),
            "rhythm": {
                "name": project.display_name(),
                "volume": 110,
                "tempo": int(project.tempo),
                "time_signature": project.time_signature,
                "parts": _parts_definition(project),
                "elements": list(ELEMENTS_JSON),
                "tracks": tracks,
            },
        }
        json_path = tmp / "ritmo.json"
        json_path.write_text(json.dumps(data, indent=2, ensure_ascii=False),
                             encoding="utf-8")

        try:
            proc = subprocess.run(
                [sys.executable, str(ac7maker), str(json_path)],
                capture_output=True, timeout=120,
            )
        except subprocess.TimeoutExpired as exc:
            raise ExportError("O ac7maker demorou demais para responder.") from exc
        if proc.returncode != 0:
            detail = (proc.stderr or b"").decode("utf-8", "replace").strip()
            last = detail.splitlines()[-1] if detail else "erro desconhecido"
            raise ExportError(f"O ac7maker falhou:\n{last}")
        blob = proc.stdout
        if not blob.startswith(b"AC07"):
            raise ExportError("O ac7maker não gerou um arquivo AC7 válido.")
        declared = int.from_bytes(blob[4:8], "little")
        if declared != len(blob):
            raise ExportError(
                f"O AC7 gerado está inconsistente ({declared} bytes declarados, "
                f"{len(blob)} bytes reais)."
            )
        if len(blob) > MAX_SIZE:
            raise ExportError(
                f"O AC7 ficou com {len(blob) / 1024:.1f} KB, acima do limite "
                f"de 64 KB do CT-X800."
            )

    if empty_sections:
        shown = ", ".join(empty_sections[:6])
        extra = (f" e mais {len(empty_sections) - 6}"
                 if len(empty_sections) > 6 else "")
        warnings.append(
            f"{len(empty_sections)} seção(ões) sem notas ativas "
            f"(saem em silêncio): {shown}{extra}."
        )
    output.write_bytes(blob)
    return ExportResult(
        path=output,
        size=len(blob),
        sha256=hashlib.sha256(blob).hexdigest(),
        tracks=track_count,
        elements=12,
        warnings=warnings,
    )


def render_section_wav(project: Project, section_id: str, output: Path) -> Path:
    """Atalho de depuração: renderiza uma seção para WAV."""
    from audio import AudioEngine
    engine = AudioEngine()
    try:
        wav = engine.render(project, section_id)
        shutil.copyfile(wav, output)
    finally:
        engine.close()
    return output
