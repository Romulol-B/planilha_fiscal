"""Equivalência simbólica das fórmulas numéricas de saldo do modelo.

Reconhece somas, subtrações, SUM/SOMA e IF/SE que apenas substitui um
resultado zero por zero. Não executa fórmulas nem infere equivalência pelos
valores atuais da planilha. Outras funções permanecem bloqueadas.
"""
import re


def add(left, right, sign=1):
    result = left.copy()
    for key, value in right.items():
        result[key] = result.get(key, 0) + sign * value
        if not result[key]:
            del result[key]
    return result


class BalanceExpression:
    def __init__(self, formula, row, total):
        if not isinstance(formula, str) or len(formula) > 2048:
            raise ValueError('Fórmula ausente ou longa demais.')
        text = re.sub(r'\s+', '', formula).replace('$', '').replace(';', ',').upper()
        if not text.startswith('='):
            raise ValueError('Esperada uma fórmula.')
        text = text[1:]
        self.tokens = re.findall(r'<>|[A-Z]+[1-9][0-9]*|[A-Z]+|[0-9]+|[()+,:=\-]', text)
        if ''.join(self.tokens) != text:
            raise ValueError('Sintaxe não suportada.')
        self.pos, self.depth = 0, 0
        self.row, self.total = row, total

    def peek(self, offset=0):
        pos = self.pos + offset
        return self.tokens[pos] if pos < len(self.tokens) else None

    def take(self, token=None):
        actual = self.peek()
        if actual is None or token is not None and actual != token:
            raise ValueError('Sintaxe incompleta ou não suportada.')
        self.pos += 1
        return actual

    def payments(self, first, last):
        if not 3 <= first <= last < self.total:
            raise ValueError('Intervalo fora das linhas de pagamentos.')
        # Pn representa F3+...+Fn. Evita expandir intervalos longos célula a célula.
        return add({f'P{last}': 1}, {f'P{first-1}': 1}, -1)

    def reference(self, token):
        match = re.fullmatch(r'([A-Z]+)([1-9][0-9]*)', token)
        if not match:
            raise ValueError('Referência não suportada.')
        col, number = match[1], int(match[2])
        if col == 'F':
            if number == self.total and self.row == self.total:
                # locate_total já conferiu Ftotal = SUM(F3:Ftotal-1).
                return self.payments(3, self.total - 1)
            return self.payments(number, number)
        if col == 'G' and number == 1:
            return {'G1': 1}
        if col == 'G' and self.row > 3 and number == self.row - 1:
            # check_month valida de cima para baixo: a linha anterior já passou.
            return add({'G1': 1}, self.payments(3, number))
        raise ValueError('Referência fora do saldo ou dos pagamentos.')

    def expression(self):
        self.depth += 1
        if self.depth > 32:
            raise ValueError('Aninhamento excessivo.')
        try:
            result = self.atom()
            while self.peek() in ('+', '-'):
                sign = 1 if self.take() == '+' else -1
                result = add(result, self.atom(), sign)
            return result
        finally:
            self.depth -= 1

    def atom(self):
        token = self.take()
        if token in ('+', '-'):
            # Sinais unários só precedem números/referências/grupos; sem recursão ilimitada.
            if self.peek() in ('+', '-'):
                raise ValueError('Sinais consecutivos não suportados.')
            return add({}, self.atom(), 1 if token == '+' else -1)
        if token == '(':
            result = self.expression()
            self.take(')')
            return result
        if token.isdigit():
            return {'constant': int(token)} if int(token) else {}
        if token in ('SUM', 'SOMA'):
            self.take('(')
            result = {}
            while True:
                if self.peek(1) == ':':
                    first = self.take()
                    self.take(':')
                    last = self.take()
                    if not re.fullmatch(r'F[1-9][0-9]*', first) or not re.fullmatch(r'F[1-9][0-9]*', last):
                        raise ValueError('Somente intervalos da coluna F são suportados.')
                    value = self.payments(int(first[1:]), int(last[1:]))
                else:
                    value = self.expression()
                result = add(result, value)
                if self.peek() != ',':
                    break
                self.take(',')
            self.take(')')
            return result
        if token in ('IF', 'SE'):
            self.take('(')
            left = self.expression()
            operator = self.take()
            if operator not in ('=', '<>'):
                raise ValueError('Condição não suportada.')
            difference = add(left, self.expression(), -1)
            self.take(',')
            when_true = self.expression()
            self.take(',')
            when_false = self.expression()
            self.take(')')
            if when_true == when_false:
                return when_true
            zero_branch, other = (when_true, when_false) if operator == '=' else (when_false, when_true)
            # IF(X=0,0,X) e IF(X<>0,X,0) preservam X, inclusive se 0 e X trocarem lados.
            if not zero_branch and other in (difference, add({}, difference, -1)):
                return other
            raise ValueError('A condição pode alterar o saldo.')
        return self.reference(token)

    def equivalent(self):
        result = self.expression()
        if self.peek() is not None:
            raise ValueError('Trecho de fórmula não reconhecido.')
        last = self.total - 1 if self.row == self.total else self.row
        expected = add({'G1': 1}, self.payments(3, last))
        return result == expected


def equivalent_balance(formula, row, total):
    """Usar em ordem crescente de linha, após conferir o total da coluna F."""
    try:
        return BalanceExpression(formula, row, total).equivalent()
    except (ValueError, RecursionError):
        return False
