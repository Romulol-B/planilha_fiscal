"""Planejamento de gravações e projeções mensais das parcelas."""
import copy
from datetime import date
import secrets
import re

from .installments import (HEADERS, LEDGER, MONTHS, cell, date_text, iso, purchase,
                           serial, status, values)
from .model import HEADERS as FC_HEADERS, find_sheet, validation_options

TECH_HEADERS = ['velas_parcela_id', 'velas_tipo']


def stable(rows):
    return [[{k: v for k, v in c.items() if k != 'formattedValue' and v != {}}
             for c in row] for row in rows]


def update(sheet_id, row, col, cells):
    return {'updateCells': {'range': {'sheetId': sheet_id, 'startRowIndex': row - 1,
        'endRowIndex': row, 'startColumnIndex': col, 'endColumnIndex': col + len(cells)},
        'rows': [{'values': cells}], 'fields': 'userEnteredValue'}}


class Planner:
    def __init__(self, config, factory):
        self.config, self.factory = config, factory
        self.clients, self.metas, self.steps, self.blocks = {}, {}, {}, {}
        self.master = config.get('principal_id', config['planilha_id'])
        self.years = {int(y): s for y, s in config.get('anos', {}).items()}
        if len(set(self.years.values())) != len(self.years):
            raise ValueError('Cada ano deve apontar para um arquivo diferente.')
        self.summary = []
        self.month_blocks = {}
        self.options = {}

    def client(self, book):
        if book not in self.clients:
            self.clients[book] = self.factory(book)
            self.metas[book] = self.clients[book].metadata()
        return self.clients[book]

    def step(self, book):
        self.client(book)
        return self.steps.setdefault(book, {'book': book, 'requests': [], 'blocks': [], 'sheets': {}})

    def block(self, book, title, first, last, width, sheet=None, new=False):
        key = (book, title, first, last, width)
        if key not in self.blocks:
            step = self.step(book)
            sheet = sheet or find_sheet(self.metas[book], title)
            props = sheet['properties']
            if props['gridProperties']['columnCount'] < width or props['gridProperties']['rowCount'] < last:
                raise ValueError(f'{title}: dimensões insuficientes.')
            rows = [[{} for _ in range(width)] for _ in range(last-first+1)] if new else self.client(book).cells(title, first, last, 0, width)
            block = {'title': title, 'first': first, 'last': last, 'width': width,
                     'before': None if new else stable(rows), 'after': stable(rows), 'sheet_id': props['sheetId']}
            self.blocks[key] = block
            step['blocks'].append(block)
            step['sheets'][title] = None if new else props
        return self.blocks[key]

    def put(self, book, block, row, col, value, formula=False):
        idx = row - block['first']
        before = block['after'][idx][col]
        if before.get('chipRuns'):
            raise ValueError(f'{block["title"]}, linha {row}: chip na célula de destino.')
        entered = {'formulaValue': value} if formula else cell(value).get('userEnteredValue')
        if before.get('userEnteredValue') == entered:
            return
        if entered is None:
            before.pop('userEnteredValue', None)
        else:
            before['userEnteredValue'] = entered
        self.step(book)['requests'].append(update(block['sheet_id'], row, col, [cell(value) if not formula else {'userEnteredValue': entered}]))

    def ledger(self, create=False):
        self.client(self.master)
        sheet = next((s for s in self.metas[self.master]['sheets'] if s['properties']['title'] == LEDGER), None)
        new = sheet is None
        if new and not create:
            raise ValueError('A aba Parcelas ainda não existe. Cadastre uma compra primeiro.')
        if new:
            used = {s['properties']['sheetId'] for s in self.metas[self.master]['sheets']}
            sid = secrets.randbelow(2**31-1)
            while sid in used:
                sid = secrets.randbelow(2**31-1)
            sheet = {'properties': {'sheetId': sid, 'title': LEDGER,
                                    'gridProperties': {'rowCount': 2000, 'columnCount': 16}}}
            self.step(self.master)['requests'].append({'addSheet': {'properties': {
                **sheet['properties'], 'gridProperties': {**sheet['properties']['gridProperties'], 'frozenRowCount': 1}}}})
        limit = sheet['properties']['gridProperties']['rowCount']
        if limit > 10000:
            raise ValueError('Parcelas: limite de leitura desta versão é de 10000 linhas.')
        block = self.block(self.master, LEDGER, 1, limit, 16, sheet, new)
        if new:
            for col, header in enumerate(HEADERS):
                self.put(self.master, block, 1, col, header)
        elif values(block['after'][0]) != HEADERS:
            raise ValueError('Cabeçalho da aba Parcelas diferente do esperado.')
        records = {}
        for row, cells in enumerate(block['after'][1:], 2):
            vals = values(cells)
            if all(v is None for v in vals):
                continue
            if not isinstance(vals[0], str) or vals[0] in records:
                raise ValueError(f'Parcelas: identificador ausente ou duplicado na linha {row}.')
            if any(vals[c] is None for c in range(12)) or not isinstance(vals[15], str):
                raise ValueError(f'Parcelas: registro incompleto na linha {row}.')
            if (vals[12] is None) != (vals[13] is None):
                raise ValueError(f'Parcelas: pagamento incompleto na linha {row}.')
            records[vals[0]] = (row, vals)
        return block, records

    def destination(self, day):
        dt = iso(date_text(day))
        if dt.year not in self.years:
            raise ValueError(f'Falta configurar a planilha do ano {dt.year}. Nenhuma gravação iniciada.')
        return self.years[dt.year], 'FC ' + MONTHS[dt.month-1]

    def projections(self, record):
        due = self.destination(record[9])
        desc = f'{record[3]} — parcela {record[6]}/{record[7]}'
        planned = [record[5], desc, record[8], record[9], record[10], None]
        result = {(due, 'previsto'): planned}
        if record[12] is not None:
            paid = self.destination(record[12])
            if paid == due:
                planned[0], planned[5] = record[12], record[13]
            else:
                result[(paid, 'pagamento')] = [record[12], desc, record[8], record[9], None, record[13]]
        return result

    def month(self, book, title):
        key = (book, title)
        if key in self.month_blocks:
            return self.month_blocks[key]
        self.client(book)
        sheet = find_sheet(self.metas[book], title)
        end = min(110, sheet['properties']['gridProperties']['rowCount'])
        probe = self.client(book).cells(title, 2, end, 0, 9)
        totals = []
        for row, cells in enumerate(probe, 2):
            formula = cells[5].get('userEnteredValue', {}).get('formulaValue', '').replace('$', '').replace(' ', '').upper()
            match = re.fullmatch(r'=SUM\(F3:F(\d+)\)', formula)
            if match and int(match[1]) == row-1:
                totals.append(row)
        if len(totals) != 1:
            raise ValueError(f'{title}: total de pagamentos não identificado com segurança.')
        block = self.block(book, title, 2, totals[0], 9)
        self.month_blocks[key] = block
        if values(block['after'][0][:7]) != FC_HEADERS:
            raise ValueError(f'{title}: cabeçalhos diferentes do modelo.')
        tech = values(block['after'][0][7:9])
        if tech != TECH_HEADERS:
            if any(v is not None for v in tech):
                raise ValueError(f'{title}: H:I já estão ocupadas.')
            # Verifica toda a área antes de reservar e ocultar colunas.
            end = sheet['properties']['gridProperties']['rowCount']
            for start in range(1, end+1, 250):
                rows = self.client(book).cells(title, start, min(start+249, end), 7, 9)
                if any(c.get('userEnteredValue') or c.get('chipRuns') or c.get('note') or c.get('dataValidation') for r in rows for c in r):
                    raise ValueError(f'{title}: H:I têm dados ou regras; escolha outro modelo.')
            for col, value in enumerate(TECH_HEADERS, 7):
                self.put(book, block, 2, col, value)
            self.step(book)['requests'].append({'updateDimensionProperties': {
                'range': {'sheetId': block['sheet_id'], 'dimension': 'COLUMNS', 'startIndex': 7, 'endIndex': 9},
                'properties': {'hiddenByUser': True}, 'fields': 'hiddenByUser'}})
        return block

    def project(self, old, record):
        previous = self.projections(old) if old else {}
        desired = self.projections(record)
        for (destination, kind), vals in desired.items():
            book, title = destination
            block = self.month(book, title)
            candidates = [(i+2, cells) for i, cells in enumerate(block['after'][1:-1], 1)
                          if values(cells[7:9]) == [record[0], kind]]
            if len(candidates) > 1:
                raise ValueError(f'{title}: parcela duplicada {record[0]}.')
            prior = previous.get((destination, kind))
            if candidates:
                row, cells = candidates[0]
                if prior is None or values(cells[:6]) != prior:
                    raise ValueError(f'{title}: conflito com edição existente de {record[0]}.')
            else:
                if prior is not None:
                    raise ValueError(f'{title}: lançamento de {record[0]} desapareceu. Corrija antes de continuar.')
                available = [(i+2, c) for i, c in enumerate(block['after'][1:-1], 1)
                             if not any(c[j].get('userEnteredValue') or c[j].get('note') or c[j].get('chipRuns') for j in (0,1,2,3,4,5,7,8))]
                if not available:
                    raise ValueError(f'{title}: sem espaço nas linhas 3–{block["last"]-1}.')
                row, cells = available[0]
            sheet = find_sheet(self.metas[book], title)
            for merged in sheet.get('merges', []):
                if merged.get('startRowIndex', 0) <= row-1 < merged.get('endRowIndex', 1000) and merged.get('startColumnIndex', 0) < 9:
                    raise ValueError(f'{title}, linha {row}: mesclagem no lançamento.')
            if 'formulaValue' not in cells[6].get('userEnteredValue', {}):
                raise ValueError(f'{title}!G{row}: fórmula de saldo ausente.')
            for col, val in enumerate(vals):
                if 'formulaValue' in cells[col].get('userEnteredValue', {}):
                    raise ValueError(f'{title}: fórmula em campo de entrada.')
                rule = cells[col].get('dataValidation')
                if rule and val is not None:
                    import json
                    key = (book, json.dumps(rule, sort_keys=True))
                    if key not in self.options:
                        planner = self
                        class ValidationReader:
                            def cells(self, source, first, last, start_col, end_col):
                                rows = planner.client(book).cells(source, first, last, start_col, end_col)
                                planner.step(book).setdefault('guards', []).append({
                                    'title': source, 'first': first, 'last': last, 'start_col': start_col,
                                    'end_col': end_col, 'rows': stable(rows)})
                                return rows
                        self.options[key] = validation_options(ValidationReader(), self.metas[book], rule)
                    if val not in self.options[key]:
                        raise ValueError(f'{title}, linha {row}: valor {val!r} fora da validação.')
                self.put(book, block, row, col, val)
            self.put(book, block, row, 7, record[0])
            self.put(book, block, row, 8, kind)
            self.summary.append({'id': record[0], 'planilha': book, 'aba': title, 'linha': row,
                                 'tipo': kind, 'vencimento': date_text(record[9]), 'previsto': vals[4], 'pagamento': vals[5]})

    def write_record(self, block, row, record):
        for col, val in enumerate(record):
            if col == 14:
                val = f'=IF(M{row}<>"";"paga";IF(J{row}<TODAY();"atrasada";"pendente"))'
                self.put(self.master, block, row, col, val, formula=True)
            else:
                self.put(self.master, block, row, col, val)

    def register(self, raw):
        wanted = purchase(raw)
        block, existing = self.ledger(create=True)
        group = {key: r for key, r in existing.items() if r[1][1] == raw['id']}
        if group and set(group) != {r[0] for r in wanted}:
            raise ValueError('Conflito: produtos ou parcelas da compra existente são diferentes.')
        for record in wanted:
            found = existing.get(record[0])
            old = found[1] if found else None
            if old and (old[:12] != record[:12] or old[15] != record[15]):
                raise ValueError(f'Conflito no cadastro: {record[0]}.')
            desired = old or record
            self.project(old, desired)
            if found:
                continue
            free = next((i+1 for i, c in enumerate(block['after'][1:], 1) if not any(values(c))), None)
            if free is None:
                raise ValueError('Aba Parcelas cheia. Amplie a grade manualmente.')
            self.write_record(block, free, desired)
        return self.finish()

    def pay(self, key, payment_date):
        paid = serial(payment_date)
        block, records = self.ledger()
        if key not in records:
            raise ValueError(f'Parcela não encontrada: {key}')
        row, old = records[key]
        if paid < old[5]:
            raise ValueError('Pagamento não pode ser anterior à compra.')
        if old[12] is not None and (old[12] != paid or old[13] != old[10]):
            raise ValueError('Parcela já paga com dados diferentes. Correções não são suportadas nesta versão.')
        desired = copy.deepcopy(old)
        desired[12], desired[13] = paid, old[10]
        self.project(old, desired)
        self.write_record(block, row, desired)
        return self.finish()

    def listing(self, compra=None, desde=None, ate=None, situacao=None):
        _, records = self.ledger()
        if desde: iso(desde)
        if ate: iso(ate)
        if desde and ate and desde > ate:
            raise ValueError('Data inicial posterior à final.')
        result = []
        for _, r in records.values():
            due, state = date_text(r[9]), status(r)
            if compra and r[1] != compra or desde and due < desde or ate and due > ate or situacao and state != situacao:
                continue
            result.append({'id': r[0], 'descricao': r[3], 'parcela': f'{r[6]}/{r[7]}', 'vencimento': due,
                           'previsto': r[10], 'status': state, 'data_pagamento': date_text(r[12]) if r[12] is not None else None,
                           'pago': r[13]})
        return sorted(result, key=lambda r: (r['vencimento'], r['id']))

    def finish(self):
        for step in self.steps.values():
            for block in step['blocks']:
                if block['before'] is not None or block['title'] != LEDGER:
                    continue
                sid = block['sheet_id']
                step['requests'].append({'repeatCell': {'range': {'sheetId': sid, 'startRowIndex': 0, 'endRowIndex': 1},
                    'cell': {'userEnteredFormat': {'textFormat': {'bold': True}, 'backgroundColor': {'red': 0.9, 'green': 0.9, 'blue': 0.9}}},
                    'fields': 'userEnteredFormat.textFormat.bold,userEnteredFormat.backgroundColor'}})
                for col in (5, 9, 10, 11, 12, 13):
                    fmt = {'type': 'DATE', 'pattern': 'dd/mm/yyyy'} if col in (5,9,12) else {'type': 'NUMBER', 'pattern': '#,##0.00;[Red]-#,##0.00'}
                    step['requests'].append({'repeatCell': {'range': {'sheetId': sid, 'startRowIndex': 1, 'endRowIndex': block['last'], 'startColumnIndex': col, 'endColumnIndex': col+1},
                        'cell': {'userEnteredFormat': {'numberFormat': fmt}}, 'fields': 'userEnteredFormat.numberFormat'}})
                step['requests'].extend([
                    {'autoResizeDimensions': {'dimensions': {'sheetId': sid, 'dimension': 'COLUMNS', 'startIndex': 0, 'endIndex': 15}}},
                    {'updateDimensionProperties': {'range': {'sheetId': sid, 'dimension': 'COLUMNS', 'startIndex': 15, 'endIndex': 16}, 'properties': {'hiddenByUser': True}, 'fields': 'hiddenByUser'}},
                    {'setBasicFilter': {'filter': {'range': {'sheetId': sid, 'startRowIndex': 0, 'endRowIndex': block['last'], 'startColumnIndex': 0, 'endColumnIndex': 16}}}},
                ])
        # Arquivo principal por último: o histórico só é confirmado após as projeções.
        books = sorted(self.steps, key=lambda b: (b == self.master, b))
        return {'version': 1, 'summary': self.summary,
                'steps': [self.steps[b] for b in books if self.steps[b]['requests']], 'state': 'pending'}
