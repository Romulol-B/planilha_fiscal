from contextlib import redirect_stdout, redirect_stderr
import io
import json
from pathlib import Path
import shlex
import tempfile
import unittest
from unittest.mock import patch
import xml.etree.ElementTree as ET

from velas_notas_fiscais.converter import NS, allocate, convert
from velas_notas_fiscais.__main__ import main, generate
from velas_sheets.installments import money, purchase

EXAMPLES = Path(__file__).resolve().parents[1] / 'exemplos'


class ConversionTests(unittest.TestCase):
    def test_discount_and_default_due_date(self):
        result = convert(EXAMPLES / 'nota_xml.xml', 'Conta  a pagar')
        raw, = result['compras']
        self.assertEqual(result['total'], '14.00')
        self.assertEqual(raw['primeiro_vencimento'], '2026-11-20')
        self.assertEqual(raw['produtos'][0]['valor_total'], '14.00')
        self.assertEqual(len(purchase(raw)), 1)

    def test_exact_installments_and_product_totals(self):
        result = convert(EXAMPLES / 'nota_2_xml.xml', 'Conta  a pagar')
        raws = result['compras']
        self.assertEqual([r['primeiro_vencimento'] for r in raws],
                         ['2026-12-10', '2027-01-10', '2027-02-10'])
        self.assertEqual([sum(money(p['valor_total']) for p in r['produtos']) for r in raws], [4200, 4150, 4151])
        totals = {}
        for raw in raws:
            self.assertEqual(len(purchase(raw)), 3)
            for p in raw['produtos']:
                totals[p['id']] = totals.get(p['id'], 0) + money(p['valor_total'])
        self.assertEqual(list(totals.values()), [6750, 3900, 1851])
        self.assertEqual(sum(totals.values()), 12501)
        self.assertEqual(len({raw['id'] for raw in raws}), 3)

    def test_override_calendar(self):
        raw, = convert(EXAMPLES / 'nota_xml.xml', 'Conta  a pagar', 3, '2026-12-31')['compras']
        self.assertEqual(raw['parcelas'], 3)
        self.assertEqual(len(purchase(raw)), 3)
        with self.assertRaisesRegex(ValueError, 'duplicatas'):
            convert(EXAMPLES / 'nota_2_xml.xml', 'Conta  a pagar', 3)
        for count in (0, 121):
            with self.assertRaises(ValueError):
                convert(EXAMPLES / 'nota_xml.xml', 'Conta  a pagar', count)

    def test_allocation_conserves_balances(self):
        self.assertEqual(allocate(2, [1, 1, 1]), [1, 1, 0])
        for balances in ([1, 1, 1], [3, 200, 41], [0, 2, 7]):
            for total in range(1, sum(balances) + 1):
                parts = allocate(total, balances)
                self.assertEqual(sum(parts), total)
                self.assertTrue(all(0 <= part <= balance for part, balance in zip(parts, balances)))

    def test_invalid_xml_and_entities(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / 'bad.xml'
            for data in (b'<broken', b'<!DOCTYPE x><x/>', '<!DOCTYPE x><x/>'.encode('utf-16')):
                path.write_bytes(data)
                with self.assertRaises(ValueError):
                    convert(path, 'Conta  a pagar')

    def test_invalid_fields(self):
        mutations = [
            ('.//n:qCom', '1.5'), ('.//n:xProd', ''), ('.//n:vItem', '15.00'),
            ('.//n:ICMSTot/n:vNF', '13.00'), ('.//n:ICMSTot/n:vIPI', '1.00'),
            ('.//n:ICMSTot/n:vFrete', '1.00'), ('.//n:dhEmi', 'invalid'),
        ]
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / 'bad.xml'
            for xpath, value in mutations:
                with self.subTest(xpath=xpath):
                    root = ET.parse(EXAMPLES / 'nota_xml.xml')
                    root.find(xpath, NS).text = value
                    root.write(path, encoding='utf-8')
                    with self.assertRaises(ValueError):
                        convert(path, 'Conta  a pagar')
            root = ET.parse(EXAMPLES / 'nota_2_xml.xml')
            root.findall('.//n:det', NS)[1].set('nItem', '1')
            root.write(path, encoding='utf-8')
            with self.assertRaisesRegex(ValueError, 'repetido'):
                convert(path, 'Conta  a pagar')
            for xpath, value in [('.//n:vDup', '1.00'), ('.//n:dVenc', '2020-01-01')]:
                root = ET.parse(EXAMPLES / 'nota_2_xml.xml')
                root.find(xpath, NS).text = value
                root.write(path, encoding='utf-8')
                with self.assertRaises(ValueError):
                    convert(path, 'Conta  a pagar')

    def test_output_commands_reconversion_and_conflict(self):
        result = convert(EXAMPLES / 'nota_xml.xml', 'Conta  a pagar')
        with tempfile.TemporaryDirectory(prefix='nf space ') as directory:
            output = Path(directory)
            config = output / "config ' $(test).toml"
            commands = generate(result, output, config)
            command = shlex.split(commands.splitlines()[1])
            self.assertEqual(command[:4], ['python', '-m', 'velas_sheets', '--config'])
            self.assertEqual(command[4], str(config))
            self.assertEqual(command[5], 'compra')
            self.assertEqual(json.loads(Path(command[6]).read_text()), result['compras'][0])
            before = {p.name: p.stat().st_mtime_ns for p in output.iterdir()}
            self.assertEqual(generate(result, output, config), commands)
            self.assertEqual(before, {p.name: p.stat().st_mtime_ns for p in output.iterdir()})
            result['compras'][0]['produtos'][0]['centro_custo'] = 'outro'
            with self.assertRaisesRegex(ValueError, 'conteúdo diferente'):
                generate(result, output, config)
            self.assertEqual(before, {p.name: p.stat().st_mtime_ns for p in output.iterdir()})

    def test_cli_offline_and_failure_without_outputs(self):
        with tempfile.TemporaryDirectory() as directory, redirect_stdout(io.StringIO()), redirect_stderr(io.StringIO()):
            output = Path(directory) / 'output'
            args = [str(EXAMPLES / 'nota_2_xml.xml'), '--saida', str(output), '--centro-custo', 'Conta  a pagar']
            with patch('socket.socket', side_effect=AssertionError('network')), patch('subprocess.Popen', side_effect=AssertionError('process')):
                self.assertEqual(main(args + ['--parcelas', '2']), 1)
                self.assertFalse(output.exists())
                self.assertEqual(main(args), 0)
                self.assertEqual(len(list(output.glob('*.json'))), 3)


if __name__ == '__main__':
    unittest.main()
