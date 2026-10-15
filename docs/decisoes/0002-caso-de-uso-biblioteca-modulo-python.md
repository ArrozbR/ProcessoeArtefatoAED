# 0002. Caso de uso: sistema de biblioteca como módulo Python

- **Status:** Aceito
- **Data:** 2026-10-15

## Contexto

O experimento precisa de um sistema que caiba numa resposta de LLM, tenha regras com interação não trivial e possa ser testado automaticamente, sem infraestrutura e sem resultados instáveis.

## Decisão

Sistema de empréstimos de biblioteca, implementado como um único arquivo `biblioteca.py` com a classe `Biblioteca` e exceções próprias. Estado em memória, datas como parâmetro e dinheiro em centavos.

## Alternativas consideradas

- **API HTTP:** mais realista, mas exige subir servidor, escolher framework e lidar com portas e tempo de inicialização em cada execução. Aumenta o ruído da medição sem aumentar o que se quer medir.
- **Reaproveitar um projeto do grupo (Jogo da Forca):** o código pronto enviesaria a escrita da especificação, e o domínio tem poucas regras de negócio.

## Consequências

- **Positivas:** testes rápidos e determinísticos; regras ricas (bloqueio, fila, renovação, teto de multa).
- **Negativas:** a validade externa fica limitada a sistemas pequenos e sem persistência (registrado no relatório).
- **Como saber se deu errado:** conformidade perto de 100% nas duas condições, indicando que o caso é fácil demais para discriminar.
