# Entregável 4 — Relatório Técnico do Experimento

**Avaliação experimental do processo PRECISA: conformidade entre documento de requisitos e código gerado por LLM**

| Campo | Valor |
|---|---|
| Autores | ⟨PREENCHER: nomes do grupo⟩ |
| Versão | 0.9 — protocolo e instrumentação concluídos; resultados pendentes |
| Data | ⟨PREENCHER: data da execução⟩ |
| LLM avaliada | ⟨PREENCHER: nome, versão do modelo, interface⟩ |
| Kit de reprodução | [`experimento/`](../experimento/README.md) |

> **Como ler este relatório.** As seções 1 a 4 e 7 estão completas (planejamento, instrumentação e sua validação). As seções marcadas com ⟨PREENCHER⟩ dependem da execução do protocolo descrito em [experimento/README.md](../experimento/README.md); os números são gerados automaticamente em `experimento/resultados/resultados.md`. **Nenhum valor de conformidade deve ser escrito aqui sem ter sido produzido pelo avaliador.**

---

## Resumo

**Contexto.** LLMs geram código a partir de texto, mas preenchem lacunas da especificação com suposições, o que reduz a aderência do produto aos requisitos. **Objetivo.** Avaliar se um documento de requisitos produzido com o processo PRECISA permite que uma LLM gere software com conformidade igual ou superior a 85%. **Método.** Experimento controlado com um fator (formato da especificação) e dois tratamentos — documento PRECISA (A) e descrição livre (B) —, com n = 10 gerações independentes por tratamento. A conformidade de cada execução é a proporção de 70 critérios verificáveis (60 de aceitação e 10 não funcionais) atendidos, medida por um oráculo automatizado validado previamente (100% na implementação de referência; 17 de 17 mutantes detectados). A hipótese principal (H0: μ ≤ 0,85) é testada com Wilcoxon de postos sinalizados unilateral exato (α = 0,05). **Resultados.** ⟨PREENCHER: conformidade média de A e B, IC 95%, p-valores e decisão⟩. **Conclusão.** ⟨PREENCHER⟩.

## 1. Introdução

### 1.1 Contexto e problema

Na geração de código por LLM, o documento de requisitos deixa de ser apenas comunicação entre pessoas e passa a ser **o próprio contexto de execução** do gerador. Diferentemente de uma pessoa desenvolvedora, a LLM, por padrão, não pede esclarecimentos: ela completa ambiguidades e omissões com a interpretação que considera mais provável. Cada lacuna do documento torna-se, portanto, um ponto de possível não conformidade.

### 1.2 Objetivo

Desenvolver e avaliar experimentalmente um processo de análise de requisitos (PRECISA, ver [Entregável 1](1-processo.md)) e seu artefato ([Entregável 2](2-modelo-documento-requisitos.md)), verificando se o documento produzido leva uma LLM a gerar código com conformidade **≥ 85%**.

### 1.3 Questões de pesquisa

- **QP1.** Um documento elaborado com o PRECISA permite que uma LLM gere código com conformidade média superior a 85%?
- **QP2.** A conformidade obtida com o documento PRECISA é maior que a obtida com uma descrição livre do mesmo sistema?

## 2. Fundamentação

- **Qualidade de requisitos.** A ISO/IEC/IEEE 29148 define características de requisitos bem formados (necessário, inequívoco, completo, singular, consistente, verificável, factível, rastreável), usadas como checklist de inspeção do PRECISA.
- **EARS** (Mavin et al., 2009) propõe padrões sintáticos ("QUANDO… o sistema DEVE…") que reduzem ambiguidade em requisitos textuais; o PRECISA os adota na redação dos RF.
- **Especificação por exemplos e BDD** (North, 2006; Adzic, 2011): critérios de aceitação no formato Dado/Quando/Então, com valores concretos, servem simultaneamente de especificação e de teste. No PRECISA, eles são o elo entre documento, LLM e oráculo.
- **Palavras normativas** (RFC 2119): DEVE / NÃO DEVE / PODE dão força explícita a obrigações e proibições.
- **Avaliação de código gerado por LLM.** A prática consolidada é medir correção funcional por execução de testes (Chen et al., 2021). Este trabalho adota a mesma ideia, com granularidade por critério de aceitação.
- **Validação do oráculo por mutação** (Jia; Harman, 2011): um conjunto de testes é considerado sensível quando detecta versões deliberadamente defeituosas (mutantes) do programa.
- **Experimentação em Engenharia de Software** (Wohlin et al., 2012): estrutura de definição, planejamento, operação, análise e ameaças à validade seguida neste relatório.

