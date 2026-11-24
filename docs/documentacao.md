# Arquitetura do Velas

O projeto automatiza o registro de compras e o acompanhamento do fluxo de caixa.
O Google Sheets é a interface de trabalho para quem consulta e atualiza as finanças.

- `velas_notas_fiscais` lê NF-e, valida totais e gera arquivos JSON de compra.
- `velas_sheets` audita o modelo e planeja compras e pagamentos. A aba
  `Parcelas` mantém o histórico. As abas mensais recebem as projeções.
- `velas_common` reúne o bloqueio local usado pela CLI durante a gravação.
- `exemplos` contém XMLs fictícios, entradas JSON e um modelo XLSX de demonstração.

A validação e a divisão de valores em centavos acontecem antes de escrever no
Google Sheets. Os lançamentos usam identificadores estáveis para que uma compra
ou baixa já aplicada seja reconhecida. Cada arquivo recebe um lote de alterações
e a aba de histórico é atualizada por último. Em uma operação com mais de um
arquivo, o diário local permite conferir e retomar a gravação.

## Verificação

```bash
python -m unittest discover -s tests -v
python -m velas_sheets auditar-modelo --xlsx exemplos/modelo-demonstracao.xlsx
```

O teste de recálculo usa o LibreOffice Calc quando disponível. Para regenerar
o modelo XLSX a partir do código:

```bash
python -m scripts.gerar_modelo_demo --saida exemplos/modelo-demonstracao.xlsx
```
