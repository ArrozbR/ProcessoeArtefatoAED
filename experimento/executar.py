"""Executa o experimento do módulo de logística e mede a conformidade.

Uso:
    python executar.py validar                    # oráculo: referência 100%, stub 0%, todos os mutantes detectados
    python executar.py registrar                  # avalia o código gerado em resultados/<condicao>/<NN>/servicos/
    python executar.py gerar --n 30               # gera via API da Anthropic (precisa de ANTHROPIC_API_KEY) e avalia
    python executar.py reavaliar                  # roda os testes de novo sobre o código já registrado
"""
import argparse
import csv
import os
import random
import re
import shutil
import subprocess
import sys
import tempfile
import xml.etree.ElementTree as ET
from datetime import datetime, timezone
from pathlib import Path

RAIZ = Path(__file__).resolve().parent
ESPECIFICACOES = {
    "erl": RAIZ / "especificacoes" / "ERS-LOG-001.md",
    "narrativa": RAIZ / "especificacoes" / "logistica-narrativa.md",
}
PROMPT = RAIZ / "prompt.txt"
TESTES = RAIZ / "testes"
REFERENCIA = RAIZ / "referencia"
RESULTADOS = RAIZ / "resultados"
CSV = RESULTADOS / "execucoes.csv"
SERVICOS = ["transportadoras", "cotacao", "envios", "rastreamento", "notificacoes"]
CAMPOS = ["condicao", "repeticao", "modelo", "situacao", "total_ca", "aprovados", "conformidade",
          "ca_reprovados", "linhas_codigo", "data_hora"]
SEMENTE = 2026

MUTANTES = [
    ("peso cubado com divisor 5", "cotacao", "// 6)", "// 5)"),
    ("preço interestadual arredondado para baixo", "cotacao",
     "preco = -(-(preco * (100 + t[\"adicional_interestadual_pct\"])) // 100)",
     "preco = (preco * (100 + t[\"adicional_interestadual_pct\"])) // 100"),
    ("prazo em dias corridos", "cotacao", "        if d.weekday() < 5:\n            dias -= 1",
     "        dias -= 1"),
    ("ordenação sem desempate por id", "cotacao",
     "(o[\"preco_centavos\"], o[\"prazo_dias_uteis\"], o[\"transportadora_id\"])",
     "(o[\"preco_centavos\"], o[\"prazo_dias_uteis\"])"),
    ("cotação válida por 6 dias", "cotacao", "VALIDADE_DIAS = 7", "VALIDADE_DIAS = 6"),
    ("listagem ignora somente_ativas", "transportadoras",
     "if t[\"ativa\"] or not somente_ativas]", "]"),
    ("erro de validação no formato padrão do FastAPI", "transportadoras",
     "@app.exception_handler(RequestValidationError)\n", ""),
    ("devolução só na 4ª falha", "envios", "MAX_FALHAS = 3", "MAX_FALHAS = 4"),
    ("cotação consultada antes do pedido duplicado", "envios",
     "    if any(e[\"pedido_id\"] == dados.pedido_id and e[\"status\"] != \"CANCELADO\" for e in _envios.values()):\n"
     "        raise ErroApi(409, \"ENVIO_DUPLICADO\", \"o pedido já tem um envio ativo\")\n"
     "    cotacao = _buscar_cotacao(dados.cotacao_id)\n",
     "    cotacao = _buscar_cotacao(dados.cotacao_id)\n"
     "    if any(e[\"pedido_id\"] == dados.pedido_id and e[\"status\"] != \"CANCELADO\" for e in _envios.values()):\n"
     "        raise ErroApi(409, \"ENVIO_DUPLICADO\", \"o pedido já tem um envio ativo\")\n"),
    ("data e hora anteriores aceitas", "envios",
     "    if data_hora < envio[\"historico\"][-1][\"data_hora\"]:", "    if False:"),
    ("cancelamento depois da coleta", "envios", "if envio[\"status\"] != \"CRIADO\":",
     "if envio[\"status\"] not in (\"CRIADO\", \"COLETADO\"):"),
    ("rastreamento sem idempotência", "rastreamento",
     "    if evento.id in _processados:\n        return {\"processado\": False}\n", ""),
    ("atrasado ignora estado terminal", "rastreamento", "        and r[\"status\"] not in TERMINAIS\n", ""),
    ("notificação também para COLETADO", "notificacoes",
     "    \"SAIU_PARA_ENTREGA\": \"Pedido {pedido_id}: saiu para entrega.\",",
     "    \"COLETADO\": \"Pedido {pedido_id}: coletado.\",\n"
     "    \"SAIU_PARA_ENTREGA\": \"Pedido {pedido_id}: saiu para entrega.\","),
]


