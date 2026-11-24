"""Correções pontuais do XML, sem reserializar desenhos ou validações do Excel."""
from pathlib import Path
import posixpath
import re
import secrets
import shutil
from xml.etree import ElementTree as ET
from xml.sax.saxutils import escape
from zipfile import ZipFile

NS = {'s': 'http://schemas.openxmlformats.org/spreadsheetml/2006/main'}
REL = 'http://schemas.openxmlformats.org/officeDocument/2006/relationships'


def sheet_paths(archive):
    relations = {r.attrib['Id']: r.attrib['Target'] for r in
                  ET.fromstring(archive.read('xl/_rels/workbook.xml.rels'))}
    return {s.attrib['name']: posixpath.normpath(posixpath.join('xl', relations[s.attrib[f'{{{REL}}}id']])).lstrip('/')
            for s in ET.fromstring(archive.read('xl/workbook.xml')).find('s:sheets', NS)}


def replace_formulas(xml, formulas):
    seen = set()
    def replace(match):
        address = re.search(r'\br="([A-Z]+\d+)"', match[1])[1]
        if address not in formulas:
            return match[0]
        seen.add(address)
        attrs = re.sub(r'\s+t="[^"]*"', '', match[1])
        body = re.sub(r'<f(?:\s[^>]*)?>.*?</f>|<f\b[^>]*/>|<v(?:\s[^>]*)?>.*?</v>|<v\s*/>', '', match[2], flags=re.S)
        return f'<c{attrs}><f>{escape(formulas[address].lstrip("="))}</f>{body}</c>'
    result = re.sub(r'<c(\s[^>]*\br="[A-Z]+\d+"[^>]*)(?<!/)>(.*?)</c>', replace, xml, flags=re.S)
    if seen != formulas.keys():
        raise ValueError(f'Células não encontradas: {formulas.keys() - seen}')
    return result


def repair(path, backup_directory):
    """Retorna backup; None se já corrigido. Nunca altera o original sem backup."""
    from .audit import XlsxReader, balance_formula, locate_total, total_formula
    path, backup_directory = Path(path), Path(backup_directory)
    reader = XlsxReader(path)
    changes = {}
    with ZipFile(path) as source:
        paths = sheet_paths(source)
        for title in paths:
            if title.startswith('FC '):
                total = locate_total(reader, title, reader.book[title].max_row)
                formulas = {f'G{row}': balance_formula(row) for row in range(3, total)}
                formulas[f'G{total}'] = total_formula(total)
            elif title == 'DRE':
                formulas = {f'A{row}': f"='Anal Fin - consolidado'!B{row+3}" for row in range(66, 80)}
            else:
                continue
            formulas = {address: f for address, f in formulas.items() if reader.book[title][address].value != f}
            if formulas:
                changes[paths[title]] = replace_formulas(source.read(paths[title]).decode('utf-8'), formulas).encode('utf-8')
        if not changes:
            return None
        workbook = source.read('xl/workbook.xml').decode('utf-8')
        workbook = re.sub(r'<calcPr\b[^>]*/>', '<calcPr calcId="191029" fullCalcOnLoad="1" forceFullCalc="1"/>', workbook)
        changes['xl/workbook.xml'] = workbook.encode('utf-8')
        backup_directory.mkdir(parents=True, exist_ok=True)
        backup = backup_directory / f'{path.stem}-{secrets.token_hex(6)}.xlsx'
        shutil.copy2(path, backup)
        temporary = path.with_name(path.name + '.' + secrets.token_hex(4) + '.tmp')
        try:
            with ZipFile(temporary, 'w') as target:
                for member in source.infolist():
                    target.writestr(member, changes.get(member.filename, source.read(member.filename)))
                target.comment = source.comment
        except BaseException:
            temporary.unlink(missing_ok=True)
            raise
    temporary.replace(path)
    return backup
