"""Structural regression checks for Odoo's UTF-8 article rendering path."""
import ast
from pathlib import Path
import unittest
import xml.etree.ElementTree as E

ROOT = Path(__file__).parents[1]
LAYOUT = 'elksevent.event_pdf_html_container'


class PdfLayoutTests(unittest.TestCase):
    def test_shared_layout_uses_odoo_article(self):
        root = E.parse(ROOT / 'report/event_pdf_layout.xml')
        node = root.find(".//t[@t-call='web.html_container']/div")
        self.assertIn('article', node.get('class').split())
        self.assertIn('sans-serif', node.get('style'))
        self.assertIn('font-variant-ligatures:none', node.get('style'))
        self.assertIsNotNone(node.find("t[@t-out='0']"))

    def test_standalone_reports_cannot_bypass_shared_layout(self):
        for path in (ROOT / 'report').glob('*.xml'):
            if path.name == 'event_pdf_layout.xml':
                continue
            for template in E.parse(path).findall('.//template'):
                for call in template.findall(".//t[@t-call='web.html_container']"):
                    self.assertIsNotNone(call.find(".//t[@t-call='web.external_layout']"), path.name)
                for call in template.findall(f".//t[@t-call='{LAYOUT}']"):
                    self.assertIsNone(call.find(".//t[@t-call='web.external_layout']"), path.name)

    def test_board_footer_has_plain_separator(self):
        source = (ROOT / 'report/event_board_agenda_report.xml').read_text()
        self.assertIn('event(s) - combined total:', source)
        self.assertNotIn('&#8212;', source)
        self.assertNotIn('â€', source)

    def test_layout_loads_before_its_callers(self):
        data = ast.literal_eval((ROOT / '__manifest__.py').read_text())['data']
        position = data.index('report/event_pdf_layout.xml')
        for name in data:
            path = ROOT / name
            if name.startswith('report/') and path.exists() and LAYOUT in path.read_text():
                self.assertGreater(data.index(name), position)


if __name__ == '__main__':
    unittest.main()
