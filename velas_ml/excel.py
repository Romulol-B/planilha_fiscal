"""Adiciona uma aba ao XLSX preservando os outros componentes do arquivo.

Não salva a planilha original com openpyxl: isso removeria as extensões de
validação de dados presentes no modelo do usuário. Apenas a aba da integração
e, na primeira gravação, os três índices OOXML são escritos.
"""
from datetime import datetime
import hashlib
import os
from pathlib import Path
import posixpath
import re
import shutil
import tempfile
import warnings
from xml.dom import minidom
from xml.etree import ElementTree as ET
from zipfile import ZipFile, ZIP_DEFLATED

from openpyxl import load_workbook

SHEET = 'Mercado Livre'
HEADERS = [
    'Chave integração', 'Pedido', 'Data da compra', 'Produto', 'Categoria',
    'ID categoria', 'Quantidade', 'Preço unitário (R$)', 'Subtotal produtos (R$)',
    'Frete rateado (R$)', 'Total com frete (R$)', 'Status pedido', 'Pendências',
    'Incluir', 'Centro de custo', 'Observações', 'Envios',
]
MAIN = 'http://schemas.openxmlformats.org/spreadsheetml/2006/main'
REL = 'http://schemas.openxmlformats.org/officeDocument/2006/relationships'
PKG = 'http://schemas.openxmlformats.org/package/2006/relationships'
CT = 'http://schemas.openxmlformats.org/package/2006/content-types'


def fingerprint(path):
    return hashlib.sha256(path.read_bytes()).digest()


def read_preferences(path):
    if not path.exists():
        return []
    with warnings.catch_warnings():
        warnings.filterwarnings('ignore', message='Data Validation extension is not supported.*')
        book = load_workbook(path, read_only=True, data_only=False)
    try:
        if SHEET not in book.sheetnames:
            return []
        sheet = book[SHEET]
        if list(next(sheet.iter_rows(max_row=1, max_col=len(HEADERS), values_only=True))) != HEADERS:
            raise ValueError('A aba Mercado Livre já existe com outra estrutura; não será sobrescrita.')
        values, seen = [], set()
        for row in sheet.iter_rows(min_row=2, max_col=len(HEADERS), values_only=True):
            if not row[0]:
                continue
            key = str(row[0])
            if key in seen:
                raise ValueError('Chave duplicada na aba Mercado Livre; corrija antes de sincronizar.')
            seen.add(key)
            included = str(row[13] or '').strip().casefold()
            if included not in ('sim', 'não', 'nao'):
                raise ValueError('A coluna Incluir deve conter Sim ou Não.')
            values.append((key, int(included == 'sim'), str(row[14] or ''), str(row[15] or '')))
        return values
    finally:
        book.close()


def _column(number):
    text = ''
    while number:
        number, remainder = divmod(number - 1, 26)
        text = chr(65 + remainder) + text
    return text


def _sheet_xml(rows, header_style='0', money_style='0'):
    def tag(name):
        return f'{{{MAIN}}}{name}'

    root = ET.Element(tag('worksheet'))
    ET.SubElement(root, tag('dimension'), ref=f'A1:Q{max(1, len(rows) + 1)}')
    view = ET.SubElement(ET.SubElement(root, tag('sheetViews')), tag('sheetView'), workbookViewId='0')
    ET.SubElement(view, tag('pane'), ySplit='1', topLeftCell='A2', activePane='bottomLeft', state='frozen')
    columns = ET.SubElement(root, tag('cols'))
    for col, width in enumerate([26, 23, 28, 55, 32, 18, 13, 22, 24, 23, 24, 19, 65, 12, 26, 45, 25], 1):
        ET.SubElement(columns, tag('col'), min=str(col), max=str(col), width=str(width), customWidth='1')
    data = ET.SubElement(root, tag('sheetData'))

    def write(number, values, hidden=False):
        attrs = {'r': str(number)}
        if hidden:
            attrs['hidden'] = '1'
        row = ET.SubElement(data, tag('row'), attrs)
        for col, value in enumerate(values, 1):
            if value is None:
                continue
            attrs = {'r': _column(col) + str(number)}
            if number == 1:
                attrs['s'] = header_style
            elif 8 <= col <= 11:
                attrs['s'] = money_style
            cell = ET.SubElement(row, tag('c'), attrs)
            if isinstance(value, (float, int)):
                ET.SubElement(cell, tag('v')).text = str(value)
            else:
                cell.set('t', 'inlineStr')
                text = ET.SubElement(ET.SubElement(cell, tag('is')), tag('t'))
                text.set('{http://www.w3.org/XML/1998/namespace}space', 'preserve')
                text.text = re.sub(r'[\x00-\x08\x0b\x0c\x0e-\x1f]', '', str(value))[:32767]

    write(1, HEADERS)
    for index, row in enumerate(rows, 2):
        write(index, [
            row['key'], row['order_id'], row['date'], row['product'], row['category'], row['category_id'],
            row['quantity'], row['unit_price'] / 100, row['subtotal'] / 100,
            None if row['freight'] is None else row['freight'] / 100,
            None if row['total'] is None else row['total'] / 100,
            row['status'], '; '.join(row['warnings']), 'Sim' if row['included'] else 'Não',
            row['cost_center'], row['notes'], ', '.join(row['shipment_ids']),
        ], hidden=not row['included'])
    filters = ET.SubElement(root, tag('autoFilter'), ref=f'A1:Q{max(1, len(rows) + 1)}')
    filter_column = ET.SubElement(filters, tag('filterColumn'), colId='13')
    ET.SubElement(ET.SubElement(filter_column, tag('filters')), tag('filter'), val='Sim')
    validations = ET.SubElement(root, tag('dataValidations'), count='1')
    validation = ET.SubElement(validations, tag('dataValidation'), type='list',
                               sqref=f'N2:N{max(2, len(rows) + 1)}', showErrorMessage='1',
                               errorTitle='Escolha Sim ou Não', error='Use Sim ou Não.')
    ET.SubElement(validation, tag('formula1')).text = '"Sim,Não"'
    ET.register_namespace('', MAIN)
    return ET.tostring(root, encoding='utf-8', xml_declaration=True)


