import importlib.util
import sys
import unittest
import tempfile
from types import SimpleNamespace
from unittest.mock import patch
from pathlib import Path

HERE = Path(__file__).resolve().parent
SCRIPT = HERE / "coletar_materiais.py"
spec = importlib.util.spec_from_file_location("collector_v25", SCRIPT)
collector = importlib.util.module_from_spec(spec)
sys.modules[spec.name] = collector
spec.loader.exec_module(collector)

class TestPesquisaGeralEMidia(unittest.TestCase):
    def test_general_query_file_exists_and_is_broad(self):
        path = HERE / "pesquisas_gerais.txt"
        self.assertTrue(path.is_file())
        queries = collector.load_queries(path)
        self.assertGreaterEqual(len(queries), 100)
        joined = "\n".join(queries).casefold()
        for term in ("videoclipes", "tutoriais", "manual", "receitas", "podcasts", "documentários"):
            self.assertIn(term, joined)

    def test_general_queries_have_no_hash_prefix(self):
        queries = collector.load_queries(HERE / "pesquisas_gerais.txt")
        self.assertTrue(all(not q.startswith("#") for q in queries))

    def test_general_mode_combines_custom_and_general_only(self):
        result = collector.build_query_list("geral", ["prova ENEM"], ["receita", "tutorial"], ["minha busca"])
        self.assertEqual(result, ["minha busca", "receita", "tutorial"])

    def test_all_mode_combines_standard_and_general(self):
        result = collector.build_query_list("todos", ["ENEM", "igual"], ["receita", "igual"], ["personalizada"])
        self.assertEqual(result, ["personalizada", "ENEM", "igual", "receita"])

    def test_researches_mode_does_not_add_general_library(self):
        result = collector.build_query_list("pesquisas", ["ENEM"], ["receita"], ["personalizada"])
        self.assertEqual(result, ["personalizada", "ENEM"])

    def test_media_extension_and_mime_catalogs(self):
        self.assertIn(".mp4", collector.MEDIA_EXT)
        self.assertIn(".mp3", collector.MEDIA_EXT)
        self.assertIn(".jpg", collector.MEDIA_EXT)
        self.assertEqual(collector.MEDIA_MIME_EXT["video/mp4"], ".mp4")
        self.assertEqual(collector.MEDIA_MIME_EXT["image/jpeg"], ".jpg")

    def test_platforms_include_multiple_public_video_hosts(self):
        for host in ("youtube.com", "vimeo.com", "dailymotion.com", "soundcloud.com", "tiktok.com"):
            self.assertIn(host, collector.MEDIA_PLATFORM_DOMAINS)

    def test_cli_has_general_mode_and_media_flag(self):
        import argparse
        old_argv = sys.argv[:]
        try:
            sys.argv = [str(SCRIPT), "--modo", "geral", "--baixar-midias-publicas", "--tamanho-max-midia-mb", "2048"]
            args = collector.parse()
        finally:
            sys.argv = old_argv
        self.assertEqual(args.modo, "geral")
        self.assertTrue(args.baixar_midias_publicas)
        self.assertEqual(args.tamanho_max_midia_mb, 2048)

    def test_media_download_is_opt_in(self):
        source = SCRIPT.read_text(encoding="utf-8")
        self.assertIn('not getattr(self.args, "baixar_midias_publicas", False)', source)
        self.assertIn("--baixar-midias-publicas", source)

    def test_spiderfoot_api_accepts_general_mode_and_media_flag(self):
        api = (HERE.parents[1] / "spiderfoot" / "blueprints" / "collector.py").read_text(encoding="utf-8")
        self.assertIn("'geral'", api)
        self.assertIn("baixar_midias_publicas", api)
        self.assertIn("--baixar-midias-publicas", api)

    def test_ui_exposes_general_mode_media_checkbox_and_size_limit(self):
        ui = (HERE.parents[1] / "spiderfoot" / "templates" / "pages" / "collector_enem_fatec.html").read_text(encoding="utf-8")
        self.assertIn('value="geral"', ui)
        self.assertIn('id="baixarmidiaspublicas"', ui)
        self.assertIn('id="midiamaxmb"', ui)
        self.assertIn("baixar_midias_publicas:$('baixarmidiaspublicas').checked", ui)

    def test_download_media_saves_extensionless_public_image_by_mime(self):
        class FakeResponse:
            url = "https://cdn.example.test/asset?id=123"
            headers = {"Content-Type": "image/jpeg", "Content-Length": "5"}
            def raise_for_status(self):
                return None
            def iter_content(self, chunk_size=0):
                yield b"abcde"
            def close(self):
                return None

        with tempfile.TemporaryDirectory() as tmp:
            out = Path(tmp) / "resultados"
            out.mkdir()
            args = SimpleNamespace(telegram_canais_arquivo="", baixar_midias_publicas=True,
                                   limite_arquivos=0, tamanho_max_midia_mb=1, pausa=0)
            with patch.object(collector, "OUT", out):
                instance = collector.Collector(args)
                instance.s.get = lambda *a, **kw: FakeResponse()
                ok = instance.download_media(FakeResponse.url, "receita ilustrada", "Imagem da receita")
                self.assertTrue(ok)
                saved = list((out / "midias" / "imagens").glob("*.jpg"))
                self.assertEqual(len(saved), 1)
                self.assertEqual(saved[0].read_bytes(), b"abcde")
                self.assertTrue((out / "midias_baixadas.csv").is_file())

if __name__ == "__main__":
    unittest.main(verbosity=2)
