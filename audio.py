"""Prévia de áudio: gera MIDI, renderiza WAV com FluidSynth e toca no GStreamer."""
from __future__ import annotations

import array
import hashlib
import json
import os
import subprocess
import tempfile
import wave
from pathlib import Path

import gi
import mido
from mido import Message, MetaMessage, MidiFile, MidiTrack, bpm2tempo

gi.require_version("Gst", "1.0")
from gi.repository import GLib, Gst  # noqa: E402

from model import PPQ, Project, TICKS_PER_STEP  # noqa: E402

SOUNDFONTS = (
    Path("/usr/share/sounds/sf2/default-GM.sf2"),
    Path("/usr/share/sounds/sf2/FluidR3_GM.sf2"),
    Path("/usr/share/sounds/sf2/TimGM6mb.sf2"),
)


class AudioError(Exception):
    """Falha ao renderizar ou tocar a prévia."""


def _soundfont() -> Path:
    for path in SOUNDFONTS:
        if path.is_file():
            return path
    raise AudioError(
        "SoundFont GM não encontrado. Instale o pacote fluid-soundfont-gm."
    )


def _section_duration(project: Project, section_id: str) -> float:
    """Duração exata da seção em segundos (passos de 1/16)."""
    steps = project.steps(section_id)
    return steps * (60.0 / max(20, project.tempo) / 4.0)


def _note_events(project: Project, section_id: str, part, channel: int,
                 total_ticks: int) -> list[tuple[int, int, Message]]:
    events: list[tuple[int, int, Message]] = []
    for note in project.notes(section_id, part.number):
        start = max(0, note.start) * TICKS_PER_STEP
        end = min((note.start + max(1, note.length)) * TICKS_PER_STEP,
                  total_ticks)
        if end <= start:
            continue
        velocity = max(1, min(127, note.velocity))
        events.append((start, 1, Message(
            "note_on", channel=channel, note=note.pitch, velocity=velocity)))
        events.append((end, 0, Message(
            "note_off", channel=channel, note=note.pitch, velocity=0)))
    events.sort(key=lambda item: (item[0], item[1]))
    return events


def _append_controls(track: MidiTrack, channel: int, part, time: int = 0) -> None:
    for control, value in (
        (7, part.volume),        # volume
        (10, part.pan),          # pan
        (91, part.reverb),       # reverb send
        (93, part.chorus),       # chorus send
    ):
        track.append(Message("control_change", channel=channel,
                             control=control,
                             value=max(0, min(127, int(value))), time=time))


def _append_events(track: MidiTrack,
                   events: list[tuple[int, int, Message]],
                   total_ticks: int) -> None:
    last = 0
    for tick, _priority, message in events:
        track.append(message.copy(time=max(0, tick - last)))
        last = tick
    track.append(MetaMessage("end_of_track", time=max(0, total_ticks - last)))


def _build_midi(project: Project, section_id: str, path: Path) -> None:
    total_ticks = project.steps(section_id) * TICKS_PER_STEP
    mid = MidiFile(ticks_per_beat=PPQ)
    conductor = MidiTrack()
    mid.tracks.append(conductor)
    conductor.append(MetaMessage(
        "time_signature", numerator=project.beats_per_bar,
        denominator=project.denominator, time=0))
    conductor.append(MetaMessage("set_tempo", tempo=bpm2tempo(project.tempo),
                                 time=0))
    conductor.append(MetaMessage("marker", text="end", time=total_ticks))

    active = [part for part in project.parts if not part.mute]
    drum_parts = [
        part for part in active if part.drum
        and project.notes(section_id, part.number)
    ]
    melodic_parts = [
        part for part in active if not part.drum
        and project.notes(section_id, part.number)
    ]
    if not drum_parts and not melodic_parts:
        raise AudioError(
            "A seção não tem notas em partes ativas. Adicione notas ou "
            "desmute uma parte."
        )

    # As duas partes de bateria compartilham o canal 10 do GM. Na prévia,
    # usamos o kit da parte 2 (ou da primeira parte de bateria ativa).
    if drum_parts:
        kit = next((part for part in drum_parts if part.number == 2),
                   drum_parts[0])
        track = MidiTrack()
        mid.tracks.append(track)
        channel = 9
        track.append(Message("program_change", channel=channel,
                             program=kit.tone.program, time=0))
        _append_controls(track, channel, kit)
        events: list[tuple[int, int, Message]] = []
        for part in drum_parts:
            events.extend(_note_events(project, section_id, part, channel,
                                       total_ticks))
        events.sort(key=lambda item: (item[0], item[1]))
        _append_events(track, events, total_ticks)

    for part in melodic_parts:
        track = MidiTrack()
        mid.tracks.append(track)
        channel = part.channel
        track.append(Message("program_change", channel=channel,
                             program=part.tone.program, time=0))
        _append_controls(track, channel, part)
        _append_events(
            track,
            _note_events(project, section_id, part, channel, total_ticks),
            total_ticks,
        )

    mid.save(path)


