"""Valida o oráculo de aceitação (fase F5 do processo PRECISA).

1. A implementação de referência DEVE atender 100% dos critérios.
2. Cada mutante (referência com um desvio deliberado da especificação) DEVE ser
   detectado, isto é, reprovar ao menos um critério.

Uso:  python validar_oraculo.py      → grava experimento/resultados/validacao-oraculo.md
"""

import json
import subprocess
import sys
import tempfile
from pathlib import Path

from avaliar import ordem_criterio

AQUI = Path(__file__).resolve().parent
REFERENCIA = AQUI.parent / "referencia" / "biblioteca.py"
SAIDA = AQUI.parent / "resultados" / "validacao-oraculo.md"

# (descrição do desvio, trecho original, trecho mutado)
MUTANTES = [
    ("Prazo do ALUNO de 15 dias (RN-02)", '"ALUNO": (3, 14)', '"ALUNO": (3, 15)'),
    ("Limite do ALUNO de 4 empréstimos (RN-02)", '"ALUNO": (3, 14)', '"ALUNO": (4, 14)'),
    ("Multa sem teto (RN-05)", "min(dias * MULTA_DIARIA, TETO_MULTA)", "dias * MULTA_DIARIA"),
    ("Pendência com ≥ em vez de > (RN-04)", "any(data > e.data_prevista_devolucao", "any(data >= e.data_prevista_devolucao"),
    ("ISBN sem remover hífens (C-01)", 'return isbn.replace("-", "").replace(" ", "")', 'return isbn.replace(" ", "")'),
    ("Pesquisa diferencia maiúsculas (RF-04)", "termo = termo.strip().casefold()", "termo = termo.strip()"),
    ("Indisponível verificado antes do limite (RF-05, C-05)", "if len(ativos) >= limite:",
     "if livro.exemplares_disponiveis > 0 and len(ativos) >= limite:"),
    ("Renovação somada à data prevista anterior (RN-07)",
     "emprestimo.data_prevista_devolucao = data + timedelta(days=prazo)",
     "emprestimo.data_prevista_devolucao = emprestimo.data_prevista_devolucao + timedelta(days=prazo)"),
    ("Até 3 renovações (RN-07)", "MAX_RENOVACOES = 2", "MAX_RENOVACOES = 3"),
    ("Fila de reservas ignorada no empréstimo (RN-08)", "if fila and fila[0] != usuario.id:", "if False:"),
    ("Valores monetários em float (C-03)", "return Decimal(valor).quantize(CENTAVOS, rounding=ROUND_HALF_UP)",
     "return float(valor)"),
    ("Pagamento acima do saldo aceito (RF-10)", "if valor > usuario.saldo_devedor:", "if False:"),
    ("Atrasados sem desempate por id (RF-11)", "key=lambda e: (e.data_prevista_devolucao, e.id)",
     "key=lambda e: (e.data_prevista_devolucao, -e.id)"),
    ("print() no cadastro (R-04)", '"""Cadastra um livro com todos os exemplares disponíveis (RF-01)."""',
     '"""Cadastra um livro com todos os exemplares disponíveis (RF-01)."""\n        print("cadastrando")'),
    ("Método público sem docstring (RNF-06)", '"""Retorna os ids da fila de reservas do livro, em ordem (RF-09)."""', "pass"),
    ("Tipo de usuário não convertido para maiúsculas (C-02)", "tipo.strip().upper()", "tipo.strip()"),
    ("Devolução aceita data anterior ao empréstimo (RF-06)",
     'if data < emprestimo.data_emprestimo:\n            raise DadosInvalidosErro("Data de devolução',
     'if False:\n            raise DadosInvalidosErro("Data de devolução'),
]


def executar(pasta: Path) -> dict:
    proc = subprocess.run([sys.executable, str(AQUI / "executor.py"), str(pasta)],
                          capture_output=True, text=True, timeout=900)
    return json.loads(proc.stdout.strip().splitlines()[-1])["criterios"]


def main() -> None:
    sys.stdout.reconfigure(encoding="utf-8")
    fonte = REFERENCIA.read_text(encoding="utf-8")
    ref = executar(REFERENCIA.parent)
    ok_ref = sum(c["atendido"] for c in ref.values())
    linhas = ["# Validação do oráculo de aceitação", "",
              "> Arquivo gerado por `experimento/avaliacao/validar_oraculo.py`.", "",
              f"**Implementação de referência:** {ok_ref}/{len(ref)} critérios atendidos.", "",
              "## Mutantes", "",
              "| # | Desvio introduzido | Critérios reprovados | Detectado? |", "|---|---|---|---|"]
    mortos = 0
    for i, (nome, antes, depois) in enumerate(MUTANTES, 1):
        assert fonte.count(antes) == 1, f"trecho não encontrado de forma única: {antes!r}"
        with tempfile.TemporaryDirectory() as tmp:
            Path(tmp, "biblioteca.py").write_text(fonte.replace(antes, depois), encoding="utf-8")
            res = executar(Path(tmp))
        reprovados = sorted((cid for cid, c in res.items() if not c["atendido"]), key=ordem_criterio)
        mortos += bool(reprovados)
        print(f"{i:2}. {nome}: {len(reprovados)} critério(s) reprovado(s)")
        linhas.append(f"| {i} | {nome} | {', '.join(reprovados) or '—'} | {'sim' if reprovados else '**não**'} |")
    linhas += ["", f"**Escore de mutação:** {mortos}/{len(MUTANTES)} mutantes detectados.", ""]
    SAIDA.parent.mkdir(parents=True, exist_ok=True)
    SAIDA.write_text("\n".join(linhas), encoding="utf-8")
    print(f"\nReferência {ok_ref}/{len(ref)} · mutantes detectados {mortos}/{len(MUTANTES)} · {SAIDA}")
    sys.exit(0 if ok_ref == len(ref) and mortos == len(MUTANTES) else 1)


if __name__ == "__main__":
    main()
