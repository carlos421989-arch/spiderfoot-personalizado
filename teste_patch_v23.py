from pathlib import Path
import ast
import unittest

ROOT = Path(__file__).resolve().parent
COLLECTOR = ROOT / 'tools' / 'Coletor_ENEM_FATEC' / 'coletar_materiais.py'
BLUEPRINT = ROOT / 'spiderfoot' / 'blueprints' / 'collector.py'
TEMPLATE = ROOT / 'spiderfoot' / 'templates' / 'pages' / 'collector_enem_fatec.html'

class TestPatchV23(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.source = COLLECTOR.read_text(encoding='utf-8')
        cls.blueprint = BLUEPRINT.read_text(encoding='utf-8')
        cls.template = TEMPLATE.read_text(encoding='utf-8')
        ast.parse(cls.source)
        ast.parse(cls.blueprint)

    def test_openverse_uses_images_api(self):
        self.assertIn('https://api.openverse.org/v1/images/?q={query}', self.source)
        self.assertNotIn('https://api.openverse.org/v1/search/?q={query}', self.source)

    def test_youtube_format_is_not_mp4_only(self):
        self.assertIn('return "bestvideo*+bestaudio/best" if ffmpeg_available else "best"', self.source)
        self.assertNotIn('"format": "best[ext=mp4]/best"', self.source)

    def test_youtube_failed_downloads_are_logged_and_retryable(self):
        self.assertIn('youtube_downloads_falhos.csv', self.source)
        self.assertIn('resultado_com_falha_download', self.source)

    def test_interrupted_youtube_partials_are_detected(self):
        self.assertIn('Video_*_{video_id}.*.part', self.source)
        self.assertIn('RETOMANDO DOWNLOAD PARCIAL', self.source)

    def test_bing_and_yandex_are_not_mislabelled_ddgs_backends(self):
        self.assertIn('"yandex": {"label": "Yandex (página web)", "kind": "html"', self.source)
        self.assertIn('"bing": {"label": "Bing", "kind": "html"', self.source)
        self.assertIn('"startpage": {"label": "Startpage", "kind": "ddgs", "backend": "startpage"', self.source)

    def test_repeat_completed_option_remains_wired(self):
        self.assertIn('repetir_concluidas', self.blueprint)
        self.assertIn('--repetir-concluidas', self.blueprint)
        self.assertIn('id="repetirconcluidas"', self.template)
        self.assertIn("repetir_concluidas:$('repetirconcluidas').checked", self.template)

if __name__ == '__main__':
    unittest.main(verbosity=2)
