from datetime import date
from decimal import Decimal, InvalidOperation
import json
import re


HEADERS = ['Data', 'Descritivo', 'Centro de custo', 'Vencimento',
           'Previsto R$', 'Receita/Pagamento', 'Saldo R$']
FIELDS = {'data': 0, 'descritivo': 1, 'centro_custo': 2,
          'vencimento': 3, 'previsto': 4, 'pagamento': 5}
FIRST_ROW, LAST_ROW = 3, 96


def column(index):
    result = ''
    while index >= 0:
        index, remainder = divmod(index, 26)
        result = chr(65 + remainder) + result
        index -= 1
    return result


def quote_sheet(title):
    return "'" + title.replace("'", "''") + "'"


def find_sheet(metadata, title):
    for sheet in metadata['sheets']:
        if sheet['properties']['title'] == title:
            return sheet
    raise ValueError(f'Aba não encontrada: {title}. Use conectar para listar as abas.')


def check_headers(rows):
    actual = [c.get('userEnteredValue', {}).get('stringValue', '').strip() for c in rows[0]]
    if actual != HEADERS:
        raise ValueError(f'Cabeçalhos de A2:G2 diferentes do modelo esperado: {actual}')


def parse_value(field, value):
    # null limpa apenas a célula explicitamente indicada; campos ausentes são preservados.
    if value is None:
        return {}
    if field in ('data', 'vencimento'):
        if not isinstance(value, str) or not re.fullmatch(r'\d{4}-\d{2}-\d{2}', value):
            raise ValueError(f'{field}: use uma data no formato AAAA-MM-DD.')
        serial = (date.fromisoformat(value) - date(1899, 12, 30)).days
        return {'numberValue': serial}
    if field in ('previsto', 'pagamento'):
        raw = str(value)
        if not re.fullmatch(r'-?\d+(?:[.,]\d{1,2})?', raw):
            raise ValueError(f'{field}: use um número com até dois decimais, sem separador de milhar.')
        try:
            amount = Decimal(raw.replace(',', '.'))
        except InvalidOperation:
            raise ValueError(f'{field}: valor monetário inválido.') from None
        if not amount.is_finite() or abs(amount) >= Decimal('1000000000000'):
            raise ValueError(f'{field}: valor fora do limite deste teste.')
        return {'numberValue': float(amount)}
    if not isinstance(value, str) or not value.strip():
        raise ValueError(f'{field}: informe um texto não vazio ou null para limpar.')
    # stringValue impede que descrições começando por "=" sejam executadas como fórmula.
    return {'stringValue': value}


def load_edits(path):
    edits = json.loads(path.read_text(encoding='utf-8'))
    if not isinstance(edits, list) or not 1 <= len(edits) <= 20:
        raise ValueError('Informe uma lista JSON com 1 a 20 linhas.')
    result, seen = [], set()
    for edit in edits:
        if not isinstance(edit, dict):
            raise ValueError('Cada alteração deve ser um objeto JSON.')
        row = edit.get('linha')
        if type(row) is not int or not FIRST_ROW <= row <= LAST_ROW:
            raise ValueError(f'linha deve estar entre {FIRST_ROW} e {LAST_ROW}.')
        if row in seen:
            raise ValueError(f'Linha repetida: {row}. Agrupe os campos em um único objeto.')
        seen.add(row)
        unknown = edit.keys() - {'linha', *FIELDS}
        if unknown or len(edit) == 1:
            raise ValueError(f'Campos inválidos ou ausentes na linha {row}: {sorted(unknown)}')
        result.append((row, {FIELDS[k]: parse_value(k, v) for k, v in edit.items() if k != 'linha'}))
    return result


