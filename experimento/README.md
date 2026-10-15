# Kit do experimento

Tudo o que é necessário para reproduzir o experimento descrito no [relatório técnico](../docs/4-relatorio-tecnico.md). Requer apenas **Python ≥ 3.10** (sem dependências externas).

## Estrutura

```text
experimento/
├── especificacao/
│   ├── ERS-BIB-001.md          # Tratamento A: documento no modelo PRECISA (objeto do experimento)
│   └── descricao-livre.md      # Tratamento B: linha de base, descrição livre com a mesma interface
├── prompt-geracao.md           # Prompt padrão, idêntico nos dois grupos
├── montar_prompts.py           # Gera prompts/prompt-<grupo>.txt (prompt + documento) e o hash do documento
├── prompts/                    # Prompts prontos para colar na LLM (gerados)
├── referencia/biblioteca.py    # Implementação de referência (humana) — valida o oráculo; NUNCA vai para a LLM
├── avaliacao/
│   ├── testes_aceitacao.py     # Oráculo: 1 teste por critério (60 CA + 10 CV) — NUNCA vai para a LLM
│   ├── executor.py             # Roda o oráculo contra uma implementação e emite JSON
│   ├── validar_oraculo.py      # Referência deve dar 100%; 17 mutantes devem ser detectados
│   ├── avaliar.py              # Avalia todas as execuções, calcula estatística e gera resultados/
│   └── estatistica.py          # Wilcoxon exato, t de Student, bootstrap, permutação, delta de Cliff
├── execucoes/
│   ├── registro.csv            # Uma linha por execução (preenchida pelo operador)
│   ├── A-precisa/run-01/…      # Respostas da LLM para o tratamento A
│   └── B-livre/run-01/…        # Respostas da LLM para o tratamento B
└── resultados/                 # Saídas geradas pelos scripts (não editar à mão)
```

## Protocolo

### 0. Antes do experimento (uma única vez)

1. **Concluir a fase F4 no ERS-BIB-001.** Um membro do grupo que não é o autor faz a inspeção com o [checklist](../docs/3-instrucoes-preenchimento.md#7-checklist-de-qualidade-inspeção-na-fase-f4) e executa a [revisão adversarial por LLM](../docs/3-instrucoes-preenchimento.md#8-revisão-adversarial-por-llm-fase-f4) numa sessão nova. Trate os achados, registre-os no histórico (seção 14), marque o item do DoR e mude o status para **Aprovado**.
2. **Revalidar o oráculo** se o documento mudou:
   ```bash
   python avaliacao/validar_oraculo.py
   ```
   Critério de saída: referência 70/70 **e** 17/17 mutantes detectados. Se um critério novo foi criado, escreva o teste correspondente antes.
3. **Congelar** o documento: gere os prompts e anote os hashes impressos.
   ```bash
   python montar_prompts.py
   ```
   A partir daqui, `especificacao/`, `prompt-geracao.md` e `avaliacao/` **não podem mudar** até o fim das execuções. Recomenda-se um commit/tag (`git tag experimento-congelado`).
4. **Escolher a LLM** (ex.: ChatGPT, Claude, Gemini) e a interface (web ou API). Use **a mesma** em todas as execuções.

### 1. Execuções (fase F6)

- **Tamanho da amostra:** n = 10 execuções por grupo (20 no total). Com n = 10, o menor p possível do Wilcoxon exato é 0,001.
- **Ordem intercalada:** A-01, B-01, A-02, B-02, … — reduz o efeito de mudanças no serviço da LLM ao longo do tempo.
- **Para cada execução:**
  1. Abra uma **conversa nova** (de preferência "chat temporário"), com memória e instruções personalizadas **desativadas**.
  2. Cole o conteúdo **inteiro** de `prompts/prompt-<grupo>.txt` e envie. Não escreva mais nada.
  3. Copie a resposta **completa e sem edição** para `execucoes/<grupo>/run-NN/resposta.md`.
  4. Preencha uma linha em `execucoes/registro.csv` (data/hora, LLM, versão do modelo, interface, parâmetros conhecidos, hash do documento, observações).
- **Regras de validade:**
  - É proibido corrigir o código, pedir ajustes ou repetir a pergunta na mesma conversa.
  - Se a interface cortar a resposta por tamanho, é permitido **um** "continue"; junte as partes em `resposta.md` e registre em `observacoes`.
  - Execuções nunca são descartadas: uma resposta sem código ou com erro de sintaxe vale 0% e entra na análise.

### 2. Avaliação (fase F7)

```bash
python avaliacao/avaliar.py
```

O script extrai o código de cada `resposta.md` (o bloco de código; se houver vários, o maior), roda o oráculo em processo isolado e gera:

- `resultados/resultados.md` — conformidade por execução, estatística descritiva, teste de hipótese (H1 e H2), conformidade por requisito e ranking de critérios reprovados;
- `resultados/execucoes.csv` e `resultados/criterios.csv` — dados brutos para planilha.

### 3. Análise

1. Para cada critério reprovado na tabela de `resultados.md`, classifique a causa-raiz: **D** (defeito do documento), **G** (erro de geração) ou **T** (defeito do oráculo) — ver [processo, seção 8](../docs/1-processo.md#8-ciclo-de-melhoria-causa-raiz-das-não-conformidades).
2. Se houver causa **T**, corrija o teste, revalide o oráculo e rode `avaliar.py` de novo (registre no relatório).
3. Copie as tabelas para a seção 5 do [relatório técnico](../docs/4-relatorio-tecnico.md) e redija a discussão.

## Verificação rápida do kit

```bash
python avaliacao/avaliar.py --referencia
```

Deve imprimir `Referência: 70/70 critérios atendidos (100,0%)`.