## 3. Planejamento do experimento

### 3.1 Definição do objetivo (modelo GQM)

> **Analisar** o processo PRECISA e seu modelo de documento
> **com o propósito de** avaliar
> **com respeito à** conformidade do código gerado por LLM em relação aos requisitos
> **do ponto de vista** do analista de requisitos
> **no contexto de** um módulo de software de pequeno porte gerado integralmente por uma LLM, sem intervenção humana no código.

### 3.2 Hipóteses

| ID | Hipótese nula (H0) | Hipótese alternativa (H1) | Teste |
|---|---|---|---|
| **H1** (principal, QP1) | μ_A ≤ 0,85 | μ_A > 0,85 | Wilcoxon de postos sinalizados, uma amostra, unilateral, exato (primário); t de Student unilateral (secundário) |
| **H2** (secundária, QP2) | μ_A ≤ μ_B | μ_A > μ_B | Teste de permutação, duas amostras independentes, unilateral; tamanho de efeito: delta de Cliff |

Nível de significância α = 0,05 em cada hipótese. Como H1 é a hipótese principal e H2 é exploratória, não se aplica correção para comparações múltiplas; se for desejado controle familiar, use Bonferroni (α = 0,025).

**Critério de decisão para o objetivo da atividade:** o requisito "conformidade ≥ 85%" é considerado **demonstrado** quando H0 da hipótese H1 for rejeitada (p < 0,05). Também são reportados a média, o intervalo de confiança bootstrap de 95% e a quantidade de execuções individuais com C ≥ 0,85.

### 3.3 Variáveis

| Tipo | Variável | Valores / medida |
|---|---|---|
| Independente | Formato da especificação | **A** = ERS-BIB-001 no modelo PRECISA; **B** = descrição livre |
| Dependente | Conformidade da execução *i* | $C_i$ = critérios atendidos ÷ 70 |
| Controladas | LLM e versão do modelo; interface; prompt padrão; conversa nova sem memória; ausência de edição humana; ambiente de avaliação (Python ≥ 3.10); oráculo congelado | constantes em todas as execuções |

### 3.4 Objeto experimental

| Item | Tratamento A — [ERS-BIB-001](../experimento/especificacao/ERS-BIB-001.md) | Tratamento B — [descrição livre](../experimento/especificacao/descricao-livre.md) |
|---|---|---|
| Sistema | Módulo de empréstimos de biblioteca escolar (Python) | o mesmo |
| Conteúdo | 11 RF, 8 RNF, 8 RN, 8 convenções gerais, glossário, contrato de interface, 70 critérios com valores concretos, ordem de validação, fora do escopo, diretivas para a LLM | regras de negócio principais em prosa + a mesma interface (nomes de classes, campos, métodos e exceções) |
| Tamanho | 660 linhas | 41 linhas |

A descrição livre **contém a mesma interface** de propósito: sem os nomes, nenhum teste conseguiria sequer chamar o código, e a comparação mediria apenas "acertar nomes". Assim, H2 isola o efeito das demais práticas do PRECISA (critérios de aceitação, ordem de validação, convenções, glossário, valores-limite, fora do escopo).

### 3.5 Projeto experimental

- Um fator, dois tratamentos, amostras independentes.
- **n = 10 execuções por tratamento** (20 no total). Justificativa: com n = 10, o Wilcoxon exato atinge p mínimo de 1/1024 ≈ 0,001, suficiente para α = 0,05 mesmo com alguma variabilidade; é também um volume viável de execução manual.
- Ordem intercalada (A-01, B-01, A-02, …) para diluir variações do serviço da LLM ao longo do tempo.
- Unidade experimental: uma resposta da LLM em uma conversa nova.

### 3.6 Instrumentação

| Instrumento | Função |
|---|---|
| [`prompt-geracao.md`](../experimento/prompt-geracao.md) + [`montar_prompts.py`](../experimento/montar_prompts.py) | Prompt idêntico nos dois tratamentos; gera o texto exato a colar e o hash do documento |
| [`testes_aceitacao.py`](../experimento/avaliacao/testes_aceitacao.py) | Oráculo: um teste automatizado por critério (60 CA + 10 CV) |
| [`executor.py`](../experimento/avaliacao/executor.py) | Executa o oráculo em processo isolado, com tempo-limite de 30 s por teste |
| [`avaliar.py`](../experimento/avaliacao/avaliar.py) + [`estatistica.py`](../experimento/avaliacao/estatistica.py) | Extrai o código das respostas, calcula a conformidade, a estatística e gera os resultados |
| [`validar_oraculo.py`](../experimento/avaliacao/validar_oraculo.py) + [implementação de referência](../experimento/referencia/biblioteca.py) | Validação do instrumento de medida (seção 4.1) |
| [`registro.csv`](../experimento/execucoes/registro.csv) | Rastreabilidade de cada execução (LLM, versão, data, hash do documento) |

