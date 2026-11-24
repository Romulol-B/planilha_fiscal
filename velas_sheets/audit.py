"""Auditoria somente leitura, comum ao XLSX e à API Sheets."""
import re

from .balance_formulas import equivalent_balance
from .model import column


def normalize(formula):
    return (formula or '').replace('$', '').replace(' ', '').replace(';', ',').upper()


def balance_formula(row):
    return f'=SUM(G$1,F$3:F{row})'


def total_formula(row):
    return f'=SUM(G1,F{row})'


def locate_total(client, title, count):
    found = []
    for start in range(2, count+1, 250):
        for row, cells in enumerate(client.cells(title, start, min(start+249,count), 5, 6), start):
            f = normalize(cells[0].get('userEnteredValue', {}).get('formulaValue'))
            m = re.fullmatch(r'=SUM\(F3:F(\d+)\)', f)
            if m and int(m[1]) == row-1:
                found.append(row)
    if len(found) != 1:
        raise ValueError(f'{title}: total de pagamentos não identificado com segurança.')
    return found[0]


def check_month(client, title, total):
    rows = client.cells(title, 3, total, 0, 7)
    for row, cells in enumerate(rows, 3):
        expected = total_formula(row) if row == total else balance_formula(row)
        formula = cells[6].get('userEnteredValue', {}).get('formulaValue')
        if normalize(formula) != normalize(expected) and not equivalent_balance(formula, row, total):
            raise ValueError(f'{title}!G{row}: saldo incompatível; fórmula não reconhecida como equivalente a '
                             f'{expected}. Encontrada: {str(formula)[:160]}. Execute auditar-modelo.')
        if row < total and not cells[2].get('dataValidation'):
            raise ValueError(f'{title}!C{row}: validação de centro de custo ausente.')


def report_coverage(client, metadata, title, total, cache=None):
    """Recusa referências mensais truncadas após expansão manual."""
    prefix = re.escape("'" + title.replace("'", "''") + "'")
    pattern = re.compile(prefix + r'!\$?([A-Z]+)\$?(\d+):(?:'+prefix+r'!)?\$?([A-Z]+)\$?(\d+)', re.I)
    cache = {} if cache is None else cache
    for sheet in metadata['sheets']:
        name = sheet['properties']['title']
        if name.startswith('FC ') or name == 'Parcelas':
            continue
        grid = sheet['properties']['gridProperties']
        for start in range(1, grid['rowCount']+1, 250):
            key = (name, start)
            if key not in cache:
                cache[key] = client.cells(name,start,min(start+249,grid['rowCount']),0,grid['columnCount'])
            for row, cells in enumerate(cache[key],start):
                for cell in cells:
                    formula = cell.get('userEnteredValue',{}).get('formulaValue','')
                    for match in pattern.finditer(formula):
                        if match[1].upper() in ('C','E','F') and int(match[2]) <= 3 and int(match[4]) < total-1:
                            raise ValueError(f'{name}, linha {row}: referência de {title} termina em {match[4]}, antes de {total-1}.')


def audit(client):
    metadata = client.metadata()
    sheets = [s for s in metadata['sheets'] if s['properties'].get('gridProperties')]
    metadata = {**metadata, 'sheets': sheets}
    issues = []
    totals = {}
    reports = {}
    months = [s for s in metadata['sheets'] if s['properties']['title'].startswith('FC ')]
    for sheet in months:
        title = sheet['properties']['title']
        try:
            total = locate_total(client,title,sheet['properties']['gridProperties']['rowCount'])
        except ValueError as error:
            issues.append(str(error))
            continue
        totals[title] = total
        try:
            check_month(client,title,total)
        except ValueError as error:
            issues.append(str(error))
        try:
            report_coverage(client,metadata,title,total,reports)
        except ValueError as error:
            issues.append(str(error))
    from .installments import MONTHS
    for i, month in enumerate(MONTHS[1:],1):
        name, previous = 'FC '+month, 'FC '+MONTHS[i-1]
        if name in totals and previous in totals:
            formula = client.cells(name,1,1,6,7)[0][0].get('userEnteredValue',{}).get('formulaValue')
            if normalize(formula) != normalize(f"='{previous}'!G{totals[previous]}"):
                issues.append(f'{name}!G1: vínculo com o saldo de {previous} incorreto.')
    names = {s['properties']['title'] for s in metadata['sheets']}
    missing = {'FC '+m for m in MONTHS} - names
    issues.extend(f'Aba mensal ausente: {name}.' for name in sorted(missing))
    if 'DRE' not in names or 'Anal Fin - consolidado' not in names:
        issues.append('Modelo incompleto: DRE ou consolidado ausente.')
    if 'DRE' in names:
        for row,cells in enumerate(client.cells('DRE',66,79,0,1),66):
            expected = f"='Anal Fin - consolidado'!B{row+3}"
            if normalize(cells[0].get('userEnteredValue',{}).get('formulaValue')) != normalize(expected):
                issues.append(f'DRE!A{row}: esperado {expected}.')
    for sheet in metadata['sheets']:
        name, grid = sheet['properties']['title'], sheet['properties']['gridProperties']
        for start in range(1,grid['rowCount']+1,250):
            for row,cells in enumerate(client.cells(name,start,min(start+249,grid['rowCount']),0,grid['columnCount']),start):
                for col,c in enumerate(cells):
                    if '#REF!' in c.get('userEnteredValue',{}).get('formulaValue',''):
                        issues.append(f'{name}!{column(col)}{row}: referência quebrada.')
                    elif error := c.get('effectiveValue', {}).get('errorValue'):
                        issues.append(f'{name}!{column(col)}{row}: erro de cálculo ({error["type"]}).')
    return issues


class XlsxReader:
    def __init__(self, path):
        import warnings
        from xml.etree import ElementTree as ET
        from zipfile import ZipFile

        from openpyxl import load_workbook
        from openpyxl.worksheet.cell_range import MultiCellRange

        from .xlsx_model import sheet_paths
        with warnings.catch_warnings():
            warnings.simplefilter('ignore')
            self.book = load_workbook(path,read_only=False)
        self.validations = {}
        with ZipFile(path) as archive:
            for title, member in sheet_paths(archive).items():
                self.validations[title] = [MultiCellRange(node.text) for node in ET.fromstring(archive.read(member)).iter()
                    if node.tag.endswith('}sqref') and node.text]

    def metadata(self):
        return {'sheets':[{'properties':{'sheetId':i,'title':s.title,'gridProperties':{'rowCount':max(s.max_row,100),'columnCount':max(s.max_column,16)}}} for i,s in enumerate(self.book)]}

    def cells(self,title,start,end,start_col=0,end_col=7):
        sheet=self.book[title]; result=[]
        for row in range(start,end+1):
            line=[]
            for col in range(start_col+1,end_col+1):
                c=sheet.cell(row,col); item={}
                if c.value is not None:
                    kind='formulaValue' if c.data_type=='f' else 'numberValue' if isinstance(c.value,(int,float)) else 'stringValue'
                    item['userEnteredValue']={kind:c.value}
                if any(c.coordinate in v for v in [*sheet.data_validations.dataValidation, *self.validations[title]]):
                    item['dataValidation']={'present':True}
                line.append(item)
            result.append(line)
        return result
