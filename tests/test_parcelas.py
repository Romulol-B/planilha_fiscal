import copy
from contextlib import redirect_stdout
from datetime import date
import io
import json
from pathlib import Path
import tempfile
import unittest

from velas_sheets.audit import balance_formula, total_formula
from velas_sheets.finance import Planner
from velas_sheets.installments import HEADERS, MONTHS, purchase, serial, status, values
from velas_sheets.operations import apply, pending
from velas_sheets.model import HEADERS as FC_HEADERS

FIXTURE = json.loads((Path(__file__).parent / 'fixtures/sheets_modelo.json').read_text())


def buy(**overrides):
    return {'id': 'pedido1', 'data': '2026-01-10', 'parcelas': 3,
            'primeiro_vencimento': '2026-02-10', 'produtos': [
                {'id': 'cera', 'descricao': 'Cera', 'quantidade': 2,
                 'centro_custo': 'Conta  a pagar', 'valor_total': '90,00'}], **overrides}


class MemorySheets:
    def __init__(self, name):
        self.name, self.sheets, self.rows = name, {}, {}
        self.calls = 0
        self.fail_after = False
        self.fail_before = False
        for i, title in enumerate(['FC '+m for m in MONTHS] + ['Anal Fin - consolidado']):
            self.sheets[title] = {'properties': {'sheetId': i+1, 'title': title,
                'gridProperties': {'rowCount': 100, 'columnCount': 26}}}
            self.rows[title] = {}
            if title.startswith('FC '):
                self.rows[title][2] = copy.deepcopy(FIXTURE['header']) + [{}, {}]
                for row in range(3, 97):
                    cells = copy.deepcopy(FIXTURE['rows'][1]) + [{}, {}]
                    cells[6]['userEnteredValue'] = {'formulaValue': balance_formula(row)}
                    self.rows[title][row] = cells
                self.rows[title][97] = [{} for _ in range(9)]
                self.rows[title][97][6] = {'userEnteredValue': {'formulaValue': total_formula(97)}}
                self.rows[title][1] = [{} for _ in range(9)]
                if i > 0:
                    self.rows[title][1][6] = {'userEnteredValue': {'formulaValue': f"='FC {MONTHS[i-1]}'!G97"}}
                self.rows[title][97][5] = {'userEnteredValue': {'formulaValue': '=SUM(F3:F96)'}}
            else:
                for row, option in enumerate(FIXTURE['options'], 3):
                    self.rows[title][row] = [{}, {'userEnteredValue': {'stringValue': option}, 'formattedValue': option}]

    def metadata(self):
        return {'spreadsheetId': self.name, 'sheets': copy.deepcopy(list(self.sheets.values()))}

    def cells(self, title, start, end, start_col=0, end_col=7):
        if title not in self.sheets:
            raise ValueError(title)
        return [copy.deepcopy((self.rows[title].get(row, []) + [{} for _ in range(end_col)])[start_col:end_col])
                for row in range(start, end+1)]

    def batch(self, requests):
        if self.fail_before:
            self.fail_before = False
            raise RuntimeError('Rede indisponível antes de gravar')
        self.calls += 1
        for request in requests:
            if 'addSheet' in request:
                props = copy.deepcopy(request['addSheet']['properties'])
                if props['title'] in self.sheets:
                    raise ValueError('aba duplicada')
                self.sheets[props['title']] = {'properties': props}
                self.rows[props['title']] = {}
            elif 'updateCells' in request:
                spec = request['updateCells']
                title = next(t for t, s in self.sheets.items() if s['properties']['sheetId'] == spec['range']['sheetId'])
                row, col = spec['range']['startRowIndex']+1, spec['range']['startColumnIndex']
                cells = self.rows[title].setdefault(row, [{} for _ in range(26)])
                while len(cells) < col + len(spec['rows'][0]['values']):
                    cells.append({})
                for j, c in enumerate(spec['rows'][0]['values'], col):
                    cells[j].pop('userEnteredValue', None)
                    if c.get('userEnteredValue'):
                        cells[j]['userEnteredValue'] = copy.deepcopy(c['userEnteredValue'])
            # Formatação e ocultação não afetam valores no simulador.
        if self.fail_after:
            self.fail_after = False
            raise RuntimeError('Timeout depois de gravar')


class InstallmentTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.directory = Path(self.tmp.name) / 'operations'
        self.books = {name: MemorySheets(name) for name in ('y26', 'y27')}
        self.config = {'planilha_id': 'y26', 'anos': {'2026': 'y26', '2027': 'y27'}}
        self.factory = self.books.__getitem__

    def planner(self):
        return Planner(self.config, self.factory)

    def register(self, data=None):
        plan = self.planner().register(data or buy())
        apply(plan, self.directory, self.factory)
        return plan

    def test_preview_is_read_only_and_three_months(self):
        plan = self.planner().register(buy())
        self.assertEqual([r['aba'] for r in plan['summary']], ['FC FEV', 'FC MAR', 'FC ABR'])
        self.assertEqual([r['previsto'] for r in plan['summary']], [-30]*3)
        self.assertEqual(self.books['y26'].calls, 0)
        self.assertFalse(self.directory.exists())

    def test_apply_and_repeated_registration(self):
        before = copy.deepcopy(self.books['y26'].rows['FC JAN'])
        self.register()
        book = self.books['y26']
        row = values(book.cells('FC FEV', 3, 3, 0, 9)[0])
        self.assertEqual(row[:6], [serial('2026-01-10'), 'Cera — parcela 1/3', 'Conta  a pagar', serial('2026-02-10'), -30, None])
        self.assertEqual(book.rows['FC JAN'], before)
        self.assertEqual(self.planner().register(buy())['steps'], [])
        self.assertEqual(len(self.planner().listing()), 3)
        self.assertEqual(pending(self.directory), [])
        journal = json.loads(next(self.directory.glob('*.json')).read_text())
        self.assertEqual(journal['state'], 'complete')
        self.assertEqual(journal['version'], 2)
        self.assertIn('before', journal['steps'][0]['tables'][0]['actions'][0])

    def test_rounding_multiple_products_and_quantity(self):
        raw = buy()
        raw['produtos'][0]['valor_total'] = '100.00'
        raw['produtos'].append({'id': 'pavio', 'descricao': 'Pavio', 'quantidade': 100,
                               'centro_custo': 'Conta a receber', 'valor_total': '10,01'})
        r = purchase(raw)
        self.assertEqual([v[10] for v in r[:3]], [-33.34, -33.33, -33.33])
        self.assertEqual([v[10] for v in r[3:]], [-3.34, -3.34, -3.33])
        self.assertAlmostEqual(sum(v[10] for v in r), -110.01)
        self.register(raw)
        self.assertEqual(len(self.planner().listing()), 6)

    def test_calendar_anchor_and_leap_year(self):
        for first, expected in [('2026-01-31', ['2026-01-31','2026-02-28','2026-03-31']),
                                ('2028-01-30', ['2028-01-30','2028-02-29','2028-03-30'])]:
            r = purchase(buy(data=first, primeiro_vencimento=first))
            self.assertEqual([v[9] for v in r], [serial(d) for d in expected])

    def test_payment_same_month_and_idempotence(self):
        self.register()
        plan = self.planner().pay('pedido1:cera:001', '2026-02-11')
        apply(plan, self.directory, self.factory)
        row = values(self.books['y26'].cells('FC FEV', 3, 3, 0, 9)[0])
        self.assertEqual(row[0], serial('2026-02-11'))
        self.assertEqual(row[4:6], [-30, -30])
        self.assertEqual(self.planner().pay('pedido1:cera:001', '2026-02-11')['steps'], [])
        self.assertEqual(len(self.planner().listing(situacao='paga')), 1)
        with self.assertRaisesRegex(ValueError, 'já paga'):
            self.planner().pay('pedido1:cera:001', '2026-02-12')
        self.assertEqual(self.planner().register(buy())['steps'], [])

    def test_late_and_early_payments_keep_prediction(self):
        self.register()
        apply(self.planner().pay('pedido1:cera:001', '2026-03-15'), self.directory, self.factory)
        book = self.books['y26']
        self.assertEqual(values(book.cells('FC FEV', 3, 3)[0])[4:6], [-30, None])
        march = [values(r) for r in book.cells('FC MAR', 3, 5, 0, 9)]
        paid = next(r for r in march if r[8] == 'pagamento')
        self.assertEqual(paid[4:6], [None, -30])
        apply(self.planner().pay('pedido1:cera:003', '2026-01-20'), self.directory, self.factory)
        self.assertEqual(values(book.cells('FC JAN', 3, 3)[0])[4:6], [None, -30])

    def test_missing_year_capacity_and_category_write_nothing(self):
        for raw, pattern in [(buy(primeiro_vencimento='2028-01-10'), '2028'),
                             (buy(produtos=[{**buy()['produtos'][0], 'centro_custo': 'Inexistente'}]), 'validação')]:
            with self.assertRaisesRegex(ValueError, pattern):
                self.planner().register(raw)
            self.assertEqual(sum(b.calls for b in self.books.values()), 0)
        for row in range(3,97):
            self.books['y26'].rows['FC FEV'][row][1]['userEnteredValue'] = {'stringValue': 'Manual'}
        with self.assertRaisesRegex(ValueError, 'sem espaço'):
            self.planner().register(buy())

    def test_conflicting_registration_and_manual_edits(self):
        self.register()
        with self.assertRaisesRegex(ValueError, 'Conflito'):
            self.planner().register(buy(parcelas=4))
        self.books['y26'].rows['FC FEV'][3][5]['userEnteredValue'] = {'numberValue': -20}
        with self.assertRaisesRegex(ValueError, 'conflito'):
            self.planner().pay('pedido1:cera:001', '2026-02-10')

    def test_resumption_between_years_and_response_lost(self):
        raw = buy(data='2026-11-10', primeiro_vencimento='2026-12-10')
        plan = self.planner().register(raw)
        self.books['y26'].fail_before = True  # Principal é gravada por último.
        with self.assertRaises(RuntimeError):
            apply(plan, self.directory, self.factory)
        self.assertEqual(self.books['y27'].calls, 1)
        path = pending(self.directory)[0]
        apply(json.loads(path.read_text()), self.directory, self.factory, path)
        self.assertEqual(self.books['y27'].calls, 1)
        self.assertEqual(len(self.planner().listing()), 3)
        plan = self.planner().pay('pedido1:cera:001', '2027-01-11')
        self.books['y27'].fail_after = True
        with self.assertRaises(RuntimeError):
            apply(plan, self.directory, self.factory)
        calls = self.books['y27'].calls
        path = pending(self.directory)[0]
        apply(json.loads(path.read_text()), self.directory, self.factory, path)
        self.assertEqual(self.books['y27'].calls, calls)
        self.assertEqual(len(self.planner().listing(situacao='paga')), 1)

    def test_unrelated_concurrent_change_is_preserved(self):
        plan = self.planner().register(buy())
        self.books['y26'].rows['FC MAR'][5][1]['userEnteredValue'] = {'stringValue': 'Alterado'}
        apply(plan, self.directory, self.factory)
        self.assertEqual(self.books['y26'].rows['FC MAR'][5][1]['userEnteredValue']['stringValue'], 'Alterado')

    def test_technical_columns_and_formulas_protected(self):
        self.books['y26'].rows['FC FEV'][50][8]['userEnteredValue'] = {'stringValue': 'Não apagar'}
        with self.assertRaisesRegex(ValueError, 'H:I'):
            self.planner().register(buy())
        self.books['y26'].rows['FC FEV'][50][8].clear()
        self.books['y26'].rows['FC FEV'][3][6].pop('userEnteredValue')
        with self.assertRaisesRegex(ValueError, 'saldo incompatível'):
            self.planner().register(buy())

    def test_changed_total_position_is_respected(self):
        book = self.books['y26']
        book.rows['FC FEV'][96] = book.rows['FC FEV'].pop(97)
        book.rows['FC FEV'][96][5]['userEnteredValue']['formulaValue'] = '=SUM(F3:F95)'
        book.rows['FC FEV'][96][6]['userEnteredValue']['formulaValue'] = total_formula(96)
        self.register()
        self.assertEqual(values(book.cells('FC FEV', 96, 96)[0])[5], '=SUM(F3:F95)')

    def test_category_list_change_prevents_write(self):
        plan = self.planner().register(buy())
        book = self.books['y26']
        row = next(r for r, cells in book.rows['Anal Fin - consolidado'].items() if cells[1]['userEnteredValue']['stringValue'] == 'Conta  a pagar')
        book.rows['Anal Fin - consolidado'][row][1]['userEnteredValue'] = {'stringValue': 'Alterado'}
        book.rows['Anal Fin - consolidado'][row][1]['formattedValue'] = 'Alterado'
        with self.assertRaisesRegex(RuntimeError, 'Conflito'):
            apply(plan, self.directory, self.factory)
        self.assertEqual(self.books['y26'].calls, 0)
        self.assertFalse(self.directory.exists())

    def test_cli_preview_apply_listing_and_pending_resume(self):
        from unittest.mock import patch
        from velas_sheets.__main__ import main
        config = Path(self.tmp.name) / 'config.toml'
        config.write_text('[google_sheets]\nplanilha_id="y26"\ncredenciais="missing.json"\n'
                          'aba_modelo="FC NOV"\naba_teste="TESTE CLI"\n'
                          '[anos]\n"2026"="y26"\n"2027"="y27"\n')
        source = Path(self.tmp.name) / 'compra.json'
        source.write_text(json.dumps(buy()))
        with patch('velas_sheets.finance_cli.Client', side_effect=lambda book, credentials: self.books[book]), redirect_stdout(io.StringIO()):
            self.assertEqual(main(['--config', str(config), 'compra', str(source)]), 0)
            self.assertFalse((config.parent / 'dados').exists())
            self.assertEqual(main(['--config', str(config), 'compra', str(source), '--aplicar']), 0)
            self.assertEqual(main(['--config', str(config), 'parcelas', '--compra', 'pedido1']), 0)
            self.assertEqual(main(['--config', str(config), 'pagar', 'pedido1:cera:001', '--data', '2026-03-10', '--aplicar']), 0)

    def test_filters_and_invalid_input(self):
        self.register()
        self.assertEqual(len(self.planner().listing(desde='2026-03-01', ate='2026-03-31')), 1)
        self.assertEqual(self.planner().listing(compra='outra'), [])
        for raw in [buy(parcelas=True), buy(data='2026-02-30'), buy(id='x:y'),
                    buy(produtos=[{**buy()['produtos'][0], 'valor_total': '1.000,00'}])]:
            with self.assertRaises(ValueError):
                purchase(raw)


if __name__ == '__main__':
    unittest.main()
