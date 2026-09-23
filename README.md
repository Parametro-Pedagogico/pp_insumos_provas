# pp_insumos_provas

Insumos para aplicação de provas: geração das **capas / cartões-resposta personalizados** de cada aluno
(dados do aluno, QR Code, caderno sorteado e grade de respostas pronta para leitura automática).

> **Dados pessoais não entram neste repositório.** Planilhas de alunos, capas geradas, manifestos,
> sorteio e registro de códigos ficam fora do Git (ver `.gitignore`). Os únicos dados aqui são fictícios
> (`exemplos/planilha_exemplo.csv`) e o modelo do Canva está anonimizado.

## Estrutura

```
capas_prova/            código
  gerar.py              planilha -> uma capa (PDF) por aluno + manifesto + verificação
  sorteio.py            sorteio balanceado do caderno de cada aluno (por turma, escola e total)
  nomes.py              padronização dos nomes ("MARIA DA SILVA" -> "Maria da Silva")
  codigos.py            código único do cartão (conteúdo do QR), com dígito verificador
  modelo.py             limpa a página do Canva e desenha grade, marcadores e rótulo do caderno
  verificar.py          planilha de verificação (quantitativos por escola, sorteio, nomes)
  leitor.py             protótipo de leitura do cartão (marcadores + QR + bolhas)
  simular.py            teste de leitura simulada (scanner, foto, P&B, caneta azul/preta)
config.yaml             colunas da planilha, cadernos, sorteio, layouts das grades, cores, nomes
modelos/                PDF do Canva (anonimizado) com os 3 modelos visuais (2º, 5º e 9º ano)
fonts/                  fontes embutidas nas capas (licença SIL OFL)
exemplos/               planilha fictícia para testes
ferramentas/            anonimizar_modelo.py (preparar um novo PDF do Canva para o repositório)
tests/                  testes automatizados (pytest)
dados/                  (não versionado) coloque aqui a planilha real
```

## Instalação

Python 3.10 ou mais novo. Em Debian/Ubuntu instale antes `sudo apt install python3-full python3-venv`.

```bash
python3 -m venv .venv
source .venv/bin/activate            # Windows: .venv\Scripts\activate
python -m pip install -r requirements.txt
```

## Uso

```bash
# 0) conferir a base e fazer o sorteio (sem gerar PDFs) -> saida/verificacao_previa.xlsx
python -m capas_prova.gerar --planilha dados/Base.xlsx --saida saida --validar

# 1) gerar as capas (tudo, ou por partes na mesma pasta de saída)
python -m capas_prova.gerar --planilha dados/Base.xlsx --saida saida
python -m capas_prova.gerar --planilha dados/Base.xlsx --saida saida --escola "NOME DA ESCOLA"
python -m capas_prova.gerar --planilha dados/Base.xlsx --saida saida --caderno C0201,C0202
python -m capas_prova.gerar --planilha dados/Base.xlsx --saida saida --limite 10      # teste rápido

# 2) refazer a verificação a qualquer momento (ex.: depois de gerar escola por escola)
python -m capas_prova.verificar --planilha dados/Base.xlsx --saida saida

# 3) (opcional) teste de leitura simulada
python -m capas_prova.simular --saida saida --imagens saida/simulacao
```

Para experimentar sem dados reais: `--planilha exemplos/planilha_exemplo.csv`.

### Saídas (pasta `saida/`, fora do Git)

| arquivo | conteúdo |
|---|---|
| `capas/<ESCOLA>/<TURMA>/<CADERNO>_<RA>_<NOME>.pdf` | uma capa por aluno |
| `manifesto.csv` | código do cartão → aluno, caderno, arquivo (base para juntar capa + caderno e para a leitura) |
| `sorteio_cadernos.csv` | caderno sorteado de cada aluno |
| `codigos_registrados.csv` | códigos de cartão já emitidos |
| `verificacao.xlsx` | resumo por escola, sorteio por turma, totais por caderno, conferência de nomes |
| `layouts/<CADERNO>.json` | posição de cada bolha e marcador (usado pelo leitor) |
| `relatorio.txt` | avisos: linhas ignoradas, textos reduzidos/quebrados, RA repetido |

> **Faça backup de `sorteio_cadernos.csv` e `codigos_registrados.csv`** (fora do GitHub, pois contêm RAs).
> São eles que garantem que, ao gerar de novo, ninguém muda de caderno nem de código de cartão.

## Regras principais

**Planilha.** Formato do export "Alunado" (Marca, Unidade, Série, Turma, RA, Nome do aluno…). Como cada
campo da capa é montado fica em `campos` no `config.yaml` (ex.: ESCOLA = `{Marca} - {Unidade}`).

**Sorteio do caderno.** Cada aluno recebe um caderno da sua série (`cadernos_por_serie`). Dentro de cada
turma os alunos são embaralhados e recebem os cadernos alternadamente (diferença máx. de 1 por turma); nas
turmas ímpares o aluno extra vai para o caderno com menos alunos na escola (diferença de 0 ou 1 por escola
e no total). O sorteio é feito sobre a base inteira, é reprodutível (`sorteio.semente`) e fica salvo:
alunos já sorteados não mudam; alunos novos entram no caderno que equilibra a turma.
`--refazer-sorteio` sorteia tudo de novo (capas de caderno não sorteado são apagadas da saída).
Uma coluna "Caderno" preenchida na planilha é respeitada.

**Nomes.** Inicial maiúscula em cada palavra, partículas (de, da, do, das, dos, e) minúsculas, limpeza de
espaços e pontuação. O que a regra não resolve sozinha (letra isolada, texto entre parênteses, RA repetido)
vai para a aba "Conferir" da verificação. Textos longos diminuem até 10,5 pt e depois quebram em 2 linhas.

**Código do cartão (QR).** 9 caracteres (8 + dígito verificador), sem I/L/O/U; determinístico por
(edição, RA, caderno). Mudar `edicao` no `config.yaml` a cada aplicação.

**Leitura automática.** Grade em vermelho (cor de *dropout*: some no canal vermelho da digitalização e
fica só a caneta); 4 marcadores ArUco distintos por layout (alinhamento, orientação e identificação do
layout mesmo sem QR). Alternativas para digitalização em cinza, impressão P&B, foto de celular e folha
invertida; se o QR falhar, o código impresso identifica o cartão.

## Novo modelo do Canva

1. Exporte o PDF do Canva e anonimize-o antes de colocar no repositório:
   `python ferramentas/anonimizar_modelo.py Capa_do_Canva.pdf modelos/Capa_Caderno_Prova.pdf`
2. Se o layout mudou, ajuste em `config.yaml` → `modelos` (página, área da grade, posição do QR etc.).
3. Novo caderno/nº de questões: `layouts` + `cadernos` + `cadernos_por_serie`.

## Testes

```bash
python -m pip install -r requirements-dev.txt
python -m pytest -q
```
