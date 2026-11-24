# Integrações da planilha financeira

**Google Sheets — compras parceladas:** veja [o guia](docs/GOOGLE_SHEETS.md) para
cadastrar produtos, gerar parcelas por mês/ano, confirmar pagamentos e retomar
gravações interrompidas. A CLI também mantém os comandos iniciais de conexão e
edição da aba `TESTE CLI`. A importação do CSV da Shopee permanece uma próxima etapa.

## Mercado Livre → Planilha Financeira

Integração em Python das **compras da conta autorizada** com o arquivo
`Planilha Financeira( atual 07_2026).xlsx`, conforme `docs/documentacao.md`,
`Termo_de_Abertura_de_Projeto.md` e `integracao_mercado_livre`.

O programa consulta a API, guarda as compras em SQLite e atualiza a aba
**Mercado Livre** em `saidas/Planilha Financeira - integrada.xlsx`.
Essa cópia passa a ser o arquivo de trabalho: as próximas execuções usam sua
versão mais recente, inclusive as alterações feitas nas outras abas.
O arquivo original serve de modelo somente na primeira execução.

**Estado atual:** implementação e testes locais concluídos; cópia integrada
preparada sem compras fictícias. A conexão real depende do cadastro do aplicativo,
da autorização da conta e da confirmação de acesso aos recursos de comprador.
O agendamento está disponível como exemplo, mas ainda não foi ativado.

## Instalação

Requer Python 3.11 ou superior. No Linux, dentro da pasta do projeto:

```bash
python3 -m venv .venv
.venv/bin/python -m pip install -r requirements.txt
cp config.exemplo.toml config.toml
```

No Windows, use `py -m venv .venv` e `.venv\Scripts\python.exe` no lugar de
`.venv/bin/python`. O agendamento no Windows pode usar o Agendador de Tarefas,
com a pasta do projeto como diretório de trabalho.

Edite `config.toml`. Os caminhos são relativos à localização desse arquivo.
O período inicial está configurado para 2026; ajuste `desde` e `ate` ao histórico
que deseja importar. A API pode limitar a disponibilidade de pedidos antigos;
a base local preserva os pedidos que já foram importados.

Para preparar a planilha sem acesso à API:

```bash
.venv/bin/python -m velas_ml preparar
```

## Cadastrar e autorizar o aplicativo

