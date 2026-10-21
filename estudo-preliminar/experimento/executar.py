"""Executa o experimento: gera código com a LLM a partir de cada especificação e mede a conformidade.

Uso:
    python executar.py validar                  # confere o oráculo (testes) contra a referência e um stub vazio
    python executar.py gerar --n 30             # gera e avalia N execuções por condição (retoma de onde parou)
    python executar.py reavaliar                # roda os testes de novo sobre o código já gerado
"""
import argparse
import csv
import os
import random
import re
import subprocess
import sys
import tempfile
import time
import xml.etree.ElementTree as ET
from datetime import datetime, timezone
from pathlib import Path

RAIZ = Path(__file__).resolve().parent
ESPECIFICACOES = {
    "erl": RAIZ / "especificacoes" / "biblioteca-modelo-erl.md",
    "narrativa": RAIZ / "especificacoes" / "biblioteca-narrativa.md",
}
PROMPT = (RAIZ / "prompt.txt").read_text(encoding="utf-8")
TESTES = RAIZ / "testes"
RESULTADOS = RAIZ / "resultados"
CSV = RESULTADOS / "execucoes.csv"
CAMPOS = [
    "condicao", "repeticao", "modelo", "effort", "situacao", "stop_reason",
    "tokens_entrada", "tokens_saida", "total_ca", "aprovados", "conformidade",
    "ca_reprovados", "data_hora",
]
SEMENTE = 2026
MUTANTES = [
    ("prazo do aluno 15 dias", '"aluno": 14', '"aluno": 15'),
    ("sem teto de multa", "min(atraso * MULTA_POR_DIA, MULTA_MAXIMA)", "atraso * MULTA_POR_DIA"),
    ("limite antes do bloqueio",
     "if self._bloqueado(usuario, data):\n            raise UsuarioBloqueado(id_usuario)\n"
     "        if len(usuario.ativos) >= LIMITE_ATIVOS[usuario.tipo]:\n            raise LimiteDeEmprestimosAtingido(id_usuario)",
     "if len(usuario.ativos) >= LIMITE_ATIVOS[usuario.tipo]:\n            raise LimiteDeEmprestimosAtingido(id_usuario)\n"
     "        if self._bloqueado(usuario, data):\n            raise UsuarioBloqueado(id_usuario)"),
    ("atraso inclui a data prevista", "data > self._emprestimos[i].data_prevista", "data >= self._emprestimos[i].data_prevista"),
    ("renovação a partir da data da chamada", "emprestimo.data_prevista += timedelta", "emprestimo.data_prevista = data + timedelta"),
    ("fila ignorada no empréstimo", " or (livro.fila and livro.fila[0] != id_usuario)", ""),
    ("consulta devolve estado interno",
     "e = self._emprestimo(id_emprestimo)\n",
     "e = self._emprestimo(id_emprestimo)\n        return e.__dict__\n"),
    ("ISBN aceita 12 dígitos", "len(isbn) != 13", "len(isbn) not in (12, 13)"),
]


def criterios():
    texto = ESPECIFICACOES["erl"].read_text(encoding="utf-8")
    ids = re.findall(r"\*\*(CA-RN?F\d{2}-\d{2})\*\*", texto)
    if len(ids) != len(set(ids)):
        sys.exit("IDs de CA duplicados na especificação")
    return ids


def ca_do_teste(nome):
    m = re.fullmatch(r"test_CA_(RN?F\d{2})_(\d{2})", nome)
    return f"CA-{m.group(1)}-{m.group(2)}" if m else None


def avaliar(pasta):
    ids = criterios()
    with tempfile.TemporaryDirectory() as tmp:
        junit = Path(tmp) / "junit.xml"
        cmd = [
            sys.executable, "-m", "pytest", str(TESTES), "-q", "-p", "no:cacheprovider",
            "--timeout=20", f"--junitxml={junit}",
        ]
        try:
            subprocess.run(
                cmd, cwd=tmp, env={**_env(), "ALVO": str(pasta)},
                capture_output=True, text=True, timeout=600,
            )
        except subprocess.TimeoutExpired:
            return ids, set()
        aprovados = set()
        if junit.exists():
            for caso in ET.parse(junit).getroot().iter("testcase"):
                if len(caso) == 0:
                    ca = ca_do_teste(caso.get("name", ""))
                    if ca:
                        aprovados.add(ca)
    testados = {ca_do_teste(n) for n in re.findall(r"def (test_\w+)", (TESTES / "test_aceitacao.py").read_text(encoding="utf-8"))}
    if testados != set(ids):
        sys.exit(f"CAs sem teste ou testes sem CA: {sorted(testados ^ set(ids))}")
    return ids, aprovados & set(ids)


