# CLI de teste do Google Sheets

Primeira etapa da futura importação de compras da Shopee por CSV: conectar ao
Google Sheets, ler o modelo financeiro e editar linhas em uma aba de teste.
Requer Python 3.11+ e a Google Sheets API. Não importa CSV nesta etapa.

O arquivo local `config.sheets.toml` já aponta para a planilha encontrada no Drive.
Em outra instalação, copie `config.sheets.exemplo.toml` para `config.sheets.toml`
e preencha o ID que aparece entre `/d/` e `/edit` na URL da planilha.

## Configuração do Google, uma única vez

1. No [Google Cloud Console](https://console.cloud.google.com/), crie ou selecione
   um projeto e [ative a Google Sheets API](https://console.cloud.google.com/apis/library/sheets.googleapis.com).
2. Abra **IAM e administrador → Contas de serviço → Criar conta de serviço**.
   Escolha um nome como `velas-planilha`. Não é necessário conceder um papel de
   administrador do projeto; o acesso ao arquivo será dado pelo compartilhamento.
3. Na conta criada, abra **Chaves → Adicionar chave → Criar nova chave → JSON**.
   Salve o arquivo em `dados/google-service-account.json` dentro deste projeto.
   No Linux: `chmod 600 dados/google-service-account.json`.
   Não cole o conteúdo da chave no chat nem o inclua no Git. A pasta `dados/`
   já é ignorada pelo projeto.
4. Copie o endereço de e-mail da conta de serviço, semelhante a
   `velas-planilha@seu-projeto.iam.gserviceaccount.com`.
5. Na planilha do Google Sheets, clique em **Compartilhar**, adicione esse
   endereço como **Editor** e mantenha o acesso geral como **Restrito**.
   A chave por si só não concede acesso à planilha.

Esse fluxo usa uma conta de serviço para acessar um documento compartilhado,
conforme a [documentação de credenciais do Google](https://developers.google.com/workspace/guides/create-credentials).
Não é necessário configurar OAuth de aplicativo desktop para esta versão.
Se sua organização impedir a criação de chaves, será necessário adaptar a
autenticação; não remova políticas da organização para executar o teste.

## Executar

Na pasta do projeto, instale as dependências no ambiente virtual existente:

```bash
.venv/bin/python -m pip install -r requirements.txt
.venv/bin/python -m velas_sheets conectar
.venv/bin/python -m velas_sheets ler --aba 'FC NOV' --inicio 1 --fim 8
.venv/bin/python -m velas_sheets preparar-teste
.venv/bin/python -m velas_sheets editar exemplos/sheets-teste.json
.venv/bin/python -m velas_sheets editar exemplos/sheets-teste.json --aplicar
.venv/bin/python -m velas_sheets ler --inicio 1 --fim 8
```

`conectar` comprova autenticação e leitura. `preparar-teste` comprova a escrita
criando uma cópia nativa de `FC NOV` chamada `TESTE CLI`, incluindo fórmulas,
validações, formato e dados existentes. Se a aba de teste já existe, preserva seu
conteúdo. O título A1 identifica o teste. As fórmulas copiadas podem continuar
consultando outras abas, como o saldo do mês anterior.

`editar` mostra os valores atuais e propostos. Só grava com `--aplicar`, em uma
única chamada de atualização, e depois relê as linhas para conferir os valores,
fórmulas, validações e formatos. Ao executar novamente o mesmo JSON, as células
que já possuem os valores desejados não são gravadas de novo.

Para testar uma alteração após o primeiro lançamento, mude `pagamento` na linha
3 do JSON de `-15,00` para `-16,00`, gere a prévia e aplique. Isso edita a linha
existente. Os valores do exemplo são fictícios e ficam identificados como teste.

## Padrão do modelo

Cabeçalhos em A2:G2; lançamentos nas linhas 3 a 96:

| Campo no JSON | Coluna | Valor esperado |
| --- | --- | --- |
| `linha` | — | Número explícito entre 3 e 96 |
| `data` | A — Data | `AAAA-MM-DD` |
| `descritivo` | B — Descritivo | Texto |
| `centro_custo` | C — Centro de custo | Opção existente na validação da célula |
| `vencimento` | D — Vencimento | `AAAA-MM-DD` |
| `previsto` | E — Previsto R$ | Número, por exemplo `-15,50` ou `-15.50` |
| `pagamento` | F — Receita/Pagamento | Número, negativo para saída de caixa |

G — Saldo R$ é calculado pela fórmula existente e não é editável pela CLI.
Datas são gravadas como números seriais do Sheets, mantendo o formato visual
do modelo. Valores monetários são células numéricas, e descrições são texto
literal, mesmo que comecem com `=`. Não use separadores de milhar nos valores.

Campos omitidos permanecem como estavam; `null` limpa somente o campo indicado.
O exemplo omite Centro de custo: a lista atual do modelo contém títulos de
grupos e ainda não tem as categorias detalhadas das compras cadastradas.
A CLI consulta a validação ao alterar esse campo e rejeita opções fora da lista.
Não cria categorias nem define uma classificação contábil automaticamente.

Antes da gravação, os valores e metadados anteriores são salvos em
`dados/sheets_backups/`, com permissão local restrita. Essa cópia serve para
inspeção e recuperação manual; não há comando de restauração nesta versão.

## Limites deste protótipo

- Escrita restrita à aba configurada com prefixo `TESTE `; não lança compras reais
  nas abas mensais nem importa CSV. O JSON permite até 20 linhas por execução.
- Não insere linhas, não altera totais e não sobrescreve fórmulas ou chips.
- A releitura detecta mudanças ocorridas durante o preparo, mas não é um bloqueio
  transacional contra edição simultânea. Use este teste com um operador por vez.
- Em falha de rede durante a gravação, confira as linhas antes de repetir.
- Erro 403: confira a ativação da API e a permissão de Editor. Erro 404: confira
  o ID e se a planilha foi compartilhada com a conta de serviço.
- A importação futura precisará mapear o CSV real da Shopee, identificar pedidos
  sem duplicar lançamentos e distinguir compra, vencimento e pagamento.

A integração anterior com Mercado Livre continua disponível em `velas_ml`.

## Testes locais

```bash
.venv/bin/python -m unittest discover -s tests -v
```

Os testes da CLI usam um cliente simulado para verificar prévia sem escrita,
valores numéricos, preservação de fórmulas e validações e rejeição de entradas
inválidas. A autenticação local real exige a chave e o compartilhamento acima.

Validação em 21/11/2026: os 33 testes do projeto passaram. Pela conexão do
Google Drive disponível na sessão, foi criada `TESTE CLI` e aplicadas as dez
alterações geradas pelo código da CLI para o JSON de exemplo, nas linhas 3 e 4.
O mesmo código conferiu os valores gravados e a preservação das fórmulas,
validações e formatos. O saldo em G4 ficou em -20,00. Uma nova prévia com os
mesmos dados não gera gravações. `FC NOV!A1:G97` e `DRE!V3:V80` permaneceram
iguais aos valores e metadados lidos antes do teste.

Essa verificação utilizou a conexão da sessão, não a autenticação local da CLI,
que ainda depende da conta de serviço. A conferência de apresentação foi feita
pelos metadados da API; não houve inspeção visual no navegador. As fórmulas
originais do modelo foram preservadas, inclusive o saldo zero exibido em linhas
vazias. A aba de teste já está pronta para os próximos comandos.

## Compras parceladas: cadastro e pagamento

A CLI agora também oferece `compra`, `parcelas`, `pagar` e `retomar`.
O cadastro manual por JSON antecede a importação de CSV, que continua fora desta
versão. Cada produto gera uma linha por parcela. O valor informado é o **total
final da linha de produto**, já multiplicado pela quantidade e com os descontos,
frete e juros que você atribuiu a ela. Não informe o preço unitário nesse campo.

### Configuração anual

`planilha_id` recebe a aba auxiliar **Parcelas**, que concentra todos os anos.
Opcionalmente, `principal_id` em `[google_sheets]` escolhe outro arquivo para esse
histórico. Acrescente ao TOML uma seção separada:

```toml
[anos]
"2026" = "ID_DA_PLANILHA_2026"
"2027" = "ID_DA_PLANILHA_2027"
```

Cada ano precisa de um arquivo diferente. Compartilhe todos os arquivos,
inclusive o principal, com a conta de serviço como Editor. A CLI não cria arquivos
anuais. Um ano ausente bloqueia o planejamento antes de qualquer gravação.

A configuração local `config.sheets.teste.toml` aponta para duas cópias criadas
para validação. Use-a inicialmente. Elas preservam os dados do modelo copiado;
a cópia de 2027 não é um exercício novo com saldos zerados. Para uso real, prepare
os arquivos anuais e seus saldos iniciais antes de configurar seus IDs.

### Cadastrar uma compra

Edite `exemplos/compra-parcelada.json`. O exemplo representa R$ 90 em três
parcelas, com compra em janeiro e primeiro vencimento em fevereiro. O centro de
custo usado no exemplo é uma opção existente do modelo, somente para teste;
substitua pela categoria apropriada já cadastrada na validação da planilha.

```bash
.venv/bin/python -m velas_sheets --config config.sheets.teste.toml compra exemplos/compra-parcelada.json
.venv/bin/python -m velas_sheets --config config.sheets.teste.toml compra exemplos/compra-parcelada.json --aplicar
.venv/bin/python -m velas_sheets --config config.sheets.teste.toml parcelas --compra teste-janeiro
```

`id` identifica a compra; cada produto também tem um `id` próprio. Use letras
ASCII, números, hífen ou sublinhado, até 64 caracteres. A chave da parcela será,
por exemplo, `teste-janeiro:cera:001`. Guarde os IDs: mudá-los cria outra compra.
Repetir o cadastro idêntico não duplica dados, mesmo após pagamentos. Alterar
produtos, valores, classificação ou calendário de um ID existente gera conflito.

O cadastro aceita até 100 produtos e de 1 a 120 parcelas mensais. Todos os produtos
do pedido compartilham o calendário. A divisão usa centavos inteiros e distribui
os centavos restantes nas primeiras parcelas. R$ 100 vira R$ 33,34 + R$ 33,33 +
R$ 33,33. Se o primeiro vencimento for dia 31, fevereiro usa o último dia do mês
e março volta ao dia 31. Uma compra à vista pode ser cadastrada com uma parcela;
a confirmação do pagamento continua separada.

### Confirmar um pagamento

```bash
.venv/bin/python -m velas_sheets --config config.sheets.teste.toml pagar teste-janeiro:cera:001 --data 2026-03-15
.venv/bin/python -m velas_sheets --config config.sheets.teste.toml pagar teste-janeiro:cera:001 --data 2026-03-15 --aplicar
.venv/bin/python -m velas_sheets --config config.sheets.teste.toml parcelas --status paga
.venv/bin/python -m velas_sheets --config config.sheets.teste.toml parcelas --desde 2026-02-01 --ate 2026-04-30
```

A baixa é integral, pelo valor previsto. Pagamento no mês do vencimento atualiza
`Data` e `Receita/Pagamento` na linha existente. Em outro mês, a previsão original
permanece e uma linha de realização é criada no mês efetivamente pago, com
`Previsto` vazio. Não há dupla saída de caixa. A data original da compra e o
vencimento ficam preservados na aba Parcelas. A coluna Data da linha de previsão
usa a data da compra; a linha com pagamento usa a data efetiva.

Os estados são `pendente`, `atrasada` e `paga`. A aba calcula o estado por fórmula;
a consulta da CLI calcula na data local da execução. Uma baixa repetida com a mesma
data não gera outra gravação. Outra data para uma parcela já paga gera conflito.
Não estão implementados pagamentos parciais, cancelamentos, estornos, renegociação,
correções de compras ou rateio automático de frete/juros/descontos.

### Preservação e retomada

As abas mensais mantêm as sete colunas visíveis e reservam H:I, ocultas, para ID
e tipo do lançamento. Se H:I já contiverem dados, notas, chips ou validações, a
operação é bloqueada. Cabeçalhos e fórmulas de saldo são conferidos; o total é
localizado pela fórmula `SUM(F3:F...)` para respeitar alterações de tamanho do
modelo (como janeiro, cujo total está atualmente na linha 96). Linhas manuais
não são usadas como destino. Não há inserção automática de linhas; se o espaço
acabar, amplie o modelo e revise suas fórmulas antes de prosseguir.

A aba Parcelas nasce com 2.000 linhas, cabeçalho congelado, filtro e formatos de
data/valor. Se atingir a capacidade, será preciso expandi-la manualmente. A CLI
suporta leitura de até 10.000 linhas nessa aba. Não altere manualmente os IDs,
campos gerenciados ou fórmulas de status. Alterações em lançamentos mensais
controlados são detectadas como conflitos, em vez de sobrescritas.

Antes de aplicar, todos os destinos são verificados. O diretório
`dados/parcelas_operacoes/` guarda os valores e metadados anteriores, o estado
esperado e os pedidos de gravação, com acesso local restrito. A gravação é atômica
por arquivo, não entre arquivos. O arquivo principal é concluído por último.

```bash
.venv/bin/python -m velas_sheets --config config.sheets.teste.toml retomar
.venv/bin/python -m velas_sheets --config config.sheets.teste.toml retomar --aplicar
```

Após falha, a CLI bloqueia novas operações de escrita até a retomada. `retomar`
compara cada arquivo com os estados anterior e esperado: reconhece uma gravação
já recebida mesmo quando a resposta se perdeu e executa somente o que falta.
Se houver outro estado, mantém o diário e pede revisão manual. Guarde o diretório
`dados/`; não apague um diário pendente para forçar outra importação.

Use um operador por vez. O bloqueio local impede duas gravações pela mesma pasta,
mas não bloqueia outras máquinas nem edições simultâneas no navegador. A releitura
reduz o risco; a API não fornece uma transação única entre todos os arquivos.

### Evidência do teste com duas planilhas

Foram criadas cópias **TESTE Parcelas - Financeiro 2026** e **2027** na pasta
ChatGPT do Drive. A compra fictícia `teste-virada`, em
`exemplos/compra-virada-ano.json`, distribui dois produtos (R$ 100 e R$ 10,01) em
seis lançamentos entre dezembro/2026, janeiro/2027 e fevereiro/2027. A parcela
`teste-virada:cera:001` recebeu uma baixa fictícia em 15/02/2027 para testar atraso.
Esse cenário simula datas futuras e não representa pagamentos reais.

Os pedidos foram gerados pelo planejador da CLI e enviados pelo conector da
sessão. A conferência usa o mesmo código de verificação da CLI sobre os dados
relidos do Google. A autenticação local por conta de serviço continua pendente.
A verificação visual fica limitada aos formatos e valores retornados pela API;
não houve inspeção no navegador.

Validação final: **48 testes locais passaram**, cobrindo também os comandos
anteriores. Nas cópias reais, a primeira parcela de R$ 33,34 permaneceu prevista
em dezembro/2026 e foi registrada uma única vez em fevereiro/2027; o total de
pagamentos desse mês ficou em -33,34. Replanejar a compra após a baixa produziu
zero gravações. A DRE mantém suas fórmulas originais: a seção de valores futuros
consulta a coluna E, e as linhas de receitas/despesas realizadas consultam F.
O teste não altera a classificação dos centros de custo nem reconstrói a DRE.
