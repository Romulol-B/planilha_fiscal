from pathlib import Path


SCOPE = 'https://www.googleapis.com/auth/spreadsheets'
CELL_FIELDS = 'userEnteredValue,formattedValue,userEnteredFormat,dataValidation,chipRuns,note'
AUDIT_CELL_FIELDS = 'userEnteredValue,effectiveValue(errorValue),dataValidation'
AUDIT_BATCH_CELLS = 100_000


class AuditSnapshot:
    """Leituras da mesma auditoria reutilizam as células obtidas do Google."""

    def __init__(self, metadata):
        self.meta = metadata
        self.rows = {sheet['properties']['title']: {} for sheet in metadata['sheets']}

    def metadata(self):
        return self.meta

    def add(self, response):
        for sheet in response.get('sheets', []):
            rows = self.rows[sheet['properties']['title']]
            for block in sheet.get('data', []):
                first_row = block.get('startRow', 0) + 1
                first_col = block.get('startColumn', 0)
                for row, data in enumerate(block.get('rowData', []), first_row):
                    cells = {col: cell for col, cell in enumerate(data.get('values', []), first_col) if cell}
                    if cells:
                        rows.setdefault(row, {}).update(cells)

    def cells(self, title, start_row, end_row, start_col=0, end_col=7):
        rows = self.rows[title]
        result = []
        for row in range(start_row, end_row + 1):
            cells = rows.get(row, {})
            result.append([cells.get(col, {}) for col in range(start_col, end_col)])
        return result


class Client:
    def __init__(self, spreadsheet_id, credentials_path):
        if not Path(credentials_path).is_file():
            raise ValueError(
                f'Credencial não encontrada: {credentials_path}. '
                'Siga o guia docs/GOOGLE_SHEETS.md para configurar a conta de serviço.'
            )
        try:
            from google.oauth2.service_account import Credentials
            from google.auth.transport.requests import AuthorizedSession
        except ImportError:
            raise ValueError('Instale as dependências: .venv/bin/python -m pip install -r requirements.txt') from None
        try:
            credentials = Credentials.from_service_account_file(str(credentials_path), scopes=[SCOPE])
        except (ValueError, KeyError):
            raise ValueError('JSON inválido: use a chave de uma conta de serviço do Google.') from None
        self.email = credentials.service_account_email
        self.session = AuthorizedSession(credentials)
        self.base = f'https://sheets.googleapis.com/v4/spreadsheets/{spreadsheet_id}'

    def request(self, method, suffix='', **kwargs):
        from google.auth.exceptions import GoogleAuthError
        from requests import RequestException

        try:
            response = self.session.request(method, self.base + suffix, timeout=30, **kwargs)
        except GoogleAuthError:
            raise RuntimeError('Falha ao autenticar no Google. Confira a chave da conta de serviço.') from None
        except RequestException:
            raise RuntimeError(
                'Falha de rede. Se estava aplicando alterações, leia as linhas antes de repetir: '
                'o Google pode ter recebido a gravação.'
            ) from None
        if not response.ok:
            hints = {
                400: 'O Google recusou os dados ou o intervalo.',
                401: 'A credencial não foi aceita.',
                403: f'Ative a Google Sheets API e compartilhe a planilha como Editor com {self.email}.',
                404: f'Confira o ID da planilha e o compartilhamento com {self.email}.',
                429: 'Cota excedida. Aguarde antes de tentar novamente.',
            }
            raise RuntimeError(f'Google Sheets HTTP {response.status_code}: '
                               + hints.get(response.status_code, 'Falha no serviço. Confira o estado antes de repetir.'))
        return response.json()

    def metadata(self):
        return self.request('GET', params={
            'fields': 'spreadsheetId,spreadsheetUrl,properties(title,locale),sheets(properties,merges)',
        })

    def audit_snapshot(self):
        from .model import column, quote_sheet

        metadata = self.metadata()
        snapshot = AuditSnapshot(metadata)
        ranges = []
        size = 0

        def read_ranges():
            snapshot.add(self.request('GET', params={
                'ranges': ranges,
                'includeGridData': 'true',
                'fields': ('sheets(properties(title),'
                           f'data(startRow,startColumn,rowData(values({AUDIT_CELL_FIELDS}))))'),
            }))

        # Limita o volume e o comprimento da URL mesmo em abas grandes.
        for sheet in metadata['sheets']:
            props = sheet['properties']
            grid = props.get('gridProperties')
            if not grid:
                continue
            title = quote_sheet(props['title'])
            width = grid['columnCount']
            rows_per_range = max(1, AUDIT_BATCH_CELLS // width)
            for first in range(1, grid['rowCount'] + 1, rows_per_range):
                last = min(first + rows_per_range - 1, grid['rowCount'])
                area = width * (last - first + 1)
                if ranges and (size + area > AUDIT_BATCH_CELLS or len(ranges) == 12):
                    read_ranges()
                    ranges = []
                    size = 0
                ranges.append(f'{title}!A{first}:{column(width - 1)}{last}')
                size += area
        if ranges:
            read_ranges()
        return snapshot

    def cells(self, title, start_row, end_row, start_col=0, end_col=7):
        from .model import column, quote_sheet

        result = self.request('GET', params={
            'ranges': f'{quote_sheet(title)}!{column(start_col)}{start_row}:{column(end_col - 1)}{end_row}',
            'includeGridData': 'true',
            'fields': f'sheets(data(startRow,startColumn,rowData(values({CELL_FIELDS}))))',
        })
        rows = [[{} for _ in range(end_col - start_col)] for _ in range(end_row - start_row + 1)]
        for sheet in result.get('sheets', []):
            for block in sheet.get('data', []):
                for offset, row in enumerate(block.get('rowData', [])):
                    r = block.get('startRow', 0) + offset - start_row + 1
                    for c_offset, cell in enumerate(row.get('values', [])):
                        c = block.get('startColumn', 0) + c_offset - start_col
                        if 0 <= r < len(rows) and 0 <= c < len(rows[r]):
                            rows[r][c] = cell
        return rows

    def batch(self, requests):
        return self.request('POST', ':batchUpdate', json={'requests': requests})