### 3.7 Métrica de conformidade

Um critério é **atendido** quando todos os testes associados a ele passam. A conformidade de uma execução é

$$C_i = \frac{\text{critérios atendidos}}{70}$$

Todos os critérios têm o mesmo peso. Uma resposta que não compila ou não contém código vale $C_i = 0$ e **não é descartada**.

### 3.8 Análise estatística

- **Descritiva:** n, média, desvio-padrão, mediana, mínimo, máximo, nº de execuções com C ≥ 0,85, conformidade média por requisito.
- **H1:** Wilcoxon de postos sinalizados unilateral **exato** contra 0,85 (primário). Escolhido porque $C_i$ é limitado a [0, 1] e tende a se concentrar perto do teto, o que viola a normalidade exigida pelo teste t. O teste t é reportado como análise de sensibilidade. Intervalo de confiança de 95% por bootstrap percentil (10.000 reamostras, semente fixa).
- **H2:** teste de permutação unilateral da diferença de médias (exato quando viável), com delta de Cliff como tamanho de efeito (|δ| ≥ 0,474 = grande).
- Toda a estatística é implementada com a biblioteca padrão do Python em `estatistica.py` (verificada contra valores tabelados da distribuição t: t = 2,262 com 9 gl → p unilateral = 0,0250).

## 4. Operação

### 4.1 Validação do instrumento de medida (realizada)

Antes de qualquer geração, o oráculo foi validado conforme a fase F5 do processo:

1. **Cobertura:** os 70 critérios da especificação têm teste correspondente, e não há teste sem critério (verificado automaticamente por `avaliar.py`).
2. **Correção:** a implementação de referência, escrita por humanos, atende **70/70 critérios (100%)**.
3. **Sensibilidade (teste de mutação):** 17 mutantes da referência, cada um com um desvio deliberado da especificação, foram submetidos ao oráculo. **Todos os 17 foram detectados** (escore de mutação 100%). Resultado completo em [`resultados/validacao-oraculo.md`](../experimento/resultados/validacao-oraculo.md).

| # | Desvio introduzido | Critérios que o detectaram |
|---|---|---|
| 1 | Prazo do ALUNO de 15 dias | 16 critérios (ex.: CA-05.1, CA-07.1, CA-11.2) |
| 2 | Limite do ALUNO de 4 empréstimos | CA-05.4, CA-05.11 |
| 3 | Multa sem teto | CA-06.3 |
| 4 | Atraso com ≥ em vez de > | CA-05.8 |
| 5 | ISBN sem remover hífens | CA-01.1, CA-01.6, CA-05.1 |
| 6 | Pesquisa diferenciando maiúsculas | CA-04.2 |
| 7 | "Indisponível" verificado antes do limite | CA-05.11 |
| 8 | Renovação somada à data prevista anterior | CA-07.1, CA-07.4, CA-07.7 |
| 9 | Até 3 renovações | CA-07.4 |
| 10 | Fila de reservas ignorada | CA-09.2 |
| 11 | Dinheiro em `float` | 15 critérios (ex.: CA-06.2, CV-08.1) |
| 12 | Pagamento acima do saldo aceito | CA-10.3, CV-07.1 |
| 13 | Atrasados sem desempate por id | CA-11.4 |
| 14 | `print()` no módulo | CV-03.2 |
| 15 | Método público sem docstring | CV-06.1 |
| 16 | Tipo de usuário sem conversão para maiúsculas | CA-02.1 |
| 17 | Devolução com data anterior aceita | CA-06.5, CV-07.1 |

**Conclusão parcial:** o instrumento mede o que se propõe a medir — aprova uma implementação conforme e reprova desvios pontuais, inclusive de valores-limite e de ordem de validação.

### 4.2 Execução das gerações

⟨PREENCHER após a execução, com base em `experimento/execucoes/registro.csv`:⟩

