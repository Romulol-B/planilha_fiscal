import copy
from contextlib import redirect_stderr, redirect_stdout
from datetime import date
import io
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from velas_sheets.__main__ import main, prepare_test
from velas_sheets.client import Client
from velas_sheets.model import load_edits, parse_value, plan_edits, verify_edits


ROOT = Path(__file__).resolve().parents[1]
FIXTURE = json.loads((ROOT / 'tests/fixtures/sheets_modelo.json').read_text())


class FakeClient:
    """Modelo em memória com metadados das células reais de FC NOV."""

    email = 'teste@example.invalid'

    def __init__(self):
        self.meta = {'spreadsheetId': 'test', 'properties': {'title': 'Financeiro'}, 'sheets': [
            {'properties': {'sheetId': i, 'title': title,
                            'gridProperties': {'rowCount': 1000, 'columnCount': 26}}}
            for i, title in enumerate(['FC NOV', 'TESTE CLI', 'Anal Fin - consolidado'])
        ]}
        self.rows = {2: copy.deepcopy(FIXTURE['header']),
                     3: copy.deepcopy(FIXTURE['rows'][0]), 4: copy.deepcopy(FIXTURE['rows'][1])}
        self.writes = []

    def metadata(self):
        return copy.deepcopy(self.meta)

    def cells(self, title, start, end, start_col=0, end_col=7):
        if title == 'Anal Fin - consolidado':
            return [[{'formattedValue': v}] for v in FIXTURE['options']]
        return [copy.deepcopy(self.rows.get(row, [{} for _ in range(7)])[start_col:end_col])
                for row in range(start, end + 1)]

    def batch(self, requests):
        self.writes.append(copy.deepcopy(requests))
        for request in requests:
            if 'duplicateSheet' in request:
                spec = request['duplicateSheet']
                self.meta['sheets'].append({'properties': {'sheetId': spec['newSheetId'],
                    'title': spec['newSheetName'], 'gridProperties': {'rowCount': 1000, 'columnCount': 26}}})
                continue
            spec = request['updateCells']
            row = spec['range']['startRowIndex'] + 1
            col = spec['range']['startColumnIndex']
            self.rows.setdefault(row, [{} for _ in range(7)])
            value = spec['rows'][0]['values'][0].get('userEnteredValue')
            if value:
                self.rows[row][col]['userEnteredValue'] = copy.deepcopy(value)
            else:
                self.rows[row][col].pop('userEnteredValue', None)
        return {}


