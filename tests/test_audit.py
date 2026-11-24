from contextlib import redirect_stdout
import copy
import io
from pathlib import Path
import shutil
import subprocess
import tempfile
import unittest
from unittest.mock import patch
import warnings
from zipfile import ZipFile
import os

from openpyxl import Workbook, load_workbook
from openpyxl.worksheet.datavalidation import DataValidation
from velas_sheets.audit import XlsxReader, audit, balance_formula, check_month, total_formula
from velas_sheets.client import Client
from velas_sheets.xlsx_model import repair, sheet_paths
from velas_sheets.installments import MONTHS

ROOT = Path(__file__).resolve().parents[1]
MODEL = ROOT / 'exemplos/modelo-demonstracao.xlsx'


def fixture(path):
    book = Workbook()
    book.remove(book.active)
    for i, month in enumerate(MONTHS):
        sheet = book.create_sheet('FC '+month)
        for row in range(3, 7):
            sheet[f'G{row}'] = f'=IF(E{row}+F{row}=0,0,G{row-1}+F{row})'
        sheet['F7'] = '=SUM(F3:F6)'
        sheet['G7'] = '=IF(F7=0,0,G1+F7)'
        if i:
            sheet['G1'] = f"='FC {MONTHS[i-1]}'!G7"
        validation = DataValidation(type='list',formula1='"Categoria"')
        validation.add('C3:C6')
        sheet.add_data_validation(validation)
    sheet = book.create_sheet('DRE')
    for row in range(66,80):
        sheet[f'A{row}'] = '=#REF!'
    consolidated = book.create_sheet('Anal Fin - consolidado')
    for row in range(69,83):
        consolidated[f'B{row}'] = f'Categoria {row}'
    book.save(path)


class AuditTests(unittest.TestCase):
    def test_canonical_model_and_local_command_need_no_credentials(self):
        from velas_sheets.__main__ import main
        self.assertEqual(audit(XlsxReader(MODEL)), [])
        with redirect_stdout(io.StringIO()) as output:
            self.assertEqual(main(['--config','inexistente.toml','auditar-modelo','--xlsx',str(MODEL)]),0)
        self.assertIn('auditoria OK',output.getvalue())

    def test_online_audit_reads_in_batches_and_detects_calculation_errors(self):
        client = Client.__new__(Client)
        titles = ('FC DEZ', 'DRE', 'Anal Fin - consolidado', 'Parcelas')
        metadata = {'sheets': [{'properties': {
            'title': title, 'gridProperties': {'rowCount': 1000, 'columnCount': 26},
        }} for title in titles]}
        responses = [
            metadata,
            {'sheets': [
                {'properties': {'title': 'FC DEZ'}, 'data': [
                    {'startRow': 2, 'startColumn': 6, 'rowData': [
                        {'values': [{'userEnteredValue': {'formulaValue': '=IF(G1+F3=0;0;G1+F3)'}}]},
                        {'values': [{'userEnteredValue': {'formulaValue': '=IF(F4=0;0;G3+F4)'}}]},
                    ]},
                    {'startRow': 2, 'startColumn': 2, 'rowData': [
                        {'values': [{'dataValidation': {'strict': True}}]},
                    ]},
                    {'startRow': 4, 'startColumn': 5, 'rowData': [
                        {'values': [{'userEnteredValue': {'formulaValue': '=SUM(F3:F4)'}}]},
                    ]},
                ]},
            ]},
            {'sheets': [{'properties': {'title': 'Parcelas'}, 'data': [
                {'startRow': 251, 'startColumn': 14, 'rowData': [{'values': [
                    {'effectiveValue': {'errorValue': {'type': 'DIVIDE_BY_ZERO'}}},
                ]}]},
            ]}]},
        ]
        with patch.object(client, 'request', side_effect=responses) as request:
            snapshot = client.audit_snapshot()
        self.assertEqual(request.call_count, 3)
        self.assertTrue(all(call.args == ('GET',) for call in request.call_args_list))
        self.assertEqual(
            [r for call in request.call_args_list[1:] for r in call.kwargs['params']['ranges']],
            [f"'{title}'!A1:Z1000" for title in titles],
        )
        issues = audit(snapshot)
        self.assertTrue(any(issue.startswith('FC DEZ!G4: saldo incompatível') for issue in issues))
        self.assertIn('Parcelas!O252: erro de cálculo (DIVIDE_BY_ZERO).', issues)

    def test_repair_backups_and_preserves_every_unmodified_zip_member(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory)/'modelo.xlsx'
            fixture(path)
            original = path.read_bytes()
            self.assertTrue(audit(XlsxReader(path)))
            backup = repair(path,Path(directory)/'backup')
            self.assertEqual(backup.read_bytes(),original)
            self.assertEqual(audit(XlsxReader(path)),[])
            with ZipFile(backup) as before, ZipFile(path) as after:
                sheets = sheet_paths(before)
                permitted = {'xl/workbook.xml', sheets['DRE'], *[sheets['FC '+m] for m in MONTHS]}
                self.assertEqual(before.namelist(),after.namelist())
                for member in before.namelist():
                    if member not in permitted:
                        self.assertEqual(before.read(member),after.read(member),member)
            self.assertIsNone(repair(path,Path(directory)/'backup'))

    def test_recalculation_in_libreoffice_on_disposable_fixture(self):
        executable = shutil.which('libreoffice') or shutil.which('soffice')
        if not executable:
            if os.environ.get('REQUIRE_LIBREOFFICE') == '1':
                self.fail('CI exige LibreOffice para validar resultados calculados.')
            self.skipTest('LibreOffice indisponível; obrigatório na CI.')
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            path = root/'modelo.xlsx'
            fixture(path)
            repair(path,root/'backup')
            book = load_workbook(path)
            book['FC JAN']['G1'] = 1000
            book['FC JAN']['F3'] = -100
            book['FC JAN']['F5'] = -25
            # Fevereiro vazio carrega 875. Março tem movimento líquido zero.
            book['FC MAR']['F3'] = 50
            book['FC MAR']['F5'] = -50
            book.save(path)
            out = root/'recalculado'; out.mkdir()
            subprocess.run([executable, '-env:UserInstallation='+ (root/'lo-profile').as_uri(), '--headless',
                            '--convert-to','xlsx','--outdir',str(out),str(path)],check=True,capture_output=True,timeout=60)
            with warnings.catch_warnings():
                warnings.simplefilter('ignore')
                result = load_workbook(out/path.name,data_only=True)
            self.assertEqual(result['FC JAN']['G4'].value,900)
            self.assertEqual(result['FC JAN']['G6'].value,875)
            self.assertEqual(result['FC JAN']['G7'].value,875)
            self.assertEqual(result['FC FEV']['G7'].value,875)
            self.assertEqual(result['FC MAR']['G4'].value,925)
            self.assertEqual(result['FC MAR']['G7'].value,875)
            self.assertEqual(result['FC ABR']['G1'].value,875)
            for row in range(66,80):
                self.assertEqual(result['DRE'][f'A{row}'].value,f'Categoria {row+3}')


