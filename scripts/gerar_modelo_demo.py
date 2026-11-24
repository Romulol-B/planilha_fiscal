"""Gera um modelo financeiro vazio, com categorias fictícias para demonstração."""
from argparse import ArgumentParser
from pathlib import Path

from openpyxl import Workbook
from openpyxl.styles import Alignment, Font, PatternFill
from openpyxl.utils import get_column_letter
from openpyxl.worksheet.datavalidation import DataValidation

from velas_sheets.audit import balance_formula, total_formula
from velas_sheets.installments import MONTHS
from velas_sheets.model import HEADERS


CATEGORIES = (
    'Conta  a pagar', 'Conta a receber', 'Matéria-prima', 'Embalagens',
    'Frete', 'Serviços', 'Marketing', 'Equipamentos', 'Manutenção',
    'Eventos', 'Fornecedores', 'Aluguel', 'Taxas', 'Outros',
)
TOTAL_ROW = 97
MONEY = '#,##0.00;[Red]-#,##0.00'


def style_header(cells):
    for cell in cells:
        cell.fill = PatternFill('solid', fgColor='162C3D')
        cell.font = Font(bold=True, color='FFFFFF')
        cell.alignment = Alignment(vertical='center')


def generate(path):
    book = Workbook()
    book.properties.creator = 'Projeto Velas'
    book.properties.title = 'Modelo financeiro de demonstração'
    consolidated = book.active
    consolidated.title = 'Anal Fin - consolidado'
    consolidated['A1'] = 'Centros de custo de demonstração'
    consolidated['B2'] = 'Centro de custo'
    consolidated.column_dimensions['B'].width = 27
    consolidated.row_dimensions.group(3, 68, hidden=True)
    style_header(consolidated['A2:B2'][0])
    for row, category in enumerate(CATEGORIES, 69):
        consolidated[f'B{row}'] = category

    dre = book.create_sheet('DRE')
    dre['A1'] = 'Demonstrativo de fluxo por centro de custo'
    dre['A2'] = 'As colunas separam vencimentos previstos de pagamentos realizados.'
    dre['A65'] = 'Centro de custo'
    dre.column_dimensions['A'].width = 27
    dre.freeze_panes = 'B3'
    dre.row_dimensions.group(3, 64, hidden=True)
    for row in range(66, 80):
        dre[f'A{row}'] = f"='Anal Fin - consolidado'!B{row + 3}"
    dre['A80'] = 'Total'

    for index, month in enumerate(MONTHS):
        sheet = book.create_sheet('FC ' + month)
        sheet['A1'] = f'Fluxo de caixa · {month}'
        sheet['F1'] = 'Saldo inicial'
        previous = MONTHS[index - 1] if index else None
        sheet['G1'] = f"='FC {previous}'!G{TOTAL_ROW}" if previous else 0
        sheet['G1'].number_format = MONEY
        sheet.append(HEADERS)
        sheet.freeze_panes = 'A3'
        for col, width in enumerate((14, 44, 25, 14, 16, 21, 16, 18, 18), 1):
            sheet.column_dimensions[get_column_letter(col)].width = width
        style_header(sheet['A2:G2'][0])
        validation = DataValidation(type='list', formula1='"' + ','.join(CATEGORIES) + '"',
                                    allow_blank=True, showErrorMessage=True)
        sheet.add_data_validation(validation)
        validation.add(f'C3:C{TOTAL_ROW - 1}')
        for row in range(3, TOTAL_ROW):
            for col in ('A', 'D'):
                sheet[f'{col}{row}'].number_format = 'dd/mm/yyyy'
            for col in ('E', 'F', 'G'):
                sheet[f'{col}{row}'].number_format = MONEY
            sheet[f'G{row}'] = balance_formula(row)
        sheet[f'B{TOTAL_ROW}'] = 'Fechamento do mês'
        for col in ('E', 'F'):
            sheet[f'{col}{TOTAL_ROW}'] = f'=SUM({col}3:{col}{TOTAL_ROW - 1})'
        sheet[f'G{TOTAL_ROW}'] = total_formula(TOTAL_ROW)
        for col in ('E', 'F', 'G'):
            sheet[f'{col}{TOTAL_ROW}'].number_format = MONEY
        style_header(sheet[f'A{TOTAL_ROW}:G{TOTAL_ROW}'][0])

        for offset, (amount_col, label) in enumerate((('E', 'previsto'), ('F', 'realizado'))):
            col = get_column_letter(index * 2 + offset + 2)
            dre[f'{col}65'] = f'{month} {label}'
            dre.column_dimensions[col].width = 17
            for row in range(66, 80):
                dre[f'{col}{row}'] = (
                    f"=SUMIF('FC {month}'!C3:C{TOTAL_ROW - 1},$A{row},"
                    f"'FC {month}'!{amount_col}3:{amount_col}{TOTAL_ROW - 1})"
                )
                dre[f'{col}{row}'].number_format = MONEY
            dre[f'{col}80'] = f'=SUM({col}66:{col}79)'
            dre[f'{col}80'].number_format = MONEY
    style_header(dre[65])
    style_header(dre[80])
    book.active = 2
    book.save(path)


if __name__ == '__main__':
    parser = ArgumentParser(description=__doc__)
    parser.add_argument('--saida', type=Path, required=True)
    args = parser.parse_args()
    generate(args.saida)