class SheetsTests(unittest.TestCase):
    def setUp(self):
        self.client = FakeClient()
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.directory = Path(self.temp.name)
        self.config = self.directory / 'config.toml'
        self.config.write_text('[google_sheets]\nplanilha_id="test"\ncredenciais="missing.json"\n'
                               'aba_modelo="FC NOV"\naba_teste="TESTE CLI"\n')

    def invoke(self, *args):
        output = io.StringIO()
        with patch('velas_sheets.__main__.Client', return_value=self.client), \
                redirect_stdout(output), redirect_stderr(output):
            code = main(['--config', str(self.config), *map(str, args)])
        return code, output.getvalue()

    def edits_file(self, edits):
        path = self.directory / 'edits.json'
        path.write_text(json.dumps(edits))
        return path

    def test_preview_never_writes(self):
        code, out = self.invoke('editar', ROOT / 'exemplos/sheets-teste.json')
        self.assertEqual(code, 0, out)
        self.assertIn('Prévia', out)
        self.assertEqual(self.client.writes, [])
        self.assertFalse((self.directory / 'dados').exists())

    def test_apply_typed_values_preserves_formulas_validation_and_format(self):
        before = copy.deepcopy(self.client.rows)
        code, out = self.invoke('editar', ROOT / 'exemplos/sheets-teste.json', '--aplicar')
        self.assertEqual(code, 0, out)
        self.assertEqual(len(self.client.writes), 1)
        self.assertEqual(self.client.rows[3][0]['userEnteredValue'],
                         {'numberValue': (date(2026, 11, 21) - date(1899, 12, 30)).days})
        self.assertEqual(self.client.rows[3][5]['userEnteredValue'], {'numberValue': -15})
        for row in (3, 4):
            self.assertEqual(self.client.rows[row][6], before[row][6])
            self.assertEqual(self.client.rows[row][2], before[row][2])
            for col in range(7):
                self.assertEqual(self.client.rows[row][col].get('userEnteredFormat'),
                                 before[row][col].get('userEnteredFormat'))
        saved = list((self.directory / 'dados/sheets_backups').glob('*.json'))
        self.assertEqual(len(saved), 1)
        data = json.loads(saved[0].read_text())
        self.assertEqual(data['linhas_antes']['3'], before[3])
        code, out = self.invoke('editar', ROOT / 'exemplos/sheets-teste.json', '--aplicar')
        self.assertEqual(code, 0, out)
        self.assertEqual(len(self.client.writes), 1)

    def test_edit_existing_row_preserves_omitted_fields_and_can_clear(self):
        self.client.rows[3][1]['userEnteredValue'] = {'stringValue': 'Descrição original'}
        self.client.rows[3][5]['userEnteredValue'] = {'numberValue': -99}
        path = self.edits_file([{'linha': 3, 'pagamento': None}])
        code, out = self.invoke('editar', path, '--aplicar')
        self.assertEqual(code, 0, out)
        self.assertNotIn('userEnteredValue', self.client.rows[3][5])
        self.assertEqual(self.client.rows[3][1]['userEnteredValue']['stringValue'], 'Descrição original')

    def test_unknown_category_blocks_entire_batch(self):
        path = self.edits_file([{'linha': 3, 'pagamento': '-10'},
                               {'linha': 4, 'centro_custo': 'Inventado'}])
        code, out = self.invoke('editar', path, '--aplicar')
        self.assertEqual(code, 1)
        self.assertIn('lista permitida', out)
        self.assertEqual(self.client.writes, [])

    def test_range_validation_reads_live_options(self):
        path = self.edits_file([{'linha': 3, 'centro_custo': 'Conta  a pagar'}])
        code, out = self.invoke('editar', path, '--aplicar')
        self.assertEqual(code, 0, out)
        self.assertEqual(self.client.rows[3][2]['userEnteredValue']['stringValue'], 'Conta  a pagar')

    def test_formula_and_header_protection(self):
        path = self.edits_file([{'linha': 3, 'pagamento': '-10'}])
        self.client.rows[3][5]['userEnteredValue'] = {'formulaValue': '=SUM(A1:A2)'}
        code, out = self.invoke('editar', path, '--aplicar')
        self.assertEqual(code, 1)
        self.assertIn('fórmula', out)
        self.assertEqual(self.client.writes, [])
        self.client.rows[2][0]['userEnteredValue'] = {'stringValue': 'Outra coluna'}
        code, out = self.invoke('editar', path, '--aplicar')
        self.assertEqual(code, 1)
        self.assertIn('Cabeçalhos', out)

    def test_production_sheet_is_not_writable(self):
        with self.assertRaisesRegex(ValueError, 'apenas em abas'):
            plan_edits(self.client, self.client.metadata(), 'FC NOV', [(3, {5: {'numberValue': -1}})])
        self.assertEqual(self.client.writes, [])

    def test_invalid_json_shape_rows_and_values(self):
        for edits in ([], [{'linha': True, 'pagamento': 1}], [{'linha': 97, 'pagamento': 1}],
                      [{'linha': 3, 'saldo': 1}], [{'linha': 3}],
                      [{'linha': 3, 'pagamento': 1}, {'linha': 3, 'pagamento': 2}]):
            with self.subTest(edits=edits), self.assertRaises(ValueError):
                load_edits(self.edits_file(edits))
        for value in ('1.000,00', 'NaN', float('inf'), True, '1e3', '1.001'):
            with self.subTest(value=value), self.assertRaises(ValueError):
                parse_value('pagamento', value)
        with self.assertRaises(ValueError):
            parse_value('data', '2026-02-30')
        self.assertEqual(parse_value('descritivo', '=IMPORTXML("x")'),
                         {'stringValue': '=IMPORTXML("x")'})

    def test_concurrent_change_prevents_write(self):
        original = self.client.metadata
        count = 0

        def metadata():
            nonlocal count
            count += 1
            if count == 2:
                self.client.rows[3][1]['userEnteredValue'] = {'stringValue': 'Outra pessoa editou'}
            return original()

        self.client.metadata = metadata
        code, out = self.invoke('editar', ROOT / 'exemplos/sheets-teste.json', '--aplicar')
        self.assertEqual(code, 1)
        self.assertIn('mudou', out)
        self.assertEqual(self.client.writes, [])

    def test_post_write_verification_detects_formula_loss(self):
        edits = [(3, {5: {'numberValue': -5}})]
        _, snapshots, requests = plan_edits(self.client, self.client.metadata(), 'TESTE CLI', edits)
        self.client.batch(requests)
        self.client.rows[3][6].pop('userEnteredValue')
        with self.assertRaisesRegex(RuntimeError, 'divergiu'):
            verify_edits(self.client, 'TESTE CLI', edits, snapshots)

    def test_prepare_duplicate_and_repeat_preserves_existing_content(self):
        self.client.meta['sheets'] = [s for s in self.client.meta['sheets'] if s['properties']['title'] != 'TESTE CLI']
        with redirect_stdout(io.StringIO()):
            config = {'aba_modelo': 'FC NOV', 'aba_teste': 'TESTE CLI'}
            prepare_test(self.client, self.client.metadata(), config)
            prepare_test(self.client, self.client.metadata(), config)
        self.assertEqual(len(self.client.writes), 1)
        self.assertIn('duplicateSheet', self.client.writes[0][0])

    def test_client_maps_offsets_without_shifting_sparse_cells(self):
        client = object.__new__(Client)
        response = {'sheets': [{'data': [{'startRow': 2, 'startColumn': 1,
                    'rowData': [{'values': [{'formattedValue': 'B3'}, {}, {'formattedValue': 'D3'}]}]}]}]}
        with patch.object(client, 'request', return_value=response):
            rows = client.cells('TESTE CLI', 3, 4)
        self.assertEqual(rows[0][1], {'formattedValue': 'B3'})
        self.assertEqual(rows[0][3], {'formattedValue': 'D3'})
        self.assertEqual(rows[1], [{}] * 7)


if __name__ == '__main__':
    unittest.main()
