#!/usr/bin/env python3
"""Testes offline para renomeacao V12 em downloads novos e arquivos antigos."""
import csv
import hashlib
import io
import os
import sys
import tempfile
import types
import unittest
from pathlib import Path
from contextlib import redirect_stdout
from unittest.mock import patch

import fitz
import analisar_conteudo as a
import organizar_resultados as o


def make_pdf(path: Path, text: str):
    doc = fitz.open()
    page = doc.new_page()
    for i, line in enumerate(text.splitlines()):
        page.insert_text((45, 56 + i * 20), line, fontsize=13)
    for _ in range(22):
        doc.new_page()
    doc.save(path)
    doc.close()


class FakeResponse:
    def __init__(self, url, body):
        self.url = url
        self.body = body
        self.headers = {"Content-Type": "application/pdf", "Content-Length": str(len(body))}
    def raise_for_status(self):
        return None
    def iter_content(self, chunk_size=65536):
        yield self.body
    def close(self):
        return None


class TestCorrecaoNomesV12(unittest.TestCase):
    def test_historical_year_does_not_imply_cancelled_application(self):
        with tempfile.TemporaryDirectory() as tmp:
            p = Path(tmp) / "2000_PV_impresso_404a5ffb64.pdf"
            make_pdf(p, "ENEM 2000\nExame Nacional do Ensino Medio\nPROVA 1 - AMARELA\nLeia atentamente as questoes")
            info = a.inspect(p, {"titulo": "Prova ENEM 2000", "url": "https://example.org/2000.pdf", "pesquisa": "ENEM 2000 prova cancelada"}, ocr=False)
            self.assertNotEqual(info.edition, "Aplicacao_Cancelada")
            self.assertEqual(info.kind, "01_Provas")

    def test_explicit_cancelled_filename_is_preserved(self):
        with tempfile.TemporaryDirectory() as tmp:
            p = Path(tmp) / "enem-2009-1-cancelado.pdf"
            make_pdf(p, "ENEM 2009\nExame Nacional do Ensino Medio\nPROVA\nAplicacao cancelada")
            info = a.inspect(p, {"titulo": "ENEM 2009 prova", "url": "https://example.org/enem-2009.pdf", "pesquisa": "ENEM 2009 prova"}, ocr=False)
            self.assertEqual(info.edition, "Aplicacao_Cancelada")
            self.assertEqual(info.kind, "01_Provas")

    def test_hashed_2009_cancelled_filenames_keep_cancelled_status_and_day(self):
        cases = [
            ("enem-2009-1-cancelado_af480c318d.pdf", "1"),
            ("enem-2009-2-cancelado_a77f9f0063.pdf", "2"),
        ]
        for filename, expected_day in cases:
            with self.subTest(filename=filename), tempfile.TemporaryDirectory() as tmp:
                p = Path(tmp) / filename
                make_pdf(p, "ENEM 2009\nExame Nacional do Ensino Medio\nPROVA\nAplicacao cancelada")
                info = a.inspect(p, {"titulo": "ENEM 2009 prova cancelada", "url": "https://example.org/" + filename, "pesquisa": "ENEM 2009 prova"}, ocr=False)
                self.assertEqual(info.edition, "Aplicacao_Cancelada")
                self.assertEqual(info.day, expected_day)
                self.assertEqual(info.kind, "01_Provas")
                self.assertIn("Cancelada", o.friendly_name(info, p, o.sha256_file(p)))

    def test_hashed_cancelled_answer_key_remains_gabarito(self):
        with tempfile.TemporaryDirectory() as tmp:
            filename = "gabarito-enem-2009-cancelado_e9d084aa54.pdf"
            p = Path(tmp) / filename
            make_pdf(p, "ENEM 2009\nGABARITO\nGabarito da prova cancelada")
            info = a.inspect(p, {"titulo": "Gabarito ENEM 2009 cancelado", "url": "https://example.org/" + filename, "pesquisa": "ENEM 2009 gabarito"}, ocr=False)
            self.assertEqual(info.edition, "Aplicacao_Cancelada")
            self.assertEqual(info.kind, "02_Gabaritos")
            self.assertIn("Gabarito", o.friendly_name(info, p, o.sha256_file(p)))

    def test_detects_prova_1_amarela_as_single_day_enem_2002(self):
        with tempfile.TemporaryDirectory() as tmp:
            p = Path(tmp) / "2002_PV_impresso_4cb26e840d.pdf"
            make_pdf(p, "ENEM 2002\nExame Nacional do Ensino Medio\nMinisterio da Educacao\nPROVA 1 - AMARELA\nLEIA ATENTAMENTE")
            info = a.inspect(p, {"titulo": "Provas e gabaritos", "url": "https://example.org/2002_PV_impresso.pdf", "pesquisa": "ENEM 2002 prova"}, ocr=False)
            self.assertEqual(info.exam, "ENEM")
            self.assertEqual(info.year, "2002")
            self.assertEqual(info.proof_number, "1")
            self.assertEqual(info.colors, ["Amarelo"])
            self.assertEqual(info.day, "Unico")
            self.assertEqual(o.friendly_name(info, p, o.sha256_file(p)), f"ENEM_2002_Prova_1_Amarela_{o.sha256_file(p)[:10]}.pdf")

    def test_generic_historical_enem_pdf_uses_results_folder_context(self):
        with tempfile.TemporaryDirectory() as tmp:
            base = Path(tmp)
            p = base / "resultados" / "materiais" / "ENEM" / "2000" / "2000_PV_impresso_404a5ffb64.pdf"
            p.parent.mkdir(parents=True)
            make_pdf(p, "Prova de Redacao\nLeia atentamente as instrucoes")
            info = a.inspect(p, {"titulo": "PDF", "url": "https://example.org/2000_PV_impresso.pdf", "pesquisa": "materiais de estudo"}, ocr=False)
            self.assertEqual(info.exam, "ENEM")
            self.assertEqual(info.year, "2000")
            self.assertEqual(info.kind, "01_Provas")
            self.assertEqual(info.day, "Unico")
            name = o.friendly_name(info, p, o.sha256_file(p))
            self.assertTrue(name.startswith("ENEM_2000_Prova_"), name)

    def test_renames_old_download_and_updates_index_and_acervo_copy(self):
        with tempfile.TemporaryDirectory() as tmp:
            base = Path(tmp)
            src = base / "resultados"
            folder = src / "materiais" / "ENEM" / "2002" / "Gabaritos"
            folder.mkdir(parents=True)
            old = folder / "2002_PV_impresso_4cb26e840d.pdf"
            make_pdf(old, "ENEM 2002\nExame Nacional do Ensino Medio\nMinisterio da Educacao\nPROVA 1 - AMARELA\nLEIA ATENTAMENTE")
            digest = o.sha256_file(old)
            index = src / "arquivos_baixados.csv"
            with index.open("w", encoding="utf-8-sig", newline="") as f:
                w = csv.DictWriter(f, fieldnames=["data", "pesquisa", "titulo", "url", "arquivo", "bytes", "sha256"])
                w.writeheader()
                w.writerow({"data": "2026-10-21", "pesquisa": "ENEM 2002 prova", "titulo": "Provas e gabaritos", "url": "https://example.org/2002_PV_impresso.pdf", "arquivo": str(old.relative_to(src)), "bytes": old.stat().st_size, "sha256": digest})
            with patch.object(o, "SRC", src), patch.object(o, "BASE", base):
                self.assertEqual(o.main(["--destino", str(base / "acervo")]), 0)
            new = folder / f"ENEM_2002_Prova_1_Amarela_{digest[:10]}.pdf"
            self.assertTrue(new.is_file())
            self.assertFalse(old.exists())
            self.assertEqual(o.sha256_file(new), digest)
            rows = o.read_csv(index)
            self.assertEqual(rows[0]["arquivo"], str(new.relative_to(src)))
            acervo_copy = base / "acervo" / "01_ENEM" / "2002" / "Aplicacao_Nao_Identificada" / "Dia_Unico" / "Amarelo" / "01_Provas" / new.name
            self.assertTrue(acervo_copy.is_file(), str(acervo_copy))
            self.assertTrue((src / "relatorio_renomeacoes_downloads.csv").is_file())

    def test_old_download_rename_falls_back_to_verified_copy_when_windows_handle_blocks_rename(self):
        with tempfile.TemporaryDirectory() as tmp:
            base = Path(tmp)
            src = base / "resultados"
            folder = src / "materiais" / "ENEM" / "2002"
            folder.mkdir(parents=True)
            old = folder / "2002_PV_impresso_4cb26e840d.pdf"
            make_pdf(old, "ENEM 2002\nExame Nacional do Ensino Medio\nPROVA 1 - AMARELA")
            digest = o.sha256_file(old)
            index = src / "arquivos_baixados.csv"
            with index.open("w", encoding="utf-8-sig", newline="") as f:
                w = csv.DictWriter(f, fieldnames=["data", "pesquisa", "titulo", "url", "arquivo", "bytes", "sha256"])
                w.writeheader()
                w.writerow({"data":"2026-10-21", "pesquisa":"ENEM 2002 prova", "titulo":"Provas", "url":"https://example.org/2002.pdf", "arquivo":str(old.relative_to(src)), "bytes":old.stat().st_size, "sha256":digest})
            real_rename = os.rename
            def blocked_rename(path, target):
                if Path(path) == old:
                    raise PermissionError("simulated Windows file handle")
                return real_rename(path, target)
            with patch.object(o, "SRC", src), patch.object(o, "BASE", base), patch.object(o.os, "rename", blocked_rename):
                self.assertEqual(o.main(["--destino", str(base / "acervo")]), 0)
            new = folder / f"ENEM_2002_Prova_1_Amarela_{digest[:10]}.pdf"
            self.assertTrue(new.is_file())
            self.assertFalse(old.exists())
            self.assertEqual(o.sha256_file(new), digest)

    def test_new_download_uses_content_based_name_and_correct_folder(self):
        # The latest collector imports this module in the installed package. Stub
        # only its pre-existing first-pass name generator so this test isolates V12.
        stub = types.ModuleType("document_intelligence")
        stub.infer_meta = lambda query, title, url, path, meta: (meta, "", {})
        stub.suggested_filename = lambda path, meta, text, tags, query, title: "download_generico.pdf"
        sys.modules["document_intelligence"] = stub
        sys.path.insert(0, str(Path(__file__).resolve().parent))
        try:
            import coletar_materiais as co
            with tempfile.TemporaryDirectory() as tmp:
                base = Path(tmp)
                source_pdf = base / "source.pdf"
                make_pdf(source_pdf, "ENEM 2002\nExame Nacional do Ensino Medio\nMinisterio da Educacao\nPROVA 1 - AMARELA\nLEIA ATENTAMENTE")
                body = source_pdf.read_bytes()
                args = types.SimpleNamespace(limite_arquivos=5, tamanho_max_mb=20, pausa=0, telegram_canais_arquivo="")
                with patch.object(co, "OUT", base / "resultados"):
                    collector = co.Collector(args)
                    response = FakeResponse("https://example.org/2002_PV_impresso.pdf", body)
                    collector.s.get = lambda *a, **kw: response
                    collector.download("https://example.org/2002_PV_impresso.pdf", "ENEM 2002 prova", "Provas e gabaritos")
                    rows = o.read_csv(base / "resultados" / "arquivos_baixados.csv")
                    self.assertEqual(len(rows), 1)
                    rel = Path(rows[0]["arquivo"])
                    self.assertEqual(rel.name, f"ENEM_2002_Prova_1_Amarela_{hashlib.sha256(body).hexdigest()[:10]}.pdf")
                    self.assertIn("Provas", rel.parts)
                    self.assertIn("Caderno_Amarela", rel.parts)
                    self.assertTrue((base / "resultados" / rel).is_file())
        finally:
            sys.modules.pop("document_intelligence", None)
            if sys.path and sys.path[0] == str(Path(__file__).resolve().parent):
                sys.path.pop(0)

    def test_gabarito_filename_wins_over_prova_reference_in_pdf_cover(self):
        with tempfile.TemporaryDirectory() as tmp:
            p = Path(tmp) / "GABARITO_ENEM_2002_AMARELA_c8e7c5c2ea.pdf"
            make_pdf(p, "ENEM 2002\nGABARITO OFICIAL\nGABARITO\nPROVA 1 - AMARELA\nQuestao 1: alternativa A")
            info = a.inspect(p, {"titulo": "Gabarito ENEM 2002", "url": "https://example.org/gabarito.pdf", "pesquisa": "ENEM 2002 gabarito"}, ocr=False)
            self.assertEqual(info.kind, "02_Gabaritos")
            name = o.friendly_name(info, p, o.sha256_file(p))
            self.assertIn("Gabarito", name)
            self.assertNotIn("Prova_1", name)

    def test_partial_downloads_are_skipped_by_renamer(self):
        with tempfile.TemporaryDirectory() as tmp:
            base = Path(tmp)
            src = base / "resultados"
            folder = src / "materiais" / "ENEM" / "2002"
            folder.mkdir(parents=True)
            partial = folder / "GABARITO_ENEM_2002_AMARELA.pdf_abcd.part"
            partial.write_bytes(b"incomplete")
            index = src / "arquivos_baixados.csv"
            with index.open("w", encoding="utf-8-sig", newline="") as f:
                w = csv.DictWriter(f, fieldnames=["arquivo", "sha256", "titulo", "url", "pesquisa"])
                w.writeheader(); w.writerow({"arquivo": str(partial.relative_to(src)), "sha256": hashlib.sha256(partial.read_bytes()).hexdigest(), "titulo": "Gabarito ENEM 2002", "url": "", "pesquisa": "ENEM 2002 gabarito"})
            with patch.object(o, "SRC", src), patch.object(o, "BASE", base):
                self.assertEqual(o.main(["--simular", "--destino", str(base / "acervo")]), 0)
            self.assertTrue(partial.exists())
            self.assertFalse(any(folder.glob("ENEM_*.part")))


    def test_cancelled_historical_enem_is_classified_as_cancelled_proof(self):
        with tempfile.TemporaryDirectory() as tmp:
            p = Path(tmp) / "enem-2009-1-cancelado.pdf"
            make_pdf(p, "ENEM 2009\nPROVA CANCELADA\nExame Nacional do Ensino Medio")
            info = a.inspect(p, {"titulo": "ENEM 2009 cancelado", "url": "https://example.org/enem-2009-1-cancelado.pdf", "pesquisa": "ENEM 2009 prova cancelada"}, ocr=False)
            self.assertEqual(info.exam, "ENEM")
            self.assertEqual(info.year, "2009")
            self.assertEqual(info.day, "1")
            self.assertEqual(info.edition, "Aplicacao_Cancelada")
            self.assertEqual(info.kind, "01_Provas")
            self.assertNotIn("SEM_EXAME_OU_DISCIPLINA", info.reason)

    def test_combined_answer_key_is_copied_to_both_day_folders(self):
        with tempfile.TemporaryDirectory() as tmp:
            base = Path(tmp)
            src = base / "resultados"
            folder = src / "materiais" / "ENEM" / "2022"
            folder.mkdir(parents=True)
            pdf = folder / "ENEM-2022-gabarito.pdf"
            make_pdf(pdf, "ENEM 2022\nGABARITO OFICIAL\nGABARITO DO 1º DIA\nGABARITO DO 2º DIA\nCADERNO AZUL")
            digest = o.sha256_file(pdf)
            index = src / "arquivos_baixados.csv"
            with index.open("w", encoding="utf-8-sig", newline="") as f:
                w = csv.DictWriter(f, fieldnames=["data", "pesquisa", "titulo", "url", "arquivo", "bytes", "sha256"])
                w.writeheader()
                w.writerow({"data":"2026-10-21", "pesquisa":"ENEM 2022 gabarito", "titulo":"Gabarito ENEM 2022", "url":"https://example.org/ENEM-2022-gabarito.pdf", "arquivo":str(pdf.relative_to(src)), "bytes":pdf.stat().st_size, "sha256":digest})
            captured = io.StringIO()
            with patch.object(o, "SRC", src), patch.object(o, "BASE", base), redirect_stdout(captured):
                self.assertEqual(o.main(["--simular", "--destino", str(base / "acervo")]), 0)
            info = a.inspect(pdf, {"titulo":"Gabarito ENEM 2022", "url":"https://example.org/ENEM-2022-gabarito.pdf", "pesquisa":"ENEM 2022 gabarito"}, ocr=False)
            self.assertEqual(info.kind, "02_Gabaritos")
            self.assertEqual(info.day, "Ambos")
            self.assertTrue(info.combined_days)
            self.assertNotIn("GABARITO_ABRANGE_DIAS_1_E_2", info.reason)
            # Normalize Windows and POSIX separators so the assertion is portable.
            output_paths = captured.getvalue().replace("\\", "/")
            self.assertIn("/1_Dia/Azul/02_Gabaritos/", output_paths)
            self.assertIn("/2_Dia/Azul/02_Gabaritos/", output_paths)
            self.assertIn("2 destinos", output_paths)
            # A simulacao imprime um destino por dia; nao altera o PDF original.
            self.assertTrue(pdf.is_file())


if __name__ == "__main__":
    unittest.main(verbosity=2)
