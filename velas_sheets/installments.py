"""Regras de compras e parcelas, independentes da API."""
import calendar
from datetime import date, timedelta
from decimal import Decimal
import hashlib
import json
import re

MONTHS = 'JAN FEV MAR ABR MAI JUN JUL AGO SET OUT NOV DEZ'.split()
LEDGER = 'Parcelas'
HEADERS = ['ID parcela', 'Compra', 'Produto', 'Descrição', 'Quantidade', 'Data compra',
           'Parcela', 'Total parcelas', 'Centro de custo', 'Vencimento', 'Previsto R$',
           'Total produto R$', 'Data pagamento', 'Pago R$', 'Status', 'Hash compra']
EPOCH = date(1899, 12, 30)


def iso(value):
    if not isinstance(value, str) or not re.fullmatch(r'\d{4}-\d{2}-\d{2}', value):
        raise ValueError('Use datas AAAA-MM-DD.')
    return date.fromisoformat(value)


def serial(value):
    return (iso(value) - EPOCH).days


def date_text(value):
    return (EPOCH + timedelta(days=int(value))).isoformat()


def money(value):
    if isinstance(value, bool) or not re.fullmatch(r'\d+(?:[.,]\d{1,2})?', str(value)):
        raise ValueError('Informe valores positivos, com até dois decimais e sem separador de milhar.')
    cents = int(Decimal(str(value).replace(',', '.')) * 100)
    if not 0 < cents < 100_000_000_000_000:
        raise ValueError('Valor fora dos limites.')
    return cents


def identifier(value):
    if not isinstance(value, str) or not re.fullmatch(r'[A-Za-z0-9_-]{1,64}', value):
        raise ValueError('IDs devem ter de 1 a 64 letras ASCII, números, _ ou -.')
    return value


def purchase(raw):
    if not isinstance(raw, dict) or set(raw) != {'id', 'data', 'parcelas', 'primeiro_vencimento', 'produtos'}:
        raise ValueError('Compra: informe id, data, parcelas, primeiro_vencimento e produtos.')
    key = identifier(raw['id'])
    bought, first = iso(raw['data']), iso(raw['primeiro_vencimento'])
    count = raw['parcelas']
    if type(count) is not int or not 1 <= count <= 120 or first < bought:
        raise ValueError('Use 1 a 120 parcelas e primeiro vencimento a partir da compra.')
    if not isinstance(raw['produtos'], list) or not 1 <= len(raw['produtos']) <= 100:
        raise ValueError('Informe entre 1 e 100 produtos.')
    products, seen = [], set()
    for p in raw['produtos']:
        if not isinstance(p, dict) or set(p) != {'id', 'descricao', 'quantidade', 'centro_custo', 'valor_total'}:
            raise ValueError('Produto: informe id, descricao, quantidade, centro_custo e valor_total.')
        pid = identifier(p['id'])
        if pid in seen:
            raise ValueError(f'Produto repetido: {pid}')
        seen.add(pid)
        if type(p['quantidade']) is not int or p['quantidade'] < 1:
            raise ValueError('Quantidade deve ser um inteiro positivo.')
        for field in ('descricao', 'centro_custo'):
            if not isinstance(p[field], str) or not p[field].strip():
                raise ValueError(f'{field} não pode ficar vazio.')
        cents = money(p['valor_total'])
        if cents < count:
            raise ValueError('O valor não permite parcelas de pelo menos um centavo.')
        products.append({**p, 'valor_total': cents})
    products.sort(key=lambda p: p['id'])
    normalized = {**raw, 'produtos': products}
    digest = hashlib.sha256(json.dumps(normalized, sort_keys=True, ensure_ascii=False).encode()).hexdigest()
    result = []
    for p in products:
        base, extra = divmod(p['valor_total'], count)
        for n in range(count):
            year, month0 = divmod(first.year * 12 + first.month - 1 + n, 12)
            due = date(year, month0 + 1, min(first.day, calendar.monthrange(year, month0 + 1)[1]))
            result.append([f'{key}:{p["id"]}:{n+1:03}', key, p['id'], p['descricao'], p['quantidade'],
                           serial(raw['data']), n + 1, count, p['centro_custo'], (due - EPOCH).days,
                           -(base + (n < extra)) / 100, -p['valor_total'] / 100, None, None, None, digest])
    return result


def status(row, today=None):
    if row[12] is not None:
        return 'paga'
    return 'atrasada' if date_text(row[9]) < (today or date.today()).isoformat() else 'pendente'


def values(cells):
    return [next(iter(c.get('userEnteredValue', {}).values()), None) for c in cells]


def cell(value):
    if value is None:
        return {}
    return {'userEnteredValue': {'stringValue' if isinstance(value, str) else 'numberValue': value}}
