"""Executa o oráculo de aceitação contra UMA implementação e imprime o resultado em JSON.

Uso:  python executor.py <pasta que contém biblioteca.py>

Um critério é atendido somente se todos os seus testes passarem. Cada teste roda
com tempo-limite próprio, para que um laço infinito no código gerado não derrube
a avaliação inteira.
"""

import io
import json
import os
import sys
import threading
import unittest
from pathlib import Path

TEMPO_LIMITE_S = 30


def executar(pasta_impl: Path) -> dict:
    sys.path.insert(0, str(pasta_impl.resolve()))
    sys.path.insert(1, str(Path(__file__).resolve().parent))
    saida_real = sys.stdout
    sys.stdout = io.StringIO()  # descarta print() do código gerado
    try:
        import testes_aceitacao as t

        criterios: dict[str, dict] = {}
        for teste in _todos_os_testes(unittest.defaultTestLoader.loadTestsFromModule(t)):
            cid = getattr(getattr(teste, teste._testMethodName), "criterio", None)
            if cid is None:
                continue
            registro = criterios.setdefault(cid, {"atendido": True, "falhas": []})
            problema = _rodar(teste)
            if problema:
                registro["atendido"] = False
                registro["falhas"].append(f"{teste._testMethodName}: {problema}")
        e = t.ERRO_IMPORTACAO
        erro = f"{type(e).__name__}: {e}" if e else None
        return {"erro_importacao": erro, "criterios": criterios}
    finally:
        sys.stdout = saida_real


def _todos_os_testes(suite):
    for item in suite:
        if isinstance(item, unittest.TestSuite):
            yield from _todos_os_testes(item)
        else:
            yield item


def _rodar(teste) -> str | None:
    """Roda um teste numa thread com tempo-limite; devolve a descrição do problema ou None."""
    resultado = unittest.TestResult()
    thread = threading.Thread(target=teste.run, args=(resultado,), daemon=True)
    thread.start()
    thread.join(TEMPO_LIMITE_S)
    if thread.is_alive():
        return f"tempo-limite de {TEMPO_LIMITE_S}s excedido"
    for _, rastro in resultado.failures + resultado.errors:
        return _linha_da_excecao(rastro)[:300]
    return None


def _linha_da_excecao(rastro: str) -> str:
    """Primeira linha não indentada após o último 'Traceback' (ex.: 'AssertionError: ...')."""
    linhas = rastro.strip().splitlines()
    inicio = max((i for i, l in enumerate(linhas) if l.startswith("Traceback")), default=-1)
    return next((l for l in linhas[inicio + 1:] if l and not l[0].isspace()), linhas[-1])


if __name__ == "__main__":
    if len(sys.argv) != 2:
        sys.exit(__doc__)
    print(json.dumps(executar(Path(sys.argv[1]))))
    sys.stdout.flush()
    os._exit(0)  # encerra mesmo se houver threads presas em laço infinito
