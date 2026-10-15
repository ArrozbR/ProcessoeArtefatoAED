"""Gera os prompts completos (prompt padrão + documento) de cada grupo do experimento.

Uso:  python montar_prompts.py   → grava prompts/prompt-A-precisa.txt e prompts/prompt-B-livre.txt
e imprime o SHA-256 de cada documento, que deve ser anotado no registro (execucoes/registro.csv).
"""

import hashlib
import re
from pathlib import Path

AQUI = Path(__file__).resolve().parent
GRUPOS = {
    "A-precisa": AQUI / "especificacao" / "ERS-BIB-001.md",
    "B-livre": AQUI / "especificacao" / "descricao-livre.md",
}


def main() -> None:
    padrao = (AQUI / "prompt-geracao.md").read_text(encoding="utf-8")
    prompt = re.search(r"```text\n(.*?)```", padrao, re.DOTALL).group(1)
    destino = AQUI / "prompts"
    destino.mkdir(exist_ok=True)
    for grupo, doc in GRUPOS.items():
        conteudo = doc.read_text(encoding="utf-8")
        (destino / f"prompt-{grupo}.txt").write_text(prompt + "\n" + conteudo, encoding="utf-8")
        resumo = hashlib.sha256(conteudo.encode("utf-8")).hexdigest()[:16]
        print(f"{grupo}: {doc.name}  sha256={resumo}  -> prompts/prompt-{grupo}.txt")


if __name__ == "__main__":
    main()