1. Entre no [portal de desenvolvedores do Mercado Livre](https://developers.mercadolivre.com.br/)
   com a conta titular e acesse a área **Meus aplicativos** para criar o aplicativo.
2. Cadastre uma URL de retorno HTTPS fixa, sob seu controle. O portal exige HTTPS.
   Ela deve ser exatamente a mesma em `redirect_uri`; ative PKCE e use S256.
   Consulte o [guia oficial de criação de aplicativos](https://developers.mercadolivre.com.br/en_us/javascript/register-your-application).
3. Configure permissões de leitura para os recursos usados e acesso offline para
   renovação dos tokens. A disponibilidade depende do aplicativo e da conta.
   Esta integração não precisa alterar anúncios, pedidos ou pagamentos.
4. Copie o App ID para `client_id` em `config.toml` e preencha `redirect_uri`.
   Guarde a Secret Key localmente, fora do código e da planilha.
5. Gere o link:

   ```bash
   .venv/bin/python -m velas_ml autorizar
   ```

6. Abra o link no navegador, entre na conta cujas **compras** deseja importar e
   autorize o aplicativo. Após o redirecionamento, copie a URL completa com
   `code` e `state`. A URL deve apontar para uma página HTTPS sua; o programa
   não hospeda essa página nem configura seu domínio.
7. Conclua dentro de 30 minutos, enquanto o código ainda estiver válido:

   ```bash
   .venv/bin/python -m velas_ml concluir-autorizacao
   ```

   O terminal solicita a Secret Key e a URL de retorno com entrada oculta.
   Não cole esses valores no chat. O programa valida `state`, troca o código
   pelos tokens e os salva em `dados/tokens.json`, com acesso restrito no Linux.
   Se o código expirar antes disso, gere outro link.

O fluxo implementa a [autorização OAuth oficial](https://developers.mercadolivre.com.br/pt_br/realizacao-de-testes/autenticacao-e-autorizacao),
incluindo PKCE e substituição do refresh token após a renovação. Para renovar
automaticamente, a variável `MELI_CLIENT_SECRET` precisa estar disponível para o processo.

## Sincronizar

No Bash, informe a chave sem gravá-la literalmente no histórico:

```bash
read -rsp 'Secret Key do aplicativo: ' MELI_CLIENT_SECRET
export MELI_CLIENT_SECRET
.venv/bin/python -m velas_ml sincronizar
```

Feche a planilha antes de executar. O programa identifica a conta por `/users/me`
e consulta `/orders/search?buyer=...`, recurso de comprador descrito na
[documentação de pedidos](https://developers.mercadolivre.com.br/pt_br/pedidos-e-opinioes).
Depois consulta os detalhes, categorias e envios.

O período inteiro é consultado novamente, inclusive pedidos cancelados, para
atualizar os registros já importados. Uma chave estável evita duplicatas.
Se a API negar o acesso às compras, a importação para e informa o HTTP recebido.
Autenticação bem-sucedida, sozinha, não comprova acesso ao histórico de compras.

## Usar a planilha

A aba **Mercado Livre** contém produto, categoria, quantidade, preço unitário,
subtotal, frete rateado, total, pedido, data, status e pendências. Identificadores
são texto para preservar os dígitos. Os valores monetários são células numéricas.

As colunas editáveis são:

| Coluna | Uso |
| --- | --- |
| Incluir | Use `Não` para retirar uma compra pessoal da visualização padrão. |
| Centro de custo | Classificação interna, independente da categoria do Mercado Livre. |
| Observações | Anotações que serão preservadas nas próximas execuções. |

Salve e feche o Excel; depois execute `exportar` ou `sincronizar` para persistir
essas escolhas na base local. O filtro padrão mostra somente `Incluir = Sim`.
Limpe o filtro para ver as exclusões e alterar uma linha de volta para `Sim`.
Não apague linhas para excluir compras: sem a marcação, elas serão reimportadas.
Não edite as chaves nem renomeie a aba/colunas da integração.

Também é possível excluir/restaurar um pedido inteiro pelo terminal:

```bash
.venv/bin/python -m velas_ml ignorar NUMERO_DO_PEDIDO
.venv/bin/python -m velas_ml restaurar NUMERO_DO_PEDIDO
.venv/bin/python -m velas_ml exportar
```

Exclusões pelo comando `ignorar` são restauradas pelo comando `restaurar`.
O histórico é mantido para que compras excluídas não reapareçam após uma consulta.

**As abas FC JAN–FC DEZ, DRE e Precificação não recebem lançamentos nesta versão.**
A documentação pede detalhes das compras; o modelo financeiro usa centros de custo
e datas de pagamento, que não equivalem às categorias e datas de criação do pedido.
O próximo passo para alimentar a DRE é definir esse mapeamento e o tratamento de
parcelas, descontos, cancelamentos e estornos. Os valores importados representam
itens comprados, não uma conciliação bancária ou o valor líquido de pagamentos.

## Regras de frete e dados incompletos

- O frete é o custo do **comprador**, `receiver.cost`, documentado em
  [custos de envio](https://developers.mercadolivre.com.br/pt_br/imovel-gerenciamento-de-pacotes/mercado-envios-custos-e-cotacoes).
- Divide-se igualmente entre as **linhas de produtos de uma entrega**. Três
  unidades do mesmo produto em uma linha contam como uma parte. Esta é a
  interpretação adotada para “dividido igualmente entre todos os produtos”.
- Exemplo: R$ 10,00 entre três linhas resulta em R$ 3,34, R$ 3,33 e R$ 3,33.
  O centavo restante é atribuído em ordem estável da chave da linha.
- Quando pedidos compartilham uma entrega, o frete é rateado uma única vez.
  Pedidos relacionados podem ser consultados e importados mesmo fora do período,
  para completar a composição da entrega.
- O rateio acontece antes de excluir compras pessoais. A parte pessoal do frete
  não é transferida para os itens do negócio.
- A composição do envio é conferida por pedido, produto, variação e quantidade.
  Se custo/composição não estiverem acessíveis, frete e total ficam **em branco**,
  com uma pendência explícita. Zero só é usado quando a API informa custo zero.
- Pedidos sem envio identificado ou com vários envios de ida ficam pendentes nesta
  versão. Envios de devolução não entram no rateio.
- Categoria indisponível mantém o ID, quando existente, e uma indicação de pendência.
  Compras fora de BRL interrompem a execução para evitar misturar moedas.

## Preservação do arquivo

O modelo contém gráficos e extensões de validação que uma gravação comum pelo
openpyxl removeria. O exportador altera somente o XML da aba da integração e,
na primeira criação, os três índices necessários para registrá-la. Os demais
componentes do XLSX são copiados byte a byte: gráficos, fórmulas, estilos e
validações existentes permanecem preservados.

A gravação usa arquivo temporário e substituição atômica. Cada atualização de uma
saída existente cria uma cópia anterior em `saidas/backups/`; esses backups devem
ser administrados conforme o espaço disponível. Se detectar edição concorrente
do Excel, o programa interrompe a exportação. A base pode já ter sido atualizada;
nesse caso, `exportar` conclui a gravação sem consultar a API novamente.

As fórmulas do modelo não são recalculadas pelo Python. Referências `#REF!` já
presentes no arquivo original não foram corrigidas por esta integração.

## Agendamento no Linux

Os exemplos em `agendamento/` usam systemd do usuário e o caminho
`~/Projetos/Velas`. Ajuste os caminhos se necessário. Só ative após concluir a
autorização e testar uma sincronização real.

Crie `.env` localmente com uma linha `MELI_CLIENT_SECRET=sua_chave` e restrinja
a leitura com `chmod 600 .env`. Este arquivo é lido pelo systemd, não pelo Python.
Não o versione. Com o aplicativo já autorizado:

```bash
mkdir -p ~/.config/systemd/user
cp agendamento/velas-mercado-livre.service ~/.config/systemd/user/
cp agendamento/velas-mercado-livre.timer ~/.config/systemd/user/
systemctl --user daemon-reload
systemctl --user enable --now velas-mercado-livre.timer
```

O temporizador executa a cada hora enquanto o serviço de usuário estiver ativo.
A máquina precisa estar ligada, com rede, e a planilha fechada. Uma execução
pendente é retomada quando o temporizador volta a ficar ativo.

```bash
systemctl --user list-timers velas-mercado-livre.timer
journalctl --user -u velas-mercado-livre.service
systemctl --user disable --now velas-mercado-livre.timer
```

## Validação e estrutura

```bash
.venv/bin/python -m unittest discover -s tests -v
```

Os testes usam respostas simuladas para OAuth, paginação, isolamento de conta,
rateio, dados ausentes, duplicatas, exclusões e preservação do XLSX.
Não substituem o teste real de permissões do aplicativo no Mercado Livre.

| Arquivo | Responsabilidade |
| --- | --- |
| `velas_ml/client.py` | OAuth, renovação de tokens, requisições e paginação. |
| `velas_ml/sync.py` | Pedidos, categorias, composição dos envios e rateio. |
| `velas_ml/store.py` | SQLite, atualizações e escolhas persistentes. |
| `velas_ml/excel.py` | Leitura das escolhas e atualização preservando o XLSX. |
| `velas_ml/__main__.py` | Comandos e bloqueio de execuções simultâneas. |
| `config.exemplo.toml` | Caminhos, período e identificação do aplicativo. |

Estoque, Shopee, TikTok Shop e WhatsApp permanecem etapas posteriores do projeto.