def validation_options(client, metadata, rule):
    condition = rule.get('condition', {})
    values = condition.get('values', [])
    if condition.get('type') == 'ONE_OF_LIST':
        if any(v.get('userEnteredValue', '').startswith('=') for v in values):
            raise ValueError('A lista de validação contém fórmulas não suportadas neste teste.')
        return [v.get('userEnteredValue', '') for v in values]
    if condition.get('type') != 'ONE_OF_RANGE' or len(values) != 1:
        raise ValueError('Regra de validação não suportada neste teste; a célula não será alterada.')
    ref = values[0].get('userEnteredValue', '')
    match = re.fullmatch(r"=?('(?:[^']|'')+'|[^!]+)!\$?([A-Z]+)\$?(\d+):\$?([A-Z]+)\$?(\d+)", ref)
    if not match:
        raise ValueError(f'Intervalo de validação não suportado: {ref}')
    title, col, first, last_col, last = match.groups()
    if title.startswith("'"):
        title = title[1:-1].replace("''", "'")
    first, last = int(first), int(last)
    index = 0
    for char in col:
        index = index * 26 + ord(char) - 64
    grid = find_sheet(metadata, title)['properties']['gridProperties']
    if col != last_col or not 1 <= first <= last <= grid['rowCount'] or last - first >= 500 or index > grid['columnCount']:
        raise ValueError('Intervalo de validação fora dos limites deste teste.')
    return [c[0]['formattedValue'] for c in client.cells(title, first, last, index - 1, index)
            if c[0].get('formattedValue')]


def plan_edits(client, metadata, title, edits):
    if not title.startswith('TESTE '):
        raise ValueError('Esta CLI escreve apenas em abas cujo nome começa com "TESTE ".')
    sheet = find_sheet(metadata, title)
    props = sheet['properties']
    grid = props['gridProperties']
    if grid['rowCount'] < LAST_ROW or grid['columnCount'] < 7:
        raise ValueError('A aba não tem as dimensões do modelo financeiro.')
    check_headers(client.cells(title, 2, 2))
    changes, snapshots, requests = [], {}, []
    options_cache = {}
    for row, fields in edits:
        cells = client.cells(title, row, row)[0]
        snapshots[row] = cells
        if 'formulaValue' not in cells[6].get('userEnteredValue', {}):
            raise ValueError(f'G{row} não contém a fórmula de saldo esperada do modelo.')
        for col, value in fields.items():
            cell, address = cells[col], f'{column(col)}{row}'
            for merged in sheet.get('merges', []):
                if (merged.get('startRowIndex', 0) <= row - 1 < merged.get('endRowIndex', grid['rowCount'])
                        and merged.get('startColumnIndex', 0) <= col < merged.get('endColumnIndex', grid['columnCount'])):
                    raise ValueError(f'{address} pertence a uma mesclagem; alteração bloqueada.')
            if 'formulaValue' in cell.get('userEnteredValue', {}) or cell.get('chipRuns'):
                raise ValueError(f'{address} contém fórmula ou chip; alteração bloqueada.')
            if value and cell.get('dataValidation'):
                key = json.dumps(cell['dataValidation'], sort_keys=True)
                if key not in options_cache:
                    options_cache[key] = validation_options(client, metadata, cell['dataValidation'])
                options = options_cache[key]
                if value.get('stringValue') not in options:
                    raise ValueError(f'{address}: valor fora da lista permitida: {options}')
            before = cell.get('userEnteredValue', {})
            if before == value:
                continue
            changes.append({'celula': address, 'antes': before, 'depois': value})
            requests.append({'updateCells': {
                'range': {'sheetId': props['sheetId'], 'startRowIndex': row - 1, 'endRowIndex': row,
                          'startColumnIndex': col, 'endColumnIndex': col + 1},
                'rows': [{'values': [{'userEnteredValue': value} if value else {}]}],
                'fields': 'userEnteredValue',
            }})
    return changes, snapshots, requests


def stable_cells(rows):
    # formattedValue muda com o recálculo; os valores digitados, fórmulas e estrutura não.
    return [[{k: v for k, v in cell.items() if k != 'formattedValue'} for cell in row] for row in rows]


def verify_edits(client, title, edits, snapshots):
    import copy

    for row, fields in edits:
        expected = copy.deepcopy(snapshots[row])
        for col, value in fields.items():
            if value:
                expected[col]['userEnteredValue'] = value
            else:
                expected[col].pop('userEnteredValue', None)
        actual = client.cells(title, row, row)[0]
        if stable_cells([actual]) != stable_cells([expected]):
            raise RuntimeError(f'Gravação enviada, mas a conferência da linha {row} divergiu. Leia a aba antes de repetir.')
