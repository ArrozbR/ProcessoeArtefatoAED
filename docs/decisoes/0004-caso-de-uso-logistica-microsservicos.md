# 0004. Caso de uso: módulo de logística em microsserviços

- **Status:** Aceito (substitui a ADR 0002)
- **Data:** 2026-10-21

## Contexto

O escopo da atividade mudou: o sistema especificado deve ser um módulo de **microsserviços de logística de entregas** a domicílio por transportadoras, no estilo Mercado Livre e Amazon, em que o cliente escolhe a transportadora. Além disso, o estudo preliminar (biblioteca, módulo único) bateu no teto da métrica (100% nas duas condições), indicando que o caso era fácil demais para discriminar formatos de especificação.

## Decisão

Cinco serviços FastAPI, cada um num processo, com estado em memória: Transportadoras, Cotação, Envios, Rastreamento e Notificações. HTTP síncrono para consultas (Cotação → Transportadoras; Envios → Cotação) e eventos para mudanças de estado (Envios → Rastreamento e Notificações), por um barramento **fornecido pronto**, que entrega em ordem e de forma síncrona. A LLM gera os cinco serviços; o barramento é infraestrutura.

## Alternativas consideradas

- **Só REST:** mais simples, mas deixaria de fora o problema central de microsserviços: o contrato de eventos entre serviços que não se conhecem.
- **Kafka ou RabbitMQ real via Docker:** mais fiel, mas cada uma das 60 execuções precisaria subir o broker, o que deixa o experimento lento e sujeito a falhas de infraestrutura que não têm relação com a especificação.
- **Barramento gerado pela LLM:** misturaria a medição da especificação com a qualidade de um componente de infraestrutura.

## Consequências

- **Positivas:** 77 critérios, 5 serviços e contratos entre eles; o caso é grande o bastante para que a forma da especificação possa fazer diferença.
- **Negativas:** o barramento síncrono não exercita falhas reais de mensageria (declarado no relatório e no documento do módulo, Seção 7).
- **Como saber se deu errado:** teto de novo (100% nas duas condições) indicaria que nem este tamanho discrimina.
