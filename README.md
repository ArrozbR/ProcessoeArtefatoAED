# Atividade: Processo de Análise de Requisitos para Geração de Código com LLM

## Entregáveis

| # | Entregável | Arquivo |
|---|---|---|
| 1 | Processo ERL (Especificação de Requisitos para LLM), para microsserviços | [`entregaveis/1-processo.md`](entregaveis/1-processo.md) |
| 2 | Artefato: modelo do documento de requisitos | [`entregaveis/2-modelo-documento-requisitos.md`](entregaveis/2-modelo-documento-requisitos.md) |
| 3 | Instruções de preenchimento e checklist | [`entregaveis/3-instrucoes-preenchimento.md`](entregaveis/3-instrucoes-preenchimento.md) |
| 4 | Relatório técnico do experimento | [`entregaveis/4-relatorio-tecnico.md`](entregaveis/4-relatorio-tecnico.md) |
| 5 | Módulo de logística de entregas: visão completa (arquitetura, regras, fluxos) | [`entregaveis/5-modulo-logistica.md`](entregaveis/5-modulo-logistica.md) |

## Experimento: módulo de logística em microsserviços

| O quê | Onde |
|---|---|
| Documento de requisitos no modelo ERL (o que a LLM recebe) | `experimento/especificacoes/ERS-LOG-001.md` |
| Especificação narrativa (grupo de controle) | `experimento/especificacoes/logistica-narrativa.md` |
| Barramento de eventos (infraestrutura fornecida) | `experimento/infra/barramento.py` |
| Implementação de referência dos 5 serviços (valida o oráculo) | `experimento/referencia/servicos/` |
| Oráculo: um teste HTTP por critério de aceitação; harness que sobe os serviços como processos | `experimento/testes/` |
| Geração, registro e medição | `experimento/executar.py` |
| Estatística | `experimento/analisar.py` |
| Dados brutos e código gerado | `experimento/resultados/` |

Para reproduzir (Windows):

```bash
cd experimento
py -m venv .venv
.venv/Scripts/python -m pip install -r requirements.txt
.venv/Scripts/python -m pytest testes -q          # sobe os 6 processos e roda os 77 critérios contra a referência
.venv/Scripts/python executar.py validar          # oráculo: referência 100%, sistema vazio 0%, mutantes detectados
.venv/Scripts/python executar.py registrar        # avalia o código gerado em resultados/<condicao>/<NN>/servicos/
.venv/Scripts/python analisar.py                  # gera resultados/resumo.md
```

O código gerado pela LLM é executado localmente pelos testes. Rode numa máquina que você controla.

## Demonstração

Sobe os 5 microsserviços, o barramento e um gateway com uma página web para mostrar o fluxo completo: cotação, escolha da transportadora, atualizações da transportadora, rastreamento, notificações e eventos.

```bash
cd experimento
.venv/Scripts/python demo/subir.py                       # roda o código gerado pela LLM (resultados/erl/01)
.venv/Scripts/python demo/subir.py --alvo referencia     # ou a implementação de referência
```

Abra http://127.0.0.1:8000. O Swagger de cada serviço fica em `http://127.0.0.1:8101/docs` a `8105/docs` (endereços impressos no terminal). Ctrl+C encerra tudo.

## Estudo preliminar

O primeiro ciclo do processo usou um sistema pequeno (biblioteca, módulo único) e está arquivado em [`estudo-preliminar/`](estudo-preliminar/), com o relatório próprio. O resultado (100% em todas as execuções, nas duas condições) motivou a troca por um sistema maior e distribuído.

Decisões de projeto: [`docs/decisoes/`](docs/decisoes/).

---

## Descrição da Atividade

Cada grupo deverá:

- Desenvolver e descrever um processo de análise de requisitos;
- Desse processo, deve surgir um padrão de documento (formato em Markdown);
- Esse documento, se utilizado em uma IA LLM, deve gerar o código de acordo com o documento de requisitos com conformidade **igual ou superior a 85%**.

## Entregáveis

Ao final, o grupo deve entregar:

1. **Processo**;
2. **Artefato** (modelo do documento de requisitos);
3. **Instruções** para o preenchimento do documento de requisitos;
4. **Relatório técnico do experimento** (método científico de teste de hipótese) realizado com o modelo, que apresente o resultado de conformidade entre documento e produto de software igual ou superior a 85%.

---

## Objetivo Geral

Desenvolver um processo estruturado de análise e especificação de requisitos capaz de gerar um documento padronizado em Markdown, utilizável por uma LLM para produzir software com conformidade mínima de 85% em relação aos requisitos definidos.

## Objetivos Específicos

- Estruturar um processo de análise de requisitos.
- Criar um modelo padronizado de documento de requisitos.
- Definir instruções claras para seu preenchimento.
- Utilizar LLMs para geração de código a partir da especificação.
- Estabelecer critérios e métricas de conformidade.
- Aplicar método científico para testar a hipótese proposta.
- Analisar os resultados e propor melhorias no processo e no artefato.

---

## Metodologias

- Aprendizagem Baseada em Projetos.
- Aprendizagem Baseada em Problemas.
- Aprendizagem Colaborativa.
- Experimentação prática.
- Método científico.
- Design iterativo com ciclos de teste, análise e melhoria.

---

## Capacidades

### Capacidades Básicas

- Interpretação e análise de problemas.
- Raciocínio lógico e crítico.
- Comunicação e documentação técnica.
- Pesquisa e organização de informações.
- Análise de dados e resultados.
- Resolução de problemas.

### Capacidades Técnicas

- Engenharia e análise de requisitos.
- Especificação e validação de requisitos.
- Definição de requisitos funcionais, não funcionais e critérios de aceitação.
- Documentação em Markdown.
- Engenharia de prompts e contexto para LLMs.
- Geração e avaliação de código com IA.
- Testes e validação de software.
- Definição de métricas de conformidade.
- Método científico aplicado à Engenharia de Software.

### Capacidades Socioemocionais

- Trabalho em equipe.
- Comunicação e colaboração.
- Autonomia e responsabilidade.
- Gestão do tempo.
- Tomada de decisão.
- Criatividade e adaptabilidade.
- Pensamento crítico e abertura à revisão de hipóteses.

---

## Benefícios para a Sociedade

A atividade contribui para a formação de profissionais capazes de desenvolver softwares com maior qualidade, confiabilidade e aderência às necessidades dos usuários. Também estimula o uso responsável da Inteligência Artificial, a redução de erros e retrabalho, o aumento da produtividade no desenvolvimento de software e a criação de processos mais eficientes de interação entre pessoas, requisitos e sistemas de IA.
