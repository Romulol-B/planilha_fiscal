import copy
import json
import unittest
from unittest.mock import patch

import test_parcelas as fixtures
from test_parcelas import buy
from velas_sheets.audit import balance_formula, total_formula
from velas_sheets.installments import Parcela, purchase, purchase_records, values
from velas_sheets.operations import apply, cancel, diagnose, pending


class RecoveryTests(unittest.TestCase):
    setUp = fixtures.InstallmentTests.setUp
    planner = fixtures.InstallmentTests.planner
    register = fixtures.InstallmentTests.register

    def interrupt(self, raw=None, after=False):
        plan = self.planner().register(raw or buy())
        setattr(self.books['y26'], 'fail_after' if after else 'fail_before', True)
        with self.assertRaises(RuntimeError):
            apply(plan, self.directory, self.factory)
        path = pending(self.directory)[0]
        return json.loads(path.read_text()), path

    def test_unrelated_edits_and_response_lost_do_not_repeat_batch(self):
        plan, path = self.interrupt(after=True)
        self.books['y26'].rows['FC FEV'][50][1]['userEnteredValue'] = {'stringValue': 'Manual'}
        self.books['y26'].rows['FC MAR'][60][0]['userEnteredFormat'] = {'backgroundColor': {'red': 1}}
        apply(plan, self.directory, self.factory, path)
        self.assertEqual(self.books['y26'].calls, 1)
        self.assertEqual(values(self.books['y26'].cells('FC FEV',50,50)[0])[1], 'Manual')

    def test_equivalent_balances_allow_registration_payment_and_idempotence(self):
        plan = self.planner().register(buy())
        book = self.books['y26']
        for title in ('FC FEV', 'FC MAR', 'FC ABR'):
            book.rows[title][3][6]['userEnteredValue']['formulaValue'] = '=IF(G1+F3=0;0;G1+F3)'
            for row in range(4, 97):
                book.rows[title][row][6]['userEnteredValue']['formulaValue'] = f'=G{row-1}+F{row}'
            book.rows[title][97][6]['userEnteredValue']['formulaValue'] = '=G1+F97'
        apply(plan, self.directory, self.factory)
        self.assertEqual(self.planner().register(buy())['steps'], [])
        payment = self.planner().pay('pedido1:cera:001', '2026-02-11')
        apply(payment, self.directory, self.factory)
        self.assertEqual(self.planner().pay('pedido1:cera:001', '2026-02-11')['steps'], [])
        self.assertEqual(len(self.planner().listing(situacao='paga')), 1)
        self.assertEqual(book.rows['FC FEV'][4][6]['userEnteredValue']['formulaValue'], '=G3+F4')

    def test_explicit_balance_check_suspension_survives_journal_resume(self):
        book = self.books['y26']
        book.rows['FC FEV'][4][6]['userEnteredValue']['formulaValue'] = '=IF(F4=0,0,G3+F4)'
        with self.assertRaisesRegex(ValueError, 'FC FEV!G4'):
            self.planner().register(buy(parcelas=1))
        plan = fixtures.Planner(self.config, self.factory, skip_balance_check=True).register(buy(parcelas=1))
        self.assertTrue(plan['skip_balance_check'])
        book.fail_before = True
        with self.assertRaises(RuntimeError):
            apply(plan, self.directory, self.factory)
        path = pending(self.directory)[0]
        saved = json.loads(path.read_text())
        self.assertTrue(saved['skip_balance_check'])
        apply(saved, self.directory, self.factory, path)
        self.assertEqual(json.loads(path.read_text())['state'], 'complete')
        self.assertEqual(book.rows['FC FEV'][4][6]['userEnteredValue']['formulaValue'], '=IF(F4=0,0,G3+F4)')

    def test_reserved_row_occupied_reallocated_and_journal_saved_before_post(self):
        plan, path = self.interrupt()
        book = self.books['y26']
        book.rows['FC FEV'][3][1]['userEnteredValue'] = {'stringValue': 'Manual'}
        original = book.batch
        def batch(requests):
            saved = json.loads(path.read_text())
            table = next(t for s in saved['steps'] for t in s['tables'] if t['title'] == 'FC FEV')
            self.assertEqual(table['actions'][0]['row'], 4)
            original(requests)
        with patch.object(book, 'batch', side_effect=batch):
            apply(plan, self.directory, self.factory, path)
        self.assertEqual(values(book.cells('FC FEV',3,3)[0])[1], 'Manual')
        self.assertEqual(values(book.cells('FC FEV',4,4,0,9)[0])[7], 'pedido1:cera:001')

    def move(self, title, old, new):
        book = self.books['y26']
        cells = copy.deepcopy(book.rows[title][old])
        if title == 'Parcelas':
            cells[14]['userEnteredValue']['formulaValue'] = cells[14]['userEnteredValue']['formulaValue'].replace(f'M{old}',f'M{new}').replace(f'J{old}',f'J{new}')
            book.rows[title][old] = [{} for _ in range(16)]
        else:
            cells[6]['userEnteredValue']['formulaValue'] = balance_formula(new)
            for col in [0,1,2,3,4,5,7,8]:
                book.rows[title][old][col].pop('userEnteredValue', None)
        book.rows[title][new] = cells

    def test_paid_row_and_ledger_can_move_before_resumption(self):
        self.register()
        plan = self.planner().pay('pedido1:cera:001','2026-02-11')
        self.books['y26'].fail_before = True
        with self.assertRaises(RuntimeError):
            apply(plan,self.directory,self.factory)
        path = pending(self.directory)[0]
        self.move('FC FEV',3,9)
        self.move('Parcelas',2,25)
        apply(json.loads(path.read_text()),self.directory,self.factory,path)
        self.assertEqual(values(self.books['y26'].cells('FC FEV',9,9)[0])[5], -30)
        self.assertEqual(len(self.planner().listing(situacao='paga')), 1)

    def test_applied_rows_can_move_without_repeating(self):
        plan, path = self.interrupt(after=True)
        self.move('FC FEV',3,8)
        self.move('Parcelas',2,40)
        apply(plan,self.directory,self.factory,path)
        self.assertEqual(self.books['y26'].calls,1)

    def test_modified_managed_field_reports_expected_before_current(self):
        plan,path = self.interrupt(after=True)
        self.books['y26'].rows['FC FEV'][3][4]['userEnteredValue'] = {'numberValue': -999}
        report = diagnose(plan,self.factory)
        detail = next(r for r in report if r.get('celula') == 'E3' and r.get('aba') == 'FC FEV')
        self.assertEqual(detail['id'],'pedido1:cera:001')
        self.assertEqual(detail['atual'],{'numberValue':-999})
        self.assertEqual(detail['esperado'],{'numberValue':-30})
        with self.assertRaisesRegex(RuntimeError,'Conflito'):
            apply(plan,self.directory,self.factory,path)
        with self.assertRaisesRegex(ValueError,'Cancelamento bloqueado'):
            cancel(plan,path,self.factory,True)
        self.assertTrue(path.exists())

    def test_cancel_only_before_any_write_preserves_journal(self):
        plan,path = self.interrupt()
        original = path.read_bytes()
        cancel(plan,path,self.factory)
        self.assertEqual(path.read_bytes(),original)
        cancel(plan,path,self.factory,True)
        self.assertEqual(json.loads(path.read_text())['state'],'cancelled')
        self.assertEqual(pending(self.directory),[])
        self.assertEqual(self.books['y26'].calls,0)
        self.register()

    def test_cancel_after_another_year_written_is_refused(self):
        plan,path = self.interrupt(buy(data='2026-11-10',primeiro_vencimento='2026-12-10'))
        self.assertEqual(self.books['y27'].calls,1)
        with self.assertRaisesRegex(ValueError,'Cancelamento bloqueado'):
            cancel(plan,path,self.factory,True)

    def test_legacy_v1_retains_conservative_snapshots(self):
        with patch('velas_sheets.operations_v2.compile_plan',side_effect=lambda p:p):
            plan,path = self.interrupt()
        self.assertEqual(plan['version'],1)
        self.books['y26'].rows['FC FEV'][50][1]['userEnteredValue'] = {'stringValue':'Manual'}
        with self.assertRaisesRegex(RuntimeError,'Conflito'):
            apply(plan,self.directory,self.factory,path)
        self.books['y26'].rows['FC FEV'][50][1].pop('userEnteredValue')
        apply(plan,self.directory,self.factory,path)
        self.assertEqual(json.loads(path.read_text())['version'],1)

    def test_duplicate_or_missing_identity_is_not_recreated(self):
        plan,path = self.interrupt(after=True)
        book = self.books['y26']
        book.rows['FC FEV'][4] = copy.deepcopy(book.rows['FC FEV'][3])
        book.rows['FC FEV'][4][6]['userEnteredValue']['formulaValue'] = balance_formula(4)
        with self.assertRaisesRegex(RuntimeError,'Conflito'):
            apply(plan,self.directory,self.factory,path)
        self.assertEqual(book.calls,1)

    def expand(self):
        book = self.books['y26']
        template = copy.deepcopy(book.rows['FC FEV'][96])
        book.sheets['FC FEV']['properties']['gridProperties']['rowCount'] = 350
        for row in range(97,301):
            book.rows['FC FEV'][row] = copy.deepcopy(template)
            book.rows['FC FEV'][row][6]['userEnteredValue']['formulaValue'] = balance_formula(row)
        book.rows['FC FEV'][301] = [{} for _ in range(9)]
        book.rows['FC FEV'][301][5] = {'userEnteredValue':{'formulaValue':'=SUM(F3:F300)'}}
        book.rows['FC FEV'][301][6] = {'userEnteredValue':{'formulaValue':total_formula(301)}}
        for row in range(3,300):
            book.rows['FC FEV'][row][1]['userEnteredValue'] = {'stringValue':'Manual'}
        return book

    def test_total_beyond_110_and_last_input_row(self):
        book = self.expand()
        self.register(buy(parcelas=1))
        self.assertEqual(values(book.cells('FC FEV',300,300,0,9)[0])[7],'pedido1:cera:001')
        self.assertEqual(values(book.cells('FC FEV',301,301)[0])[5],'=SUM(F3:F300)')

    def test_expansion_requires_validation_balance_and_report_coverage(self):
        book = self.expand()
        book.rows['FC FEV'][299][2].pop('dataValidation')
        with self.assertRaisesRegex(ValueError,'validação'):
            self.planner().register(buy(parcelas=1))
        book.rows['FC FEV'][299][2] = copy.deepcopy(book.rows['FC FEV'][298][2])
        book.rows['Anal Fin - consolidado'][90] = [{'userEnteredValue':{'formulaValue':"=SUM('FC FEV'!F3:F96)"}}]
        with self.assertRaisesRegex(ValueError,'referência'):
            self.planner().register(buy(parcelas=1))
        self.assertEqual(book.calls,0)

    def test_money_remains_in_integer_cents_until_adapter(self):
        raw = buy()
        raw['produtos'][0]['valor_total'] = '100.00'
        records = purchase_records(raw)
        self.assertEqual([r.previsto_centavos for r in records],[3334,3333,3333])
        self.assertEqual(sum(r.previsto_centavos for r in records),10000)
        for r in records:
            self.assertEqual(Parcela.from_sheet(r.to_sheet()),r)
        broken = purchase(raw)[0]
        broken[10] = -33.333
        with self.assertRaisesRegex(ValueError,'frações de centavo'):
            Parcela.from_sheet(broken)
