"""Transformação dos pedidos em linhas de compra com valores em centavos."""
from collections import defaultdict
from decimal import Decimal, ROUND_HALF_UP
import hashlib
import json

from .client import APIError


def cents(value):
    number = Decimal(str(value))
    if not number.is_finite() or number < 0:
        raise ValueError('Valor monetário inválido na API.')
    return int((number * 100).quantize(Decimal('1'), rounding=ROUND_HALF_UP))


def split_freight(total, keys):
    """Rateio por linha, com resto de centavos em ordem determinística."""
    if total < 0 or not keys or len(set(keys)) != len(keys):
        raise ValueError('Rateio inválido.')
    base, remainder = divmod(total, len(keys))
    return {key: base + (index < remainder) for index, key in enumerate(sorted(keys))}


def optional_get(client, path, **kwargs):
    try:
        return client.get(path, **kwargs)
    except APIError as error:
        if error.status in (403, 404):
            return None
        raise


def collect(client):
    buyer = str(client.get('/users/me')['id'])
    orders = {str(o['id']): o for o in client.orders(buyer)}
    pending = list(orders)
    shipments, order_shipments, categories, products = {}, {}, {}, {}
    # Consulta detalhes e completa pedidos que compartilham a mesma entrega,
    # mesmo quando um deles está fora do período solicitado.
    while pending:
        oid = pending.pop(0)
        order = client.get('/orders/' + oid)
        if str(order['buyer']['id']) != buyer:
            raise ValueError('A API retornou um pedido de outra conta.')
        if order['currency_id'] != 'BRL':
            raise ValueError('Esta versão aceita apenas compras em BRL; não mistura moedas.')
        orders[oid] = order
        shipping = order.get('shipping') or {}
        if shipping.get('id'):
            ids = [str(shipping['id'])]
        else:
            links = optional_get(client, f'/orders/{oid}/shipments', headers={'X-New-Domain': 'true'})
            ids = sorted({str(s['id']) for s in (links or []) if s.get('type') == 'forward'})
        order_shipments[oid] = ids
        for sid in ids:
            if sid in shipments:
                continue
            members = optional_get(client, f'/shipments/{sid}/items', headers={'x-format-new': 'true'})
            costs = optional_get(client, f'/shipments/{sid}/costs')
            receiver = (costs or {}).get('receiver') or {}
            if receiver.get('user_id') is not None and str(receiver['user_id']) != buyer:
                raise ValueError('O custo do frete pertence a outro comprador.')
            shipments[sid] = {'members': members, 'cost': receiver.get('cost')}
            for member in members or []:
                related = str(member.get('order_id') or '')
                if related and related not in orders:
                    orders[related] = None
                    pending.append(related)

    rows = []
    for oid, order in sorted(orders.items()):
        grouped = {}
        for entry in order['order_items']:
            item = entry['item']
            quantity = entry['quantity']
            if isinstance(quantity, bool) or int(quantity) != quantity or quantity <= 0:
                raise ValueError(f'Quantidade inválida no pedido {oid}.')
            price = cents(entry['unit_price'])
            identity = [oid, str(item['id']), str(item.get('variation_id') or ''), price]
            key = hashlib.sha256(json.dumps(identity).encode()).hexdigest()[:24]
            if key in grouped:
                grouped[key]['quantity'] += int(quantity)
                grouped[key]['subtotal'] += price * int(quantity)
                continue
            category_id = item.get('category_id')
            if not category_id:
                if item['id'] not in products:
                    products[item['id']] = optional_get(client, '/items/' + item['id']) or {}
                category_id = products[item['id']].get('category_id')
            if category_id and category_id not in categories:
                categories[category_id] = (optional_get(client, '/categories/' + category_id) or {}).get('name')
            name = categories.get(category_id)
            grouped[key] = {
                'key': key, 'order_id': oid, 'date': order['date_created'],
                'product': item['title'], 'item_id': str(item['id']),
                'variation_id': str(item.get('variation_id') or ''),
                'category': name or category_id or 'Não disponível',
                'category_id': category_id or '', 'quantity': int(quantity),
                'unit_price': price, 'subtotal': price * int(quantity),
                'freight': None, 'total': None, 'status': order['status'],
                'shipment_ids': order_shipments[oid],
                'warnings': [] if name else ['Categoria sem nome disponível na API'],
            }
        rows.extend(grouped.values())

    by_shipment = defaultdict(list)
    for row in rows:
        if len(row['shipment_ids']) == 1:
            by_shipment[row['shipment_ids'][0]].append(row)
        else:
            row['warnings'].append('Frete pendente: nenhum envio ou múltiplos envios')
    for sid, group in by_shipment.items():
        shipment = shipments[sid]
        # Confere produtos e quantidades, evitando cobrar o frete inteiro
        # quando só uma parte do carrinho está visível para o aplicativo.
        expected, actual = defaultdict(int), defaultdict(int)
        for member in shipment['members'] or []:
            expected[(str(member.get('order_id')), str(member.get('item_id')),
                      str(member.get('variation_id') or ''))] += int(member['quantity'])
        for row in group:
            actual[(row['order_id'], row['item_id'], row['variation_id'])] += row['quantity']
        if shipment['cost'] is None or not expected or expected != actual:
            for row in group:
                row['warnings'].append('Frete pendente: custo ou composição completa do envio indisponível')
            continue
        shares = split_freight(cents(shipment['cost']), [r['key'] for r in group])
        for row in group:
            row['freight'] = shares[row['key']]
            row['total'] = row['subtotal'] + row['freight']
    return buyer, list(orders), rows