def export(source: Path, output: Path, rows, expected_fingerprint=None):
    current = output if output.exists() else source
    if current.suffix.lower() != '.xlsx' or source.resolve() == output.resolve():
        raise ValueError('Informe um arquivo XLSX de saída diferente do original.')
    if not current.exists():
        raise FileNotFoundError(f'Planilha não encontrada: {current}')
    # Valida a propriedade da aba antes de substituí-la.
    read_preferences(current)
    before = fingerprint(current)
    if expected_fingerprint is not None and before != expected_fingerprint:
        raise ValueError('A planilha mudou durante a sincronização. Feche o Excel e repita.')
    with ZipFile(current) as archive:
        entries = {info.filename: (info, archive.read(info.filename)) for info in archive.infolist()}
    workbook = minidom.parseString(entries['xl/workbook.xml'][1])
    relations = minidom.parseString(entries['xl/_rels/workbook.xml.rels'][1])
    types = minidom.parseString(entries['[Content_Types].xml'][1])
    sheets = workbook.getElementsByTagNameNS(MAIN, 'sheet')
    relation_map = {r.getAttribute('Id'): r for r in relations.getElementsByTagNameNS(PKG, 'Relationship')}

    def target(relation):
        path = relation.getAttribute('Target')
        return path.lstrip('/') if path.startswith('/') else posixpath.normpath('xl/' + path)

    existing = next((s for s in sheets if s.getAttribute('name') == SHEET), None)
    replacements = {}
    if existing:
        sheet_path = target(relation_map[existing.getAttributeNS(REL, 'id')])
    else:
        index = max(int(s.getAttribute('sheetId')) for s in sheets) + 1
        while f'xl/worksheets/sheet{index}.xml' in entries or f'rIdML{index}' in relation_map:
            index += 1
        sheet_path, rid = f'xl/worksheets/sheet{index}.xml', f'rIdML{index}'
        element = workbook.createElementNS(MAIN, 'sheet')
        element.setAttribute('name', SHEET)
        element.setAttribute('sheetId', str(index))
        element.setAttributeNS(REL, 'r:id', rid)
        # Garante a declaração de r mesmo em arquivos gerados por outros programas.
        element.setAttribute('xmlns:r', REL)
        workbook.getElementsByTagNameNS(MAIN, 'sheets')[0].appendChild(element)
        relation = relations.createElementNS(PKG, 'Relationship')
        for key, value in {'Id': rid, 'Type': REL + '/worksheet', 'Target': sheet_path[3:]}.items():
            relation.setAttribute(key, value)
        relations.documentElement.appendChild(relation)
        override = types.createElementNS(CT, 'Override')
        override.setAttribute('PartName', '/' + sheet_path)
        override.setAttribute('ContentType', 'application/vnd.openxmlformats-officedocument.spreadsheetml.worksheet+xml')
        types.documentElement.appendChild(override)
        replacements.update({
            'xl/workbook.xml': workbook.toxml(encoding='utf-8'),
            'xl/_rels/workbook.xml.rels': relations.toxml(encoding='utf-8'),
            '[Content_Types].xml': types.toxml(encoding='utf-8'),
        })
    # Reutiliza estilos existentes, sem regravar styles.xml.
    template = next((s for s in sheets if s.getAttribute('name') == 'FC JAN'), sheets[0])
    template_xml = ET.fromstring(entries[target(relation_map[template.getAttributeNS(REL, 'id')])][1])
    styles = {c.get('r'): c.get('s', '0') for c in template_xml.iter(f'{{{MAIN}}}c')}
    replacements[sheet_path] = _sheet_xml(rows, styles.get('A2', '0'), styles.get('F3', '0'))
    output.parent.mkdir(parents=True, exist_ok=True)
    fd, tmp = tempfile.mkstemp(dir=output.parent, suffix='.xlsx')
    os.close(fd)
    try:
        with ZipFile(tmp, 'w', ZIP_DEFLATED) as archive:
            for name, (info, payload) in entries.items():
                archive.writestr(info, replacements.pop(name, payload))
            for name, payload in replacements.items():
                archive.writestr(name, payload)
        # Readback antes de publicar a nova versão.
        if len(read_preferences(Path(tmp))) != len(rows):
            raise ValueError('Falha na validação da planilha gerada.')
        if fingerprint(current) != before:
            raise ValueError('A planilha foi editada durante a gravação. Repita com o Excel fechado.')
        if output.exists():
            backup = output.parent / 'backups'
            backup.mkdir(exist_ok=True)
            stamp = datetime.now().strftime('%Y%m%d-%H%M%S-%f')
            shutil.copy2(output, backup / f'{output.stem}-{stamp}.xlsx')
        os.replace(tmp, output)
    finally:
        if os.path.exists(tmp):
            os.unlink(tmp)
    return output

