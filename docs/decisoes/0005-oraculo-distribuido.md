# 0005. Oráculo distribuído: serviços como processos, testados pela rede

- **Status:** Aceito
- **Data:** 2026-10-21

## Contexto

Com microsserviços, o oráculo não pode importar o código como uma biblioteca: o que precisa ser medido inclui a comunicação entre serviços (URLs, eventos), que só existe com processos separados.

## Decisão

O harness (`experimento/testes/conftest.py`) sobe o barramento e os cinco serviços com `uvicorn`, em portas livres escolhidas na hora, passando as URLs por variável de ambiente, e testa tudo por HTTP. Antes de cada teste, o estado é zerado por `POST /admin/reset`; se a rota falhar, o harness reinicia o processo daquele serviço. O barramento guarda os eventos publicados, e os testes os usam para verificar o contrato de eventos.

## Alternativas consideradas

- **`TestClient` do FastAPI, no mesmo processo:** mais rápido, mas não testa a leitura das URLs por ambiente nem a comunicação real entre serviços.
- **Reiniciar todos os processos a cada teste:** isolamento perfeito, mas cada execução levaria minutos em vez de segundos.
- **Depender só do reset:** um serviço sem a rota de reset derrubaria em cascata quase todos os critérios, e a métrica passaria a medir um único defeito.

## Consequências

- **Positivas:** o mesmo oráculo vale para a referência e para qualquer código gerado; mede o sistema como ele rodaria de verdade.
- **Negativas:** mais lento que testes em processo (cerca de 17 s por execução completa); um sistema que não sobe leva bem mais tempo para ser avaliado.
- **Como saber se deu errado:** testes instáveis (passam e falham sem mudança de código) indicariam problema de tempo de subida ou de portas.