def _env():
    return {k: v for k, v in os.environ.items() if k != "ALVO"}


def extrair_codigo(texto):
    blocos = re.findall(r"```(?:python|py)?[ \t]*\n(.*?)```", texto, flags=re.S)
    if blocos:
        return max(blocos, key=len)
    return None


def chamar_llm(cliente, modelo, effort, especificacao):
    conteudo = PROMPT.replace("{{DOCUMENTO}}", especificacao)
    with cliente.messages.stream(
        model=modelo,
        max_tokens=64000,
        output_config={"effort": effort},
        messages=[{"role": "user", "content": conteudo}],
    ) as stream:
        resposta = stream.get_final_message()
    texto = "".join(b.text for b in resposta.content if b.type == "text")
    return texto, resposta


def linhas_existentes():
    if not CSV.exists():
        return []
    with CSV.open(encoding="utf-8", newline="") as f:
        return list(csv.DictReader(f))


def gravar(linhas):
    RESULTADOS.mkdir(exist_ok=True)
    with CSV.open("w", encoding="utf-8", newline="") as f:
        w = csv.DictWriter(f, fieldnames=CAMPOS)
        w.writeheader()
        w.writerows(linhas)


def linha(condicao, rep, modelo, effort, situacao, stop_reason, uso, ids, aprovados):
    return {
        "condicao": condicao,
        "repeticao": rep,
        "modelo": modelo,
        "effort": effort,
        "situacao": situacao,
        "stop_reason": stop_reason,
        "tokens_entrada": getattr(uso, "input_tokens", ""),
        "tokens_saida": getattr(uso, "output_tokens", ""),
        "total_ca": len(ids),
        "aprovados": len(aprovados),
        "conformidade": f"{len(aprovados) / len(ids):.4f}",
        "ca_reprovados": " ".join(c for c in ids if c not in aprovados),
        "data_hora": datetime.now(timezone.utc).isoformat(timespec="seconds"),
    }


def cmd_validar(_):
    ids, ok = avaliar(RAIZ / "referencia")
    print(f"Referência: {len(ok)}/{len(ids)} CAs aprovados")
    with tempfile.TemporaryDirectory() as tmp:
        (Path(tmp) / "biblioteca.py").write_text("", encoding="utf-8")
        _, ok_vazio = avaliar(Path(tmp))
    print(f"Stub vazio: {len(ok_vazio)}/{len(ids)} CAs aprovados")
    if len(ok) != len(ids) or ok_vazio:
        sys.exit("Oráculo inválido: a referência deve passar em tudo e o stub em nada.")
    fonte = (RAIZ / "referencia" / "biblioteca.py").read_text(encoding="utf-8")
    sobreviventes = []
    for descricao, antes, depois in MUTANTES:
        if antes not in fonte:
            sys.exit(f"Mutante não se aplica: {descricao}")
        with tempfile.TemporaryDirectory() as tmp:
            (Path(tmp) / "biblioteca.py").write_text(fonte.replace(antes, depois, 1), encoding="utf-8")
            _, ok_mutante = avaliar(Path(tmp))
        print(f"Mutante '{descricao}': {len(ok_mutante)}/{len(ids)}")
        if len(ok_mutante) == len(ids):
            sobreviventes.append(descricao)
    if sobreviventes:
        sys.exit(f"Oráculo fraco: mutantes não detectados: {sobreviventes}")
    print(f"Oráculo válido: {len(MUTANTES)} mutantes detectados.")