def criterios():
    texto = ESPECIFICACOES["erl"].read_text(encoding="utf-8")
    ids = re.findall(r"\*\*(CA-[A-Z0-9]+-\d{2})\*\*", texto)
    if len(ids) != len(set(ids)):
        sys.exit("IDs de CA duplicados na especificação")
    return ids


def ca_do_teste(nome):
    m = re.fullmatch(r"test_CA_([A-Z0-9]+)_(\d{2})", nome)
    return f"CA-{m.group(1)}-{m.group(2)}" if m else None


def avaliar(alvo):
    """Roda o oráculo sobre alvo/servicos/ e devolve (todos os CAs, CAs aprovados)."""
    ids = criterios()
    testados = {ca_do_teste(n) for n in re.findall(r"^def (test_\w+)", (TESTES / "test_aceitacao.py")
                                                    .read_text(encoding="utf-8"), flags=re.M)}
    if testados != set(ids):
        sys.exit(f"CAs sem teste ou testes sem CA: {sorted(testados ^ set(ids))}")
    with tempfile.TemporaryDirectory() as tmp:
        junit = Path(tmp) / "junit.xml"
        env = {k: v for k, v in os.environ.items() if not k.startswith("URL_")}
        env["ALVO"] = str(Path(alvo).resolve())
        cmd = [sys.executable, "-m", "pytest", str(TESTES), "-q", "-p", "no:cacheprovider",
               "--timeout=60", f"--junitxml={junit}"]
        try:
            subprocess.run(cmd, cwd=tmp, env=env, capture_output=True, text=True, timeout=1800)
        except subprocess.TimeoutExpired:
            return ids, set()
        aprovados = set()
        if junit.exists():
            for caso in ET.parse(junit).getroot().iter("testcase"):
                if len(caso) == 0:
                    ca = ca_do_teste(caso.get("name", ""))
                    if ca:
                        aprovados.add(ca)
    return ids, aprovados & set(ids)


def linhas_codigo(alvo):
    total = 0
    for nome in SERVICOS:
        arquivo = Path(alvo) / "servicos" / nome / "main.py"
        if arquivo.exists():
            total += sum(1 for l in arquivo.read_text(encoding="utf-8").splitlines() if l.strip())
    return total


def linhas_existentes():
    if not CSV.exists():
        return []
    with CSV.open(encoding="utf-8", newline="") as f:
        return list(csv.DictReader(f))


def gravar(linhas):
    RESULTADOS.mkdir(exist_ok=True)
    linhas.sort(key=lambda l: (l["condicao"], int(l["repeticao"])))
    with CSV.open("w", encoding="utf-8", newline="") as f:
        w = csv.DictWriter(f, fieldnames=CAMPOS)
        w.writeheader()
        w.writerows(linhas)


def linha(condicao, rep, modelo, situacao, ids, aprovados, alvo):
    return {
        "condicao": condicao, "repeticao": rep, "modelo": modelo, "situacao": situacao,
        "total_ca": len(ids), "aprovados": len(aprovados),
        "conformidade": f"{len(aprovados) / len(ids):.4f}",
        "ca_reprovados": " ".join(c for c in ids if c not in aprovados),
        "linhas_codigo": linhas_codigo(alvo),
        "data_hora": datetime.now(timezone.utc).isoformat(timespec="seconds"),
    }


def cmd_validar(_):
    ids, ok = avaliar(REFERENCIA)
    print(f"Referência: {len(ok)}/{len(ids)}")
    with tempfile.TemporaryDirectory() as tmp:
        for nome in SERVICOS:
            (Path(tmp) / "servicos" / nome).mkdir(parents=True)
            (Path(tmp) / "servicos" / nome / "main.py").write_text("", encoding="utf-8")
        _, ok_vazio = avaliar(tmp)
    print(f"Stub vazio: {len(ok_vazio)}/{len(ids)}")
    if len(ok) != len(ids) or ok_vazio:
        sys.exit("Oráculo inválido: a referência deve passar em tudo e o stub em nada.")
    sobreviventes = []
    for descricao, servico, antes, depois in MUTANTES:
        with tempfile.TemporaryDirectory() as tmp:
            shutil.copytree(REFERENCIA / "servicos", Path(tmp) / "servicos")
            arquivo = Path(tmp) / "servicos" / servico / "main.py"
            fonte = arquivo.read_text(encoding="utf-8")
            if antes not in fonte:
                sys.exit(f"Mutante não se aplica: {descricao}")
            arquivo.write_text(fonte.replace(antes, depois, 1), encoding="utf-8")
            _, ok_m = avaliar(tmp)
        reprovados = [c for c in ids if c not in ok_m]
        print(f"Mutante '{descricao}': {len(ok_m)}/{len(ids)}  reprovados: {' '.join(reprovados)}")
        if not reprovados:
            sobreviventes.append(descricao)
    if sobreviventes:
        sys.exit(f"Oráculo fraco: mutantes não detectados: {sobreviventes}")
    print(f"Oráculo válido: {len(MUTANTES)} mutantes detectados.")