| Item | Valor |
|---|---|
| LLM / versão do modelo | ⟨…⟩ |
| Interface (web/API) e parâmetros | ⟨…⟩ |
| Período de execução | ⟨…⟩ |
| Execuções realizadas (A / B) | ⟨10 / 10⟩ |
| Hash SHA-256 (16 primeiros) do ERS-BIB-001 usado | ⟨…⟩ |
| Desvios do protocolo (ex.: "continue" por truncamento) | ⟨nenhum / descrever⟩ |
| Achados da revisão adversarial por LLM (F4) e correções feitas no documento | ⟨…⟩ |

## 5. Resultados

⟨PREENCHER: copiar de `experimento/resultados/resultados.md`.⟩

### 5.1 Conformidade por execução

| Execução | A — PRECISA | B — Descrição livre |
|---|---|---|
| 01 | ⟨…⟩ | ⟨…⟩ |
| … | ⟨…⟩ | ⟨…⟩ |
| 10 | ⟨…⟩ | ⟨…⟩ |

### 5.2 Estatística descritiva

| Grupo | n | Média | DP | Mediana | Mín. | Máx. | Execuções ≥ 85% | IC 95% (bootstrap) |
|---|---|---|---|---|---|---|---|---|
| A — PRECISA | ⟨…⟩ | ⟨…⟩ | ⟨…⟩ | ⟨…⟩ | ⟨…⟩ | ⟨…⟩ | ⟨…⟩ | ⟨…⟩ |
| B — Livre | ⟨…⟩ | ⟨…⟩ | ⟨…⟩ | ⟨…⟩ | ⟨…⟩ | ⟨…⟩ | ⟨…⟩ | ⟨…⟩ |

### 5.3 Testes de hipótese

| Hipótese | Estatística | p | Decisão |
|---|---|---|---|
| H1 — Wilcoxon (primário) | W+ = ⟨…⟩ | ⟨…⟩ | ⟨H0 rejeitada / não rejeitada⟩ |
| H1 — t de Student (sensibilidade) | t = ⟨…⟩ (gl = ⟨…⟩) | ⟨…⟩ | ⟨…⟩ |
| H2 — Permutação | Δ média = ⟨…⟩ p.p.; δ de Cliff = ⟨…⟩ | ⟨…⟩ | ⟨…⟩ |

### 5.4 Conformidade por requisito e critérios reprovados

⟨PREENCHER: tabela "Conformidade por requisito" e tabela "Critérios reprovados" de `resultados.md`, com a coluna **Causa (D/G/T)** preenchida pelo grupo.⟩

## 6. Discussão

⟨PREENCHER. Roteiro sugerido:⟩

1. **QP1:** a conformidade do tratamento A superou 85% com significância estatística? Quantas execuções individuais ficaram abaixo da meta, e por quê?
2. **QP2:** qual a diferença entre A e B? Em quais requisitos ela se concentra? (Hipótese a examinar: as maiores diferenças devem aparecer nos critérios que dependem de informação que só o PRECISA explicita — ordem de validação, valores-limite, normalização, arredondamento, atomicidade.)
3. **Causas-raiz:** proporção D/G/T entre os critérios reprovados. Falhas **D** apontam trechos do documento a reescrever; falhas **G** recorrentes indicam regras que precisam de mais destaque (ex.: mover para Convenções Gerais, usar NÃO DEVE).
4. **Variabilidade:** a dispersão entre execuções do mesmo tratamento indica quão determinística a especificação torna a geração.

## 7. Ameaças à validade

| Tipo | Ameaça | Mitigação adotada |
|---|---|---|
| **Interna** | O mesmo grupo escreveu a especificação, a referência e o oráculo; testes poderiam exigir algo não escrito. | Regra explícita "teste só verifica o que está escrito"; checagem automática de cobertura critério ↔ teste; validação por referência e mutantes; revisão do oráculo por membro que não o escreveu (recomendada). |
| **Interna** | Não determinismo da LLM. | n = 10 por tratamento; conversa nova a cada execução; teste estatístico em vez de execução única. |
| **Interna** | Contaminação entre execuções (memória da LLM, histórico). | Chat temporário, memória e instruções personalizadas desativadas. |
| **Interna** | Intervenção humana no código. | Proibida; extração automática do bloco de código pelo avaliador; resposta bruta arquivada. |
| **Construto** | Conformidade por testes é um *proxy* da aderência aos requisitos; critérios têm peso igual, embora tenham importâncias diferentes. | 70 critérios cobrindo todos os RF e RNF; conformidade por requisito também é reportada. |
| **Construto** | A linha de base (B) foi escrita pelos mesmos autores e pode ser mais pobre (ou mais rica) que uma descrição livre típica. | B contém a mesma interface e todas as regras de negócio principais; foi escrita antes de qualquer execução e é publicada para escrutínio. |
| **Construto** | O tratamento A é mais longo; parte do efeito pode vir do volume de informação, não da estrutura. | Limitação reconhecida: o PRECISA é justamente um processo para produzir a informação necessária; separar "estrutura" de "conteúdo" exigiria um terceiro tratamento (trabalho futuro). |
| **Externa** | Um único domínio, de pequeno porte, em Python, com uma única LLM. | Resultados válidos para módulos de porte semelhante; replicação com outros domínios, linguagens e LLMs proposta na seção 8. |
| **Conclusão** | Amostra pequena (n = 10). | Teste exato não paramétrico; IC bootstrap; tamanho de efeito reportado. |
| **Conclusão** | CV-04.1 (desempenho) depende da máquina de avaliação. | Limite folgado (5 s; a referência leva ≈ 0,5 s); registrar a máquina usada. |

