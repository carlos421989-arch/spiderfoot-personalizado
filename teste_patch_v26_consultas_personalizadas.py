import importlib.util
import sys
import types
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parent
COLLECTOR = ROOT / "tools" / "Coletor_ENEM_FATEC" / "coletar_materiais.py"
BLUEPRINT = ROOT / "spiderfoot" / "blueprints" / "collector.py"
TEMPLATE = ROOT / "spiderfoot" / "templates" / "pages" / "collector_enem_fatec.html"

sys.path.insert(0, str(COLLECTOR.parent))
# The test exercises query composition only; stub the document classifier dependency.
if "document_intelligence" not in sys.modules:
    stub = types.ModuleType("document_intelligence")
    stub.infer_meta = lambda *args, **kwargs: None
    stub.suggested_filename = lambda *args, **kwargs: ""
    sys.modules["document_intelligence"] = stub
spec = importlib.util.spec_from_file_location("collector_script_v26", COLLECTOR)
module = importlib.util.module_from_spec(spec)
spec.loader.exec_module(module)

class TestConsultasPersonalizadasExclusivas(unittest.TestCase):
    def test_custom_mode_returns_only_custom_lines(self):
        custom = ["minha consulta 1", "minha consulta 2"]
        standard = ["ENEM prova oficial"]
        general = ["receita de bolo"]
        self.assertEqual(
            module.build_query_list("personalizadas", standard, general, custom),
            custom,
        )

    def test_general_mode_combines_custom_and_general(self):
        result = module.build_query_list(
            "geral", ["ENEM prova"], ["receita"], ["meu termo"]
        )
        self.assertEqual(result, ["meu termo", "receita"])

    def test_all_mode_combines_three_query_sources(self):
        result = module.build_query_list(
            "todos", ["ENEM prova"], ["receita"], ["meu termo"]
        )
        self.assertEqual(result, ["meu termo", "ENEM prova", "receita"])

    def test_imported_queries_are_not_merged_in_custom_only_mode(self):
        source = BLUEPRINT.read_text(encoding="utf-8")
        self.assertIn("if imported_path.exists() and mode in ('todos', 'pesquisas', 'geral'):", source)
        self.assertNotIn("mode in ('todos', 'pesquisas', 'personalizadas', 'geral')", source)

    def test_ui_explains_exclusive_behavior(self):
        source = TEMPLATE.read_text(encoding="utf-8")
        self.assertIn("Somente consultas personalizadas (apenas linhas coladas abaixo)", source)
        self.assertIn("não inclui a lista ENEM/FATEC, pesquisas gerais, consultas importadas de CSV/JSON", source)

    def test_empty_custom_mode_has_specific_validation(self):
        source = BLUEPRINT.read_text(encoding="utf-8")
        self.assertIn("Este modo usa somente as linhas coladas nesse campo", source)

if __name__ == "__main__":
    unittest.main(verbosity=2)