def cmd_gerar(args):
    import anthropic

    cliente = anthropic.Anthropic(max_retries=5)
    linhas = linhas_existentes()
    feitas = {(l["condicao"], int(l["repeticao"])) for l in linhas}
    agenda = [(c, r) for c in args.condicoes for r in range(1, args.n + 1)]
    random.Random(SEMENTE).shuffle(agenda)
    pendentes = [x for x in agenda if x not in feitas]
    print(f"{len(pendentes)} execuções pendentes de {len(agenda)}")
    for i, (condicao, rep) in enumerate(pendentes, 1):
        pasta = RESULTADOS / condicao / f"{rep:02d}"
        pasta.mkdir(parents=True, exist_ok=True)
        especificacao = ESPECIFICACOES[condicao].read_text(encoding="utf-8")
        inicio = time.time()
        try:
            texto, resposta = chamar_llm(cliente, args.modelo, args.effort, especificacao)
        except anthropic.APIStatusError as e:
            if e.status_code in (400, 401, 403, 404):
                sys.exit(f"Erro {e.status_code} da API, interrompendo: {e.message}")
            print(f"  erro {e.status_code} da API em {condicao}#{rep}; rode de novo para retomar")
            continue
        except anthropic.APIConnectionError:
            print(f"  falha de conexão em {condicao}#{rep}; rode de novo para retomar")
            continue
        (pasta / "resposta.md").write_text(texto, encoding="utf-8")
        codigo = extrair_codigo(texto)
        ids = criterios()
        if resposta.stop_reason == "refusal":
            situacao, aprovados = "recusa", set()
        elif resposta.stop_reason == "max_tokens":
            situacao, aprovados = "truncada", set()
        elif codigo is None:
            situacao, aprovados = "sem_codigo", set()
        else:
            (pasta / "biblioteca.py").write_text(codigo, encoding="utf-8")
            ids, aprovados = avaliar(pasta)
            situacao = "ok"
        linhas.append(linha(condicao, rep, args.modelo, args.effort, situacao,
                            resposta.stop_reason, resposta.usage, ids, aprovados))
        gravar(linhas)
        print(f"[{i}/{len(pendentes)}] {condicao}#{rep}: {len(aprovados)}/{len(ids)} "
              f"({situacao}, {time.time() - inicio:.0f}s)")


def cmd_registrar(args):
    linhas = linhas_existentes()
    feitas = {(l["condicao"], int(l["repeticao"])) for l in linhas}
    for condicao in args.condicoes:
        base = RESULTADOS / condicao
        if not base.exists():
            continue
        for pasta in sorted(p for p in base.iterdir() if p.is_dir()):
            rep = int(pasta.name)
            if (condicao, rep) in feitas:
                continue
            if (pasta / "biblioteca.py").exists():
                ids, aprovados = avaliar(pasta)
                situacao = "ok"
            else:
                ids, aprovados, situacao = criterios(), set(), "sem_codigo"
            linhas.append(linha(condicao, rep, args.modelo, args.effort, situacao, "", None, ids, aprovados))
            print(f"{condicao}#{rep}: {len(aprovados)}/{len(ids)} ({situacao})")
    linhas.sort(key=lambda l: (l["condicao"], int(l["repeticao"])))
    gravar(linhas)


def cmd_reavaliar(_):
    linhas = linhas_existentes()
    for l in linhas:
        pasta = RESULTADOS / l["condicao"] / f"{int(l['repeticao']):02d}"
        if l["situacao"] != "ok":
            continue
        ids, aprovados = avaliar(pasta)
        l.update({
            "total_ca": len(ids),
            "aprovados": len(aprovados),
            "conformidade": f"{len(aprovados) / len(ids):.4f}",
            "ca_reprovados": " ".join(c for c in ids if c not in aprovados),
        })
        print(f"{l['condicao']}#{l['repeticao']}: {len(aprovados)}/{len(ids)}")
    gravar(linhas)


def main():
    p = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = p.add_subparsers(dest="comando", required=True)
    sub.add_parser("validar").set_defaults(f=cmd_validar)
    g = sub.add_parser("gerar")
    g.add_argument("--n", type=int, default=30)
    g.add_argument("--modelo", default="claude-opus-5-5")
    g.add_argument("--effort", default="medium", choices=["low", "medium", "high", "xhigh", "max"])
    g.add_argument("--condicoes", nargs="+", default=["erl", "narrativa"], choices=list(ESPECIFICACOES))
    g.set_defaults(f=cmd_gerar)
    r = sub.add_parser("registrar", help="avalia código gerado fora da API, em resultados/<condicao>/<NN>/biblioteca.py")
    r.add_argument("--modelo", default="claude-opus-5-5 (subagente Claude Code)")
    r.add_argument("--effort", default="padrao")
    r.add_argument("--condicoes", nargs="+", default=["erl", "narrativa"], choices=list(ESPECIFICACOES))
    r.set_defaults(f=cmd_registrar)
    sub.add_parser("reavaliar").set_defaults(f=cmd_reavaliar)
    sys.stdout.reconfigure(encoding="utf-8")
    args = p.parse_args()
    args.f(args)


if __name__ == "__main__":
    main()
