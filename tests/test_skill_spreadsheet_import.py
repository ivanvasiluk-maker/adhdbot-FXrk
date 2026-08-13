import unittest
import io
import zipfile

from core.skill_importer import map_rows
from core.skill_spreadsheet import flatten, read_csv, read_xlsx
from scripts.import_skills import google_export_url


class SkillSpreadsheetImportTests(unittest.TestCase):
    def test_minimal_xlsx_is_read_without_optional_dependency(self):
        output = io.BytesIO()
        with zipfile.ZipFile(output, "w") as book:
            book.writestr("xl/workbook.xml", '''<workbook xmlns="http://schemas.openxmlformats.org/spreadsheetml/2006/main" xmlns:r="http://schemas.openxmlformats.org/officeDocument/2006/relationships"><sheets><sheet name="skills" sheetId="1" r:id="rId1"/></sheets></workbook>''')
            book.writestr("xl/_rels/workbook.xml.rels", '''<Relationships xmlns="http://schemas.openxmlformats.org/package/2006/relationships"><Relationship Id="rId1" Target="worksheets/sheet1.xml"/></Relationships>''')
            book.writestr("xl/worksheets/sheet1.xml", '''<worksheet xmlns="http://schemas.openxmlformats.org/spreadsheetml/2006/main"><sheetData><row r="1"><c r="A1" t="inlineStr"><is><t>skill_id</t></is></c><c r="B1" t="inlineStr"><is><t>title</t></is></c></row><row r="2"><c r="A2" t="inlineStr"><is><t>open_only</t></is></c><c r="B2" t="inlineStr"><is><t>Открыть</t></is></c></row></sheetData></worksheet>''')
        tables = read_xlsx(output.getvalue())
        self.assertEqual(tables[0].name, "skills")
        self.assertEqual(tables[0].rows[0]["skill_id"], "open_only")

    def test_google_edit_url_becomes_xlsx_export(self):
        value = google_export_url(
            "https://docs.google.com/spreadsheets/d/19A4NkJzZJj7mVCqSq5jmY5t1pqD8BrDb/edit?usp=sharing"
        )
        self.assertEqual(
            value,
            "https://docs.google.com/spreadsheets/d/19A4NkJzZJj7mVCqSq5jmY5t1pqD8BrDb/export?format=xlsx",
        )

    def test_russian_headers_map_to_review_safe_card(self):
        content = (
            "код навыка,версия,статус,название,подход,механизм,фазы действия,контекст,"
            "противопоказания,упрощение,минимальная версия,обычная версия,критерий завершения,"
            "первоисточник,проверка редактора\n"
            "cbt_check_prediction,1.0.0,production,Проверить прогноз,CBT,evaluation_avoidance,start,"
            "work,acute_crisis,safe_fallback,Открыть контакт,Сделать звонок,Звонок начат,CBT_REF,reviewed\n"
        ).encode()
        cards, problems = map_rows(flatten(read_csv(content)), source_ref="sheet:test")
        self.assertEqual(problems, [])
        self.assertEqual(cards[0]["skill_id"], "cbt_check_prediction")
        self.assertEqual(cards[0]["mechanisms"], ["evaluation_avoidance"])
        self.assertEqual(cards[0]["status"], "production")

    def test_unreviewed_production_row_is_rejected(self):
        rows = [{"skill_id": "unsafe", "title": "Навык", "instruction": "Шаг", "status": "production"}]
        cards, problems = map_rows(rows, source_ref="sheet:test")
        self.assertEqual(cards, [])
        self.assertIn("not explicitly reviewed", problems[0].message)


if __name__ == "__main__":
    unittest.main()
