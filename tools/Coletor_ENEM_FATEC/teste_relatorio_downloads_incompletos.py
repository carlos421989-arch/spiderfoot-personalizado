#!/usr/bin/env python3
import csv
import tempfile
import unittest
from pathlib import Path
from relatorio_downloads_incompletos import create_report

class TestRelatorioIncompletos(unittest.TestCase):
    def test_reports_partial_files_without_modifying_them(self):
        with tempfile.TemporaryDirectory() as tmp:
            src = Path(tmp) / 'resultados'
            p = src / 'materiais' / 'ENEM' / '2025' / 'prova.pdf_abc123.part'
            p.parent.mkdir(parents=True)
            p.write_bytes(b'partial')
            with (src / 'arquivos_baixados.csv').open('w', encoding='utf-8-sig', newline='') as f:
                w = csv.DictWriter(f, fieldnames=['arquivo','url','pesquisa','titulo'])
                w.writeheader(); w.writerow({'arquivo':str(p.relative_to(src)),'url':'https://example.org/prova.pdf','pesquisa':'ENEM 2025 prova','titulo':'Prova ENEM'})
            report, rows = create_report(src, age_hours=0)
            self.assertTrue(report.is_file())
            self.assertEqual(len(rows), 1)
            self.assertEqual(rows[0]['status_sugerido'], 'ANTIGO_REVISAR')
            self.assertTrue(p.is_file())
            self.assertEqual(p.read_bytes(), b'partial')

if __name__ == '__main__':
    unittest.main(verbosity=2)
