import json
import sys
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from model import (  # noqa: E402
    Note, Project, SECTIONS, default_project, empty_project,
    steps_per_bar_for,
)
from tones import TONES, TONES_BY_NUMBER, drum_tones, melodic_tones  # noqa: E402


class ToneListTests(unittest.TestCase):
    def test_all_tones(self):
        self.assertEqual(len(TONES), 600)
        self.assertEqual(len(TONES_BY_NUMBER), 600)
        self.assertEqual(len(drum_tones()), 36)
        self.assertEqual(len(melodic_tones()), 564)
        tone = TONES_BY_NUMBER[184]
        self.assertEqual(tone.name, "MELLOW STRINGS 1")
        self.assertEqual((tone.program, tone.bank), (49, 1))
        self.assertEqual((TONES_BY_NUMBER[15].program,
                          TONES_BY_NUMBER[15].bank), (0, 35))

    def test_time_signatures(self):
        self.assertEqual(steps_per_bar_for("4/4"), 16)
        self.assertEqual(steps_per_bar_for("3/4"), 12)
        self.assertEqual(steps_per_bar_for("6/8"), 12)
        self.assertEqual(steps_per_bar_for("2/4"), 8)


class ModelTests(unittest.TestCase):
    def test_default_project(self):
        project = default_project()
        self.assertEqual(len(project.parts), 8)
        self.assertEqual(len(project.sections), 12)
        self.assertEqual(project.steps_per_bar, 16)
        self.assertEqual(project.steps("var1"), 64)
        self.assertEqual(project.steps("fill1"), 16)
        self.assertEqual(project.section_note_count("var1"), 60)
        self.assertEqual(project.part(5).tone_number, 184)
        self.assertEqual(project.part(2).tone.name, "STANDARD SET 1")
        self.assertFalse(project.part(2).mute)

    def test_roundtrip_v2(self):
        project = default_project()
        project.name = "AÇÃO"
        project.part(3).tone_number = 140
        project.part(3).volume = 77
        project.part(4).mute = True
        project.set_bars("var2", 2)
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "teste.ctx800.json"
            project.save(path)
            loaded = Project.load(path)
        self.assertEqual(loaded.name, "AÇÃO")
        self.assertEqual(loaded.display_name(), "ACAO")
        self.assertEqual(loaded.part(3).tone_number, 140)
        self.assertEqual(loaded.part(3).volume, 77)
        self.assertTrue(loaded.part(4).mute)
        self.assertEqual(loaded.bars("var2"), 2)
        self.assertEqual(len(loaded.parts), 8)
        self.assertEqual(len(loaded.sections), 12)

    def test_load_v1(self):
        old = {
            "name": "ANTIGO",
            "tempo": 80,
            "time_signature": "4/4",
            "sections": {
                "intro": {
                    "drums": [{"start": 0, "pitch": 35,
                               "length": 1, "velocity": 100}],
                    "bass": [],
                    "strings": [],
                },
                "var1": {
                    "drums": [{"start": 4, "pitch": 38,
                               "length": 1, "velocity": 90}],
                    "bass": [{"start": 0, "pitch": 36,
                              "length": 8, "velocity": 80}],
                    "strings": [],
                },
            },
        }
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "old.json"
            path.write_text(json.dumps(old), encoding="utf-8")
            project = Project.load(path)
        self.assertEqual(project.name, "ANTIGO")
        self.assertEqual(len(project.parts), 8)
        self.assertEqual(project.section_note_count("intro1"), 1)
        self.assertEqual(project.notes("intro1", 2)[0].pitch, 35)
        self.assertEqual(project.notes("var1", 3)[0].pitch, 36)
        self.assertEqual(project.total_note_count(), 3)

    def test_clipping_bars_and_signature(self):
        project = empty_project()
        project.notes("var1", 2).append(Note(60, 35, 4, 100))
        project.set_bars("var1", 2)  # 2 * 16 = 32 passos
        self.assertEqual(project.steps("var1"), 32)
        self.assertEqual(project.notes("var1", 2), [])

        project.notes("var1", 2).append(Note(10, 35, 10, 100))
        project.set_bars("var1", 1)  # 16 passos
        self.assertEqual(project.steps("var1"), 16)
        self.assertEqual(project.notes("var1", 2)[0].length, 6)

        signature_project = empty_project()
        signature_project.notes("var1", 2).append(Note(55, 35, 1, 100))
        signature_project.set_time_signature("3/4")  # 4 * 12 = 48 passos
        self.assertEqual(signature_project.steps("var1"), 48)
        self.assertEqual(signature_project.notes("var1", 2), [])

    def test_all_sections_have_elements(self):
        self.assertEqual([s["element"] for s in SECTIONS],
                         list(range(1, 13)))
        self.assertEqual([s["id"] for s in SECTIONS][0], "intro1")
        self.assertEqual([s["id"] for s in SECTIONS][-1], "ending2")


if __name__ == "__main__":
    unittest.main()
