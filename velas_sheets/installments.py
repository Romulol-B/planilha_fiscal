"""Regras de compras e parcelas, independentes da API."""
import calendar
from dataclasses import dataclass
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


@dataclass(frozen=True)
class Parcela:
    id: str
    compra: str
    produto: str
    descricao: str
    quantidade: int
    data_compra: int
    numero: int
    total_parcelas: int
    centro_custo: str
    vencimento: int
    previsto_centavos: int
    total_produto_centavos: int
    data_pagamento: int | None
    pago_centavos: int | None
    hash_compra: str

    def to_sheet(self):
        """Adaptador das 16 colunas: compras são saídas negativas em reais."""
        return [self.id, self.compra, self.produto, self.descricao, self.quantidade,
                self.data_compra, self.numero, self.total_parcelas, self.centro_custo,
                self.vencimento, -self.previsto_centavos / 100, -self.total_produto_centavos / 100,
                self.data_pagamento, -self.pago_centavos / 100 if self.pago_centavos is not None else None,
                None, self.hash_compra]

    @classmethod
    def from_sheet(cls, row):
        def cents(value):
            if type(value) not in (int, float) or not -1_000_000_000_000 < value < 0:
                raise ValueError('Parcelas: saída monetária inválida.')
            amount = -Decimal(str(value)) * 100
            if amount != amount.to_integral_value():
                raise ValueError('Parcelas: valor com frações de centavo.')
            return int(amount)
        if len(row) != 16 or any(not isinstance(row[c], str) or not row[c].strip() for c in (0,1,2,3,8,15)):
            raise ValueError('Parcelas: identificação ou descrição inválida.')
        if any(type(row[c]) not in (int, float) or int(row[c]) != row[c] for c in (4,5,6,7,9)):
            raise ValueError('Parcelas: quantidade, calendário ou datas inválidos.')
        if row[4] < 1 or not 1 <= row[6] <= row[7] <= 120 or row[9] < row[5]:
            raise ValueError('Parcelas: quantidade ou calendário inválido.')
        identifier(row[1]); identifier(row[2])
        if row[0] != f'{row[1]}:{row[2]}:{int(row[6]):03}':
            raise ValueError('Parcelas: ID incompatível com a compra/produto/parcela.')
        if (row[12] is None) != (row[13] is None):
            raise ValueError('Parcelas: pagamento incompleto.')
        if row[12] is not None and (type(row[12]) not in (int, float) or int(row[12]) != row[12] or row[12] < row[5]):
            raise ValueError('Parcelas: data de pagamento inválida.')
        return cls(*row[:10], cents(row[10]), cents(row[11]), row[12],
                   cents(row[13]) if row[13] is not None else None, row[15])


def purchase(raw):
    """Compatibilidade do adaptador tabular; regras usam purchase_records."""
    return [record.to_sheet() for record in purchase_records(raw)]


def purchase_records(raw):
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
            result.append(Parcela(f'{key}:{p["id"]}:{n+1:03}', key, p['id'], p['descricao'], p['quantidade'],
                           serial(raw['data']), n + 1, count, p['centro_custo'], (due - EPOCH).days,
                           base + (n < extra), p['valor_total'], None, None, digest))
    return result


def status(row, today=None):
    row = row.to_sheet() if isinstance(row, Parcela) else row
    if row[12] is not None:
        return 'paga'
    return 'atrasada' if date_text(row[9]) < (today or date.today()).isoformat() else 'pendente'


def values(cells):
    return [next(iter(c.get('userEnteredValue', {}).values()), None) for c in cells]


def cell(value):
    if value is None:
        return {}
    return {'userEnteredValue': {'stringValue' if isinstance(value, str) else 'numberValue': value}}