## 8. Melhorias propostas

Melhorias já identificadas durante a construção do processo e do kit (a completar com as lições das causas D/G/T da seção 5.4):

1. **Automatizar a fase F6 via API**, com parâmetros fixos (temperatura, semente quando disponível), permitindo n maior e replicação com várias LLMs.
2. **Ponderar a conformidade por prioridade** (Must > Should > Could) como métrica complementar.
3. **Terceiro tratamento** com o mesmo conteúdo do PRECISA, porém sem estrutura (texto corrido), para separar o efeito da estrutura do efeito do conteúdo.
4. **Replicar em outros domínios e linguagens** (ex.: API REST em Java/TypeScript), adaptando apenas a seção de Contrato de Interface.
5. **Gerar o esqueleto do oráculo automaticamente** a partir dos critérios Dado/Quando/Então, reduzindo esforço e risco de testes que verificam algo não escrito.
6. **Concordância entre avaliadores** na classificação D/G/T (dois membros classificam de forma independente; reportar kappa de Cohen).

## 9. Conclusão

⟨PREENCHER: resposta objetiva às QP1 e QP2, com os números da seção 5, e a principal lição para o processo e o artefato.⟩

## Referências

- ADZIC, G. *Specification by Example: How Successful Teams Deliver the Right Software*. Shelter Island: Manning, 2011.
- BASILI, V. R.; CALDIERA, G.; ROMBACH, H. D. The Goal Question Metric Approach. In: *Encyclopedia of Software Engineering*. New York: Wiley, 1994.
- BRADNER, S. *RFC 2119: Key words for use in RFCs to Indicate Requirement Levels*. IETF, 1997.
- CHEN, M. et al. Evaluating Large Language Models Trained on Code. *arXiv:2107.03374*, 2021.
- CLIFF, N. Dominance statistics: Ordinal analyses to answer ordinal questions. *Psychological Bulletin*, v. 114, n. 3, p. 494–509, 1993.
- EFRON, B.; TIBSHIRANI, R. J. *An Introduction to the Bootstrap*. New York: Chapman & Hall, 1993.
- ISO/IEC/IEEE. *ISO/IEC/IEEE 29148:2018 — Systems and software engineering — Life cycle processes — Requirements engineering*. Geneva, 2018.
- ISO/IEC. *ISO/IEC 25010 — Systems and software engineering — Systems and software Quality Requirements and Evaluation (SQuaRE) — Product quality model*. Geneva.
- JIA, Y.; HARMAN, M. An Analysis and Survey of the Development of Mutation Testing. *IEEE Transactions on Software Engineering*, v. 37, n. 5, p. 649–678, 2011.
- MAVIN, A.; WILKINSON, P.; HARWOOD, A.; NOVAK, M. Easy Approach to Requirements Syntax (EARS). In: *17th IEEE International Requirements Engineering Conference (RE'09)*, 2009.
- NORTH, D. Introducing BDD. *Better Software*, 2006.
- WILCOXON, F. Individual Comparisons by Ranking Methods. *Biometrics Bulletin*, v. 1, n. 6, p. 80–83, 1945.
- WOHLIN, C. et al. *Experimentation in Software Engineering*. Berlin: Springer, 2012.

## Apêndice A — Reprodução

```bash
cd experimento
python avaliacao/validar_oraculo.py     # instrumento: referência 70/70 e 17/17 mutantes
python montar_prompts.py                # prompts congelados + hashes
# ... executar as 20 gerações conforme experimento/README.md ...
python avaliacao/avaliar.py             # gera resultados/resultados.md
```
