import sys
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from exporter import ExportError, export_ac7  # noqa: E402
from model import Note, SECTIONS, default_project, empty_project  # noqa: E402


def _elements(blob: bytes):
    seg_offset = int.from_bytes(blob[8:12], "little")
    seg = blob[seg_offset:]
    count = seg[6]
    offsets = [int.from_bytes(seg[7 + 4 * i:11 + 4 * i], "little")
               for i in range(count)]
    result = []
    for offset in offsets:
        data = seg[offset:]
        assert data[:4] == b"ELMT"
        atoms = {}
        pos = 6
        while pos + 1 < len(data):
            atom_id = data[pos]
            length = data[pos + 1]
            atoms[atom_id] = data[pos + 2:pos + 2 + length]
            pos += 2 + length
            if atom_id == 0xFF:
                break
        ts = atoms.get(1, b"\x00")[0]
        numerator = ts >> 3
        denominator = 1 << (ts & 7)
        result.append({
            "measures": atoms.get(6, b"\x00")[0],
            "tracks": atoms.get(7, b"\x00")[0],
            "signature": f"{numerator}/{denominator}",
        })
    return result


def _mixer(blob: bytes):
    index = blob.find(b"MIXR")
    count = int.from_bytes(blob[index + 8:index + 10], "little")
    entries = []
    for k in range(count):
        offset = int.from_bytes(blob[index + 10 + 4 * k:index + 14 + 4 * k],
                                "little")
        entries.append(tuple(blob[offset:offset + 6]))
    return entries


class ExporterTests(unittest.TestCase):
    def test_default_project_export(self):
        project = default_project()
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "DEMO.AC7"
            result = export_ac7(project, path)
            blob = path.read_bytes()
        self.assertEqual(result.size, len(blob))
        self.assertLess(result.size, 64 * 1024)
        self.assertEqual(blob[:4], b"AC07")
        self.assertEqual(int.from_bytes(blob[4:8], "little"), len(blob))
        self.assertEqual(result.elements, 12)
        self.assertEqual(len(_elements(blob)), 12)
        elements = _elements(blob)
        self.assertEqual(elements[0]["measures"], 4)   # Intro 1
        self.assertEqual(elements[1]["measures"], 4)   # Variação 1
        self.assertEqual(elements[3]["measures"], 1)   # Fill 1
        self.assertEqual(elements[4]["measures"], 1)   # Fill 2
        self.assertEqual(elements[5]["measures"], 4)   # Ending 1
        self.assertEqual(elements[6]["measures"], 4)   # Intro 2 (vazia)
        self.assertEqual(elements[11]["measures"], 4)  # Ending 2 (vazia)
        self.assertTrue(all(e["signature"] == "4/4" for e in elements))
        self.assertTrue(all(e["tracks"] == 8 for e in elements))
        mixer = _mixer(blob)
        self.assertEqual(len(mixer), 96)
        self.assertEqual(mixer[1][:3], (0, 120, 110))   # parte 2 bateria
        self.assertEqual(mixer[2][:3], (33, 6, 100))    # parte 3 baixo
        self.assertEqual(mixer[4][:3], (49, 1, 100))    # parte 5 strings
        self.assertEqual(mixer[0][:3], (49, 120, 100))  # parte 1 percussão

    def test_muted_part_and_time_signature(self):
        project = default_project()
        project.set_time_signature("3/4")
        for part in project.parts:
            if part.number != 3:
                part.mute = True
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "MUTE.AC7"
            result = export_ac7(project, path)
            blob = path.read_bytes()
        elements = _elements(blob)
        self.assertTrue(all(e["signature"] == "3/4" for e in elements))
        self.assertEqual(elements[0]["measures"], 4)
        self.assertEqual(elements[3]["measures"], 1)
        mixer = _mixer(blob)
        self.assertEqual(mixer[0][2], 0)  # parte 1 muda
        self.assertEqual(mixer[1][2], 0)  # parte 2 muda
        self.assertEqual(mixer[2][2], 100)  # baixo ativo
        # Só o baixo tem notas, e apenas na Variação 1.
        self.assertEqual(result.tracks, 1)
        self.assertTrue(result.warnings)

    def test_all_eight_parts_export(self):
        project = empty_project()
        for section in SECTIONS:
            for part in project.parts:
                if part.drum:
                    note = Note(0, 35, 1, 100)
                else:
                    note = Note(0, 60, 2, 90)
                project.notes(section["id"], part.number).append(note)
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "OITO.AC7"
            result = export_ac7(project, path)
            blob = path.read_bytes()
        self.assertEqual(result.tracks, 96)
        self.assertEqual(result.size, len(blob))
        self.assertEqual(len(_mixer(blob)), 96)
        self.assertEqual(len(_elements(blob)), 12)

    def test_empty_project_rejected(self):
        with tempfile.TemporaryDirectory() as tmp:
            with self.assertRaises(ExportError):
                export_ac7(empty_project(), Path(tmp) / "VAZIO.AC7")


if __name__ == "__main__":
    unittest.main()
