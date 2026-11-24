"""Leitura e rateio monetário sem acesso à rede."""
from datetime import datetime
from decimal import Decimal, InvalidOperation
from pathlib import Path
import re
import xml.etree.ElementTree as ET

from velas_sheets.installments import iso, purchase

NS = {'n': 'http://www.portalfiscal.inf.br/nfe'}


def field(node, path, default=None):
    value = node.findtext('/'.join('n:' + part for part in path.split('/')), namespaces=NS)
    if value is None or not value.strip():
        if default is not None:
            return default
        raise ValueError(f'XML: campo obrigatório ausente: {path}.')
    return value.strip()


def decimal(value, name):
    if not re.fullmatch(r'\d+(?:\.\d+)?', value):
        raise ValueError(f'XML: número inválido em {name}.')
    try:
        return Decimal(value)
    except InvalidOperation as error:
        raise ValueError(f'XML: número inválido em {name}.') from error


def cents(node, path, default=None):
    value = decimal(field(node, path, default), path) * 100
    if value != value.to_integral_value() or value >= 100_000_000_000_000:
        raise ValueError(f'XML: valor fora dos limites ou fração de centavo em {path}.')
    return int(value)


def amount(value):
    return f'{value // 100}.{value % 100:02d}'


def allocate(total, balances):
    """Maiores restos sobre os saldos; desempate pela ordem numérica de nItem."""
    remaining = sum(balances)
    if not 0 < total <= remaining:
        raise ValueError('XML: valor de duplicata incompatível com o saldo da nota.')
    parts = [total * balance // remaining for balance in balances]
    order = sorted(range(len(balances)), key=lambda i: (-(total * balances[i] % remaining), i))
    for i in order[:total - sum(parts)]:
        parts[i] += 1
    return parts


def convert(path, centro_custo, parcelas=None, primeiro_vencimento=None):
    data = Path(path).read_bytes()
    # Rejeita DTD/entidades antes do parser, inclusive XML codificado em UTF-16.
    if b'<!DOCTYPE' in data.replace(b'\x00', b'').upper() or b'<!ENTITY' in data.replace(b'\x00', b'').upper():
        raise ValueError('XML: DTD e entidades não são suportadas.')
    try:
        root = ET.fromstring(data)
    except ET.ParseError as error:
        raise ValueError(f'XML inválido: {error}') from error
    notes = root.findall('.//n:infNFe', NS)
    if len(notes) != 1:
        raise ValueError('XML deve conter exatamente uma NF-e.')
    note = notes[0]
    key = note.get('Id', '')
    if not re.fullmatch(r'NFe\d{44}', key):
        raise ValueError('XML: chave da NF-e inválida.')
    if field(note, 'ide/mod') != '55' or field(note, 'ide/finNFe') != '1':
        raise ValueError('Somente NF-e modelo 55 normal é suportada.')
    emitted = field(note, 'ide/dhEmi')
    try:
        datetime.fromisoformat(emitted)
        date = iso(emitted[:10]).isoformat()
    except ValueError as error:
        raise ValueError('XML: data de emissão inválida.') from error
    number = field(note, 'ide/nNF')
    supplier = field(note, 'emit/xNome')
    if not isinstance(centro_custo, str) or not centro_custo.strip():
        raise ValueError('Centro de custo não pode ficar vazio.')
    total_node = note.find('n:total/n:ICMSTot', NS)
    if total_node is None:
        raise ValueError('XML: total ICMSTot ausente.')
    # Não presume tratamento de componentes tributários fora dos exemplos.
    for name in ('vII', 'vIPI', 'vIPIDevol', 'vST', 'vFCPST', 'vICMSDeson'):
        if cents(total_node, name, '0'):
            raise ValueError(f'XML: componente adicional não suportado: {name}.')
    total = cents(total_node, 'vNF')
    products, seen, totals = [], set(), {name: 0 for name in ('vProd', 'vDesc', 'vFrete', 'vSeg', 'vOutro')}
    for item in note.findall('n:det', NS):
        raw_id = item.get('nItem', '')
        if not re.fullmatch(r'[1-9]\d{0,5}', raw_id) or int(raw_id) in seen:
            raise ValueError('XML: nItem inválido ou repetido.')
        index = int(raw_id)
        seen.add(index)
        if field(item, 'prod/indTot') != '1':
            raise ValueError(f'XML: item {index} fora do total não suportado.')
        quantity = decimal(field(item, 'prod/qCom'), 'qCom')
        if quantity < 1 or quantity != quantity.to_integral_value():
            raise ValueError(f'XML: item {index} tem quantidade fracionária ou não positiva; a CLI exige inteiro positivo.')
        values = {name: cents(item, 'prod/' + name, None if name == 'vProd' else '0') for name in totals}
        for name, value in values.items():
            totals[name] += value
        value = values['vProd'] - values['vDesc'] + values['vFrete'] + values['vSeg'] + values['vOutro']
        if value <= 0:
            raise ValueError(f'XML: item {index} tem valor líquido não positivo.')
        if item.find('n:vItem', NS) is not None and cents(item, 'vItem') != value:
            raise ValueError(f'XML: vItem divergente no item {index}.')
        products.append((index, {'id': f'item-{index}', 'descricao': field(item, 'prod/xProd'),
                                 'quantidade': int(quantity), 'centro_custo': centro_custo,
                                 'valor_total': amount(value)}, value))
    products.sort(key=lambda product: product[0])
    if not 1 <= len(products) <= 100:
        raise ValueError('XML: informe entre 1 e 100 itens.')
    for name, value in totals.items():
        if cents(total_node, name, '0') != value:
            raise ValueError(f'XML: soma dos itens diverge do total {name}.')
    if sum(p[2] for p in products) != total:
        raise ValueError('XML: soma dos itens diverge de vNF.')
    if note.find('n:total/n:vNFTot', NS) is not None and cents(note, 'total/vNFTot') != total:
        raise ValueError('XML: vNFTot diferente de vNF não suportado.')
    duplicates = note.findall('n:cobr/n:dup', NS)
    purchases = []
    base = {'data': date, 'parcelas': 1}
    if duplicates:
        if parcelas is not None or primeiro_vencimento is not None:
            raise ValueError('Não use --parcelas ou --primeiro-vencimento em nota com duplicatas.')
        schedule, numbers = [], set()
        for duplicate in duplicates:
            num = field(duplicate, 'nDup')
            if num in numbers:
                raise ValueError('XML: número de duplicata repetido.')
            numbers.add(num)
            due = iso(field(duplicate, 'dVenc')).isoformat()
            value = cents(duplicate, 'vDup')
            if value <= 0 or due < date:
                raise ValueError('XML: valor ou vencimento inválido na duplicata.')
            schedule.append((due, num, value))
        schedule.sort()
        if sum(value for _, _, value in schedule) != total:
            raise ValueError('XML: soma das duplicatas diverge de vNF.')
        balances = [p[2] for p in products]
        for index, (due, _, value) in enumerate(schedule, 1):
            parts = allocate(value, balances)
            balances = [balance - part for balance, part in zip(balances, parts)]
            purchases.append({**base, 'id': f'{key}-dup-{index:03}', 'primeiro_vencimento': due,
                              'produtos': [{**product[1], 'valor_total': amount(part)}
                                           for product, part in zip(products, parts) if part]})
    else:
        purchases.append({**base, 'id': key, 'parcelas': parcelas if parcelas is not None else 1,
                          'primeiro_vencimento': primeiro_vencimento or date,
                          'produtos': [p[1] for p in products]})
    for raw in purchases:
        purchase(raw)
    return {'chave': key, 'numero': number, 'fornecedor': supplier, 'total': amount(total), 'compras': purchases}