def _trim_wav(src: Path, dst: Path, duration: float) -> None:
    """Corta o rabo de reverb para o loop ficar no tempo certo."""
    try:
        with wave.open(str(src), "rb") as reader:
            params = reader.getparams()
            frames = reader.readframes(reader.getnframes())
    except (wave.Error, EOFError) as exc:
        raise AudioError(f"Arquivo de áudio inválido: {exc}") from exc

    channels = params.nchannels
    width = params.sampwidth
    rate = params.framerate
    target_frames = max(1, int(round(duration * rate)))
    frame_size = channels * width
    available = len(frames) // max(1, frame_size)
    if available > target_frames:
        frames = frames[:target_frames * frame_size]
        available = target_frames

    # Fade-out curto para evitar estalo no loop (somente PCM 16 bits).
    if width == 2 and available > 4:
        samples = array.array("h")
        samples.frombytes(frames)
        fade = min(max(1, int(0.012 * rate)), available)
        for i in range(fade):
            gain = 1.0 - (i / fade)
            frame = available - fade + i
            for channel in range(channels):
                pos = frame * channels + channel
                if 0 <= pos < len(samples):
                    samples[pos] = int(samples[pos] * gain)
        frames = samples.tobytes()

    with wave.open(str(dst), "wb") as writer:
        writer.setparams(params)
        writer.writeframes(frames)