class BalanceEquivalenceTests(unittest.TestCase):
    def setUp(self):
        self.rows = []
        for row in range(3, 8):
            cells = [{} for _ in range(7)]
            cells[2]['dataValidation'] = {'present': True}
            cells[6] = {'userEnteredValue': {'formulaValue': total_formula(row) if row == 7 else balance_formula(row)},
                        'effectiveValue': {'numberValue': 0}}
            self.rows.append(cells)

    def cells(self, title, start, end, start_col=0, end_col=7):
        return copy.deepcopy([r[start_col:end_col] for r in self.rows[start-3:end-2]])

    def formula(self, row, value):
        self.rows[row-3][6]['userEnteredValue'] = {'formulaValue': value}

    def test_equivalent_first_row_including_formula_observed_in_google(self):
        for formula in (
            '=IF(G1+F3=0;0;G1+F3)',
            '=F3+G$1',
            '=SOMA(F3;G$1)',
            '=SE(0=G$1+F3;0;G$1+F3)',
            '=IF(0<>SUM(G1,F3),F3+G1,0)',
            '=((G1)+(F3))+0',
            '=SUM(F3:F3,G1)',
            '= G1 - (-F3)',
        ):
            with self.subTest(formula=formula):
                self.formula(3, formula)
                check_month(self, 'FC DEZ', 7)

    def test_equivalent_running_and_closing_balances_preserve_original_formulas(self):
        for row, formula in {
            3: '=IF(G1+F3=0;0;G1+F3)',
            4: '=G3+F4',
            5: '=SUM(F3:F5)+G1',
            6: '=IF(SUM(G5,F6)=0,0,F6+G5)',
            7: '=IF(SUM(F7,G1)=0,0,G1+F7)',
        }.items():
            self.formula(row, formula)
        original = copy.deepcopy(self.rows)
        check_month(self, 'FC DEZ', 7)
        self.assertEqual(self.rows, original)
        self.formula(7, '=G6')
        check_month(self, 'FC DEZ', 7)

    def test_equal_current_zero_values_do_not_make_wrong_formulas_equivalent(self):
        original = copy.deepcopy(self.rows)
        for row, formula in (
            (3, '=IF(F3=0,0,G1+F3)'),
            (4, '=IF(E4+F4=0,0,G3+F4)'),
            (4, '=IF(G3+F4=0,G3+F4,0)'),
            (7, '=IF(F7=0,0,G1+F7)'),
            (4, '=SUM(G1,F4)'),
            (4, '=SUM(G1,F3:F5)'),
            (4, '=SUM(G1,F3:F4,F3)'),
            (4, '=G3+F4+E4'),
            (4, '=SUM(G1,F3:F4)+1'),
            (4, '=G4+F4'),
            (4, '=0'),
        ):
            with self.subTest(row=row, formula=formula):
                self.rows = copy.deepcopy(original)
                self.formula(row, formula)
                with self.assertRaisesRegex(ValueError, f'FC DEZ!G{row}: saldo incompatível'):
                    check_month(self, 'FC DEZ', 7)

    def test_previous_balance_must_pass_before_accepting_chained_balance(self):
        self.formula(3, '=F3')
        self.formula(4, '=G3+F4')
        with self.assertRaisesRegex(ValueError, 'FC DEZ!G3: saldo incompatível'):
            check_month(self, 'FC DEZ', 7)

    def test_unsupported_or_malformed_formulas_remain_blocked_with_clear_message(self):
        for formula in (None, '', '100', '=SUM(G1,F3', '=SUM(G1,F3)garbage',
                        '=SUM(G1,F3:F2)', '=SUM(G1,F3:G3)', '="G1+F3"',
                        '=INDIRECT("G1")+F3', '=IFERROR(G1+F3,0)',
                        '='+'('*40+'G1+F3'+')'*40, '='+'G1+'*1000+'F3'):
            with self.subTest(formula=formula):
                self.formula(3, formula)
                with self.assertRaisesRegex(ValueError, 'fórmula não reconhecida como equivalente'):
                    check_month(self, 'FC DEZ', 7)

    def test_equivalence_does_not_bypass_category_validation(self):
        self.formula(3, '=IF(G1+F3=0;0;G1+F3)')
        self.rows[0][2].clear()
        with self.assertRaisesRegex(ValueError, 'validação de centro de custo ausente'):
            check_month(self, 'FC DEZ', 7)
