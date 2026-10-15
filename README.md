# Atividade: Processo de Análise de Requisitos para Geração de Código com LLM

## Nossa entrega — Processo PRECISA

| # | Entregável | Arquivo |
|---|---|---|
| 1 | Processo de análise de requisitos | [docs/1-processo.md](docs/1-processo.md) |
| 2 | Artefato — modelo do documento de requisitos (Markdown) | [docs/2-modelo-documento-requisitos.md](docs/2-modelo-documento-requisitos.md) |
| 3 | Instruções de preenchimento | [docs/3-instrucoes-preenchimento.md](docs/3-instrucoes-preenchimento.md) |
| 4 | Relatório técnico do experimento | [docs/4-relatorio-tecnico.md](docs/4-relatorio-tecnico.md) |
| — | Exemplo preenchido (objeto do experimento) | [experimento/especificacao/ERS-BIB-001.md](experimento/especificacao/ERS-BIB-001.md) |
| — | Kit do experimento (prompts, oráculo de testes, avaliador estatístico) | [experimento/](experimento/README.md) |

Verificação rápida (requer só Python ≥ 3.10):

```bash
python experimento/avaliacao/validar_oraculo.py
```

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
