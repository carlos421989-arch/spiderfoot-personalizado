from pathlib import Path
import unittest

ROOT = Path(__file__).resolve().parent

class TestPatchRepetirMecanismos(unittest.TestCase):
    def test_blueprint_reads_and_passes_flag(self):
        p=(ROOT/"spiderfoot/blueprints/collector.py").read_text(encoding="utf-8")
        self.assertIn("repetir_concluidas", p)
        self.assertIn("--repetir-concluidas", p)
    def test_ui_sends_flag(self):
        p=(ROOT/"spiderfoot/templates/pages/collector_enem_fatec.html").read_text(encoding="utf-8")
        self.assertIn('id="repetirconcluidas"', p)
        self.assertIn("repetir_concluidas:$('repetirconcluidas').checked", p)
    def test_collector_can_bypass_done_marker(self):
        p=(ROOT/"tools/Coletor_ENEM_FATEC/coletar_materiais.py").read_text(encoding="utf-8")
        self.assertIn('and not getattr(self.args, "repetir_concluidas", False)', p)
        self.assertIn('"--repetir-concluidas"', p)

if __name__ == "__main__": unittest.main(verbosity=2)