class AudioEngine:
    """Renderiza a seção atual e toca em loop pelo GStreamer."""

    def __init__(self) -> None:
        self.cache_dir = Path(GLib.get_user_cache_dir()) / "ctx800-editor"
        self.cache_dir.mkdir(parents=True, exist_ok=True)
        self._playing = False
        self._loop = True
        self._uri: str | None = None
        self.on_finished = None
        self._error: str | None = None

        Gst.init(None)
        self._player = Gst.ElementFactory.make("playbin", "ctx800-preview")
        if self._player is None:
            raise AudioError("GStreamer playbin não está disponível.")
        try:
            self._player.set_property("volume", 0.85)
        except Exception:
            pass
        self._bus = self._player.get_bus()
        self._bus.add_signal_watch()
        self._bus.connect("message", self._on_bus_message)
        self._player.connect("about-to-finish", self._on_about_to_finish)

    # ------------------------------------------------------------- render
    def _cache_key(self, project: Project, section_id: str) -> str:
        data = {"section": section_id, "project": project.to_dict()}
        payload = json.dumps(data, sort_keys=True)
        return hashlib.sha256(payload.encode("utf-8")).hexdigest()[:16]

    def render(self, project: Project, section_id: str) -> Path:
        key = self._cache_key(project, section_id)
        final = self.cache_dir / f"preview_{key}.wav"
        if final.is_file() and final.stat().st_size > 1024:
            return final

        mid_fd, mid_name = tempfile.mkstemp(prefix="ctx800_", suffix=".mid",
                                            dir=self.cache_dir)
        wav_fd, wav_name = tempfile.mkstemp(prefix="ctx800_", suffix=".raw.wav",
                                            dir=self.cache_dir)
        os.close(wav_fd)
        mid_path = Path(mid_name)
        raw_wav = Path(wav_name)
        try:
            _build_midi(project, section_id, mid_path)
            command = [
                "fluidsynth", "-ni", "-q", "-g", "0.75", "-r", "44100",
                "-T", "wav", "-F", str(raw_wav), str(_soundfont()),
                str(mid_path),
            ]
            try:
                proc = subprocess.run(command, capture_output=True, text=True,
                                      timeout=120)
            except FileNotFoundError as exc:
                raise AudioError(
                    "FluidSynth não encontrado. Instale o pacote fluidsynth."
                ) from exc
            except subprocess.TimeoutExpired as exc:
                raise AudioError("A renderização da prévia demorou demais.") from exc
            if proc.returncode != 0:
                detail = (proc.stderr or proc.stdout or "").strip().splitlines()
                detail_text = detail[-1] if detail else "erro desconhecido"
                raise AudioError(f"FluidSynth falhou: {detail_text}")

            _trim_wav(raw_wav, final, _section_duration(project, section_id))
        finally:
            for tmp in (mid_path, raw_wav):
                try:
                    tmp.unlink(missing_ok=True)
                except OSError:
                    pass

        self._cleanup_cache()
        return final

    def _cleanup_cache(self) -> None:
        try:
            files = sorted(self.cache_dir.glob("preview_*.wav"),
                           key=lambda p: p.stat().st_mtime, reverse=True)
            for old in files[24:]:
                old.unlink(missing_ok=True)
        except OSError:
            pass

    # ------------------------------------------------------------- playback
    @property
    def playing(self) -> bool:
        return self._playing

    def play(self, wav_path: Path, loop: bool = True) -> None:
        self.stop()
        self._uri = wav_path.resolve().as_uri()
        self._loop = loop
        self._error = None
        self._player.set_property("uri", self._uri)
        result = self._player.set_state(Gst.State.PLAYING)
        if result == Gst.StateChangeReturn.FAILURE:
            self._player.set_state(Gst.State.NULL)
            raise AudioError("Não foi possível iniciar a reprodução de áudio.")
        self._playing = True

    def stop(self) -> None:
        was_playing = self._playing
        self._playing = False
        try:
            self._player.set_state(Gst.State.NULL)
        except Exception:
            pass
        if was_playing and self.on_finished:
            self.on_finished()

    def position(self) -> float:
        """Posição atual em segundos (0 se não disponível)."""
        if not self._playing:
            return 0.0
        try:
            ok, position = self._player.query_position(Gst.Format.TIME)
            if ok and position > 0:
                return position / Gst.SECOND
        except Exception:
            pass
        return 0.0

    def close(self) -> None:
        self.stop()
        try:
            self._bus.remove_signal_watch()
        except Exception:
            pass
        self._player.set_state(Gst.State.NULL)

    # ------------------------------------------------------------- sinais
    def _on_about_to_finish(self, _player) -> None:
        if self._playing and self._loop:
            try:
                self._player.seek_simple(
                    Gst.Format.TIME,
                    Gst.SeekFlags.FLUSH | Gst.SeekFlags.ACCURATE, 0,
                )
            except Exception:
                pass

    def _on_bus_message(self, _bus, message) -> None:
        if message.type == Gst.MessageType.EOS:
            if self._playing and self._loop:
                try:
                    self._player.seek_simple(
                        Gst.Format.TIME,
                        Gst.SeekFlags.FLUSH | Gst.SeekFlags.ACCURATE, 0,
                    )
                    self._player.set_state(Gst.State.PLAYING)
                except Exception:
                    self._finish()
            else:
                self._finish()
        elif message.type == Gst.MessageType.ERROR:
            error, debug = message.parse_error()
            self._error = str(error)
            self._finish()

    def _finish(self) -> None:
        if not self._playing:
            return
        self._playing = False
        try:
            self._player.set_state(Gst.State.NULL)
        except Exception:
            pass
        if self.on_finished:
            self.on_finished()
