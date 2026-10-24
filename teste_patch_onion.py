import importlib.util
import sys
import tempfile
import types
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest import mock

ROOT = Path(__file__).resolve().parent
COLLECTOR = ROOT / "tools" / "Coletor_ENEM_FATEC"
sys.path.insert(0, str(COLLECTOR))
if not (COLLECTOR / "document_intelligence.py").exists():
    stub = types.ModuleType("document_intelligence")
    stub.infer_meta = lambda *args, **kwargs: {}
    stub.suggested_filename = lambda *args, **kwargs: "arquivo"
    sys.modules["document_intelligence"] = stub
import coletar_materiais as cm
from bs4 import BeautifulSoup


class TestOnionPatch(unittest.TestCase):
    def test_five_user_supplied_onion_engines_are_registered_and_default_selected(self):
        expected = {
            "onion_2fd6": "http://2fd6cemt4gmccflhm6imvdfvli3nf7zn6rfrwpsy7uhxrgbypvwf5fad.onion/",
            "onion_tornetup": "http://tornetupfu7gcgidt33ftnungxzyfq2pygui5qdoyss34xbgx2qruzid.onion/",
            "onion_goscan24": "http://goscan24olh2vjmcwqjujgcpyrijjg6rottqzwtogj3f67djgh6c5lid.onion/",
            "onion_startpage": "http://startpagel6srwcjlue4zgq3zevrujfaow726kjytqbbjyrswwmjzcqd.onion/",
            "onion_torsearch": "http://torsearc525abz6aqsqqfteyjqe2x7xrujg55atmu56vk47wixgb44ad.onion/",
        }
        for engine_id, url in expected.items():
            self.assertIn(engine_id, cm.ENGINE_CATALOG)
            self.assertEqual(cm.ENGINE_CATALOG[engine_id]["url"], url)
            self.assertEqual(cm.ENGINE_CATALOG[engine_id]["kind"], "onion")
            self.assertTrue(cm.ENGINE_CATALOG[engine_id]["default"])

    def test_form_detection_finds_search_field_and_hidden_token(self):
        html = '<form action="/search" method="get"><input type="hidden" name="csrf" value="abc"><input type="search" name="query"></form>'
        soup = BeautifulSoup(html, "html.parser")
        obj = cm.Collector.__new__(cm.Collector)
        cfg = obj._detect_onion_search_form(soup, "http://example.onion/")
        self.assertEqual(cfg["action"], "http://example.onion/search")
        self.assertEqual(cfg["field"], "query")
        self.assertEqual(cfg["fields"]["csrf"], "abc")

    def test_missing_socks_proxy_skips_without_network_request(self):
        obj = cm.Collector.__new__(cm.Collector)
        obj.args = SimpleNamespace(tor_socks_url="socks5h://127.0.0.1:9150")
        obj._tor_proxy_checked = False
        obj._tor_proxy_available = False
        obj._tor_proxy_message = ""
        obj.engine_last_status = {}
        with mock.patch.object(cm.importlib.util, "find_spec", return_value=None):
            self.assertFalse(obj._tor_proxy_ready())
        self.assertIn("SOCKS", obj._tor_proxy_message)

    def test_onion_destination_is_recorded_but_not_automatically_opened(self):
        obj = cm.Collector.__new__(cm.Collector)
        obj.args = SimpleNamespace(resultados_busca=6)
        obj.link = mock.Mock()
        obj._record_engine = mock.Mock()
        obj._accept_engine_result = mock.Mock(return_value=True)
        response = SimpleNamespace(
            headers={"Content-Type": "text/html; charset=utf-8"},
            content=b'<html><a href="http://targetabc.onion/page">Public educational page</a></html>',
            text='<html><a href="http://targetabc.onion/page">Public educational page</a></html>',
            url="http://searcher.onion/?q=exam",
        )
        count = obj._extract_onion_results("exam", "Test Onion", response, set())
        self.assertEqual(count, 1)
        obj.link.assert_called_once()
        obj._record_engine.assert_called_once()
        obj._accept_engine_result.assert_not_called()

    def test_html_and_blueprint_include_proxy_config(self):
        html = (ROOT / "spiderfoot" / "templates" / "pages" / "collector_enem_fatec.html").read_text(encoding="utf-8")
        blueprint = (ROOT / "spiderfoot" / "blueprints" / "collector.py").read_text(encoding="utf-8")
        self.assertIn('id="torsocksurl"', html)
        self.assertIn("tor_socks_url:$('torsocksurl').value", html)
        self.assertIn("--tor-socks-url", blueprint)
        for engine_id in ("onion_2fd6", "onion_tornetup", "onion_goscan24", "onion_startpage", "onion_torsearch"):
            self.assertIn(engine_id, blueprint)


if __name__ == "__main__":
    unittest.main(verbosity=2)