def cmd_registrar(args):
    linhas = linhas_existentes()
    feitas = {(l["condicao"], int(l["repeticao"])) for l in linhas}
    for condicao in args.condicoes:
        base = RESULTADOS / condicao
        if not base.exists():
            continue
        for pasta in sorted(p for p in base.iterdir() if p.is_dir() and p.name.isdigit()):
            rep = int(pasta.name)
            if (condicao, rep) in feitas:
                continue
            tem_codigo = any((pasta / "servicos" / s / "main.py").exists() for s in SERVICOS)
            if tem_codigo:
                ids, aprovados = avaliar(pasta)
                situacao = "ok"
            else:
                ids, aprovados, situacao = criterios(), set(), "sem_codigo"
            linhas.append(linha(condicao, rep, args.modelo, situacao, ids, aprovados, pasta))
            gravar(linhas)
            print(f"{condicao}#{rep}: {len(aprovados)}/{len(ids)} ({situacao})")


def cmd_reavaliar(_):
    linhas = linhas_existentes()
    for l in linhas:
        pasta = RESULTADOS / l["condicao"] / f"{int(l['repeticao']):02d}"
        if l["situacao"] != "ok":
            continue
        ids, aprovados = avaliar(pasta)
        l.update(linha(l["condicao"], l["repeticao"], l["modelo"], "ok", ids, aprovados, pasta))
        print(f"{l['condicao']}#{l['repeticao']}: {len(aprovados)}/{len(ids)}")
    gravar(linhas)


def extrair_arquivos(texto):
    arquivos = {}
    for bloco in re.findall(r"```(?:python|py)?[ \t]*\n(.*?)```", texto, flags=re.S):
        m = re.match(r"\s*#\s*arquivo:\s*(\S+)", bloco)
        if m and m.group(1) in {f"servicos/{s}/main.py" for s in SERVICOS}:
            arquivos[m.group(1)] = bloco
    return arquivos


def cmd_gerar(args):
    import anthropic

    cliente = anthropic.Anthropic(max_retries=5)
    linhas = linhas_existentes()
    feitas = {(l["condicao"], int(l["repeticao"])) for l in linhas}
    agenda = [(c, r) for c in args.condicoes for r in range(1, args.n + 1)]
    random.Random(SEMENTE).shuffle(agenda)
    for condicao, rep in [x for x in agenda if x not in feitas]:
        pasta = RESULTADOS / condicao / f"{rep:02d}"
        pasta.mkdir(parents=True, exist_ok=True)
        conteudo = PROMPT.read_text(encoding="utf-8").replace(
            "{{DOCUMENTO}}", ESPECIFICACOES[condicao].read_text(encoding="utf-8"))
        with cliente.messages.stream(model=args.modelo, max_tokens=64000,
                                     messages=[{"role": "user", "content": conteudo}]) as stream:
            resposta = stream.get_final_message()
        texto = "".join(b.text for b in resposta.content if b.type == "text")
        (pasta / "resposta.md").write_text(texto, encoding="utf-8")
        for caminho, codigo in extrair_arquivos(texto).items():
            (pasta / caminho).parent.mkdir(parents=True, exist_ok=True)
            (pasta / caminho).write_text(codigo, encoding="utf-8")
    cmd_registrar(argparse.Namespace(condicoes=args.condicoes, modelo=args.modelo))


def main():
    sys.stdout.reconfigure(encoding="utf-8")
    p = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = p.add_subparsers(dest="comando", required=True)
    sub.add_parser("validar").set_defaults(f=cmd_validar)
    r = sub.add_parser("registrar")
    r.add_argument("--modelo", default="claude-opus-5-5 (agente Claude Code)")
    r.add_argument("--condicoes", nargs="+", default=list(ESPECIFICACOES), choices=list(ESPECIFICACOES))
    r.set_defaults(f=cmd_registrar)
    g = sub.add_parser("gerar")
    g.add_argument("--n", type=int, default=30)
    g.add_argument("--modelo", default="claude-opus-5-5")
    g.add_argument("--condicoes", nargs="+", default=list(ESPECIFICACOES), choices=list(ESPECIFICACOES))
    g.set_defaults(f=cmd_gerar)
    sub.add_parser("reavaliar").set_defaults(f=cmd_reavaliar)
    args = p.parse_args()
    args.f(args)


if __name__ == "__main__":
    main()
