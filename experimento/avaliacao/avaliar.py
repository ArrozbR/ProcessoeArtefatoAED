"""Avalia todas as execuções do experimento e gera o relatório de resultados.

Estrutura esperada:
    experimento/execucoes/<grupo>/<execucao>/biblioteca.py   (ou resposta.md com a resposta bruta da LLM)

Uso:
    python avaliar.py                 # avalia tudo e grava experimento/resultados/
    python avaliar.py --referencia    # valida o oráculo contra a implementação de referência

Saídas (em experimento/resultados/):
    resultados.md   — tabelas, estatística e decisão sobre as hipóteses (para o relatório técnico)
    execucoes.csv   — conformidade por execução
    criterios.csv   — resultado de cada critério em cada execução
"""

import argparse
import csv
import json
import re
import subprocess
import sys
from collections import defaultdict
from pathlib import Path

import estatistica as est

AQUI = Path(__file__).resolve().parent
EXPERIMENTO = AQUI.parent
PADRAO_CODIGO = re.compile(r"```[ \t]*(?:python|py)?[ \t]*\r?\n(.*?)```", re.DOTALL | re.IGNORECASE)


def criterios_do_oraculo() -> list[str]:
    texto = (AQUI / "testes_aceitacao.py").read_text(encoding="utf-8")
    return sorted(set(re.findall(r'@criterio\("([^"]+)"\)', texto)), key=ordem_criterio)


def criterios_da_especificacao(caminho: Path) -> list[str]:
    texto = caminho.read_text(encoding="utf-8")
    return sorted(set(re.findall(r"\*\*((?:CA|CV)-\d\d\.\d+)\*\*", texto)), key=ordem_criterio)


def ordem_criterio(cid: str):
    tipo, num = cid.split("-")
    req, sub = num.split(".")
    return (tipo == "CV", int(req), int(sub))


def requisito_de(cid: str) -> str:
    tipo, num = cid.split("-")
    return ("RF-" if tipo == "CA" else "RNF-") + num.split(".")[0]


def preparar_codigo(pasta: Path) -> str:
    """Garante biblioteca.py; extrai da resposta bruta se preciso. Devolve observação."""
    if (pasta / "biblioteca.py").exists():
        return ""
    for nome in ("resposta.md", "resposta.txt"):
        bruto = pasta / nome
        if bruto.exists():
            texto = bruto.read_text(encoding="utf-8")
            blocos = PADRAO_CODIGO.findall(texto)
            codigo = max(blocos, key=len) if blocos else texto
            (pasta / "biblioteca.py").write_text(codigo, encoding="utf-8")
            if len(blocos) > 1:
                return f"extraído de {nome}: {len(blocos)} blocos, usado o maior"
            return f"extraído de {nome}" + ("" if blocos else " (sem bloco de código; texto inteiro)")
    return "sem biblioteca.py nem resposta.md"


def avaliar_execucao(pasta: Path, criterios: list[str]) -> tuple[dict[str, bool], dict[str, str], str]:
    obs = preparar_codigo(pasta)
    resultado = {c: False for c in criterios}
    detalhes: dict[str, str] = {}
    if not (pasta / "biblioteca.py").exists():
        return resultado, detalhes, obs
    try:
        proc = subprocess.run([sys.executable, str(AQUI / "executor.py"), str(pasta)],
                              capture_output=True, text=True, timeout=900)
        dados = json.loads(proc.stdout.strip().splitlines()[-1])
    except (subprocess.TimeoutExpired, json.JSONDecodeError, IndexError) as erro:
        return resultado, detalhes, (obs + "; " if obs else "") + f"falha do executor: {erro!r}"[:200]
    if dados["erro_importacao"]:
        obs = (obs + "; " if obs else "") + f"erro ao importar: {dados['erro_importacao']}"[:200]
    for cid, info in dados["criterios"].items():
        resultado[cid] = info["atendido"]
        if info["falhas"]:
            detalhes[cid] = " | ".join(info["falhas"])
    return resultado, detalhes, obs


def pct(x: float) -> str:
    return f"{100 * x:.1f}%".replace(".", ",")


def num(x: float, casas: int = 4) -> str:
    return f"{x:.{casas}f}".replace(".", ",")


def p_valor(p: float) -> str:
    return "< 0,0001" if p < 1e-4 else num(p)


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--execucoes", type=Path, default=EXPERIMENTO / "execucoes")
    ap.add_argument("--saida", type=Path, default=EXPERIMENTO / "resultados")
    ap.add_argument("--especificacao", type=Path, default=EXPERIMENTO / "especificacao" / "ERS-BIB-001.md")
    ap.add_argument("--meta", type=float, default=0.85)
    ap.add_argument("--alfa", type=float, default=0.05)
    ap.add_argument("--referencia", action="store_true", help="avalia somente a implementação de referência")
    args = ap.parse_args()
    sys.stdout.reconfigure(encoding="utf-8")
    sys.stderr.reconfigure(encoding="utf-8")

    criterios = criterios_do_oraculo()
    if args.referencia:
        res, det, obs = avaliar_execucao(EXPERIMENTO / "referencia", criterios)
        ok = sum(res.values())
        print(f"Referência: {ok}/{len(criterios)} critérios atendidos ({pct(ok / len(criterios))}). {obs}")
        for cid, texto in det.items():
            print(f"  {cid}: {texto}")
        sys.exit(0 if ok == len(criterios) else 1)

    grupos: dict[str, list[tuple[str, dict, dict, str]]] = {}
    for pasta_grupo in sorted(p for p in args.execucoes.iterdir() if p.is_dir()):
        for pasta in sorted(p for p in pasta_grupo.iterdir() if p.is_dir()):
            print(f"Avaliando {pasta_grupo.name}/{pasta.name} ...", flush=True)
            res, det, obs = avaliar_execucao(pasta, criterios)
            grupos.setdefault(pasta_grupo.name, []).append((pasta.name, res, det, obs))
    if not grupos:
        sys.exit(f"Nenhuma execução encontrada em {args.execucoes}")

    args.saida.mkdir(parents=True, exist_ok=True)
    _gravar_csvs(args.saida, grupos, criterios)
    md = _relatorio(grupos, criterios, args)
    (args.saida / "resultados.md").write_text(md, encoding="utf-8")
    print(f"\nResultados gravados em {args.saida}")


def _gravar_csvs(saida: Path, grupos, criterios) -> None:
    with open(saida / "execucoes.csv", "w", newline="", encoding="utf-8") as f:
        w = csv.writer(f)
        w.writerow(["grupo", "execucao", "atendidos", "total", "conformidade", "observacao"])
        for g, execs in grupos.items():
            for nome, res, _, obs in execs:
                ok = sum(res.values())
                w.writerow([g, nome, ok, len(criterios), f"{ok / len(criterios):.4f}", obs])
    with open(saida / "criterios.csv", "w", newline="", encoding="utf-8") as f:
        w = csv.writer(f)
        w.writerow(["grupo", "execucao", "criterio", "requisito", "atendido", "detalhe"])
        for g, execs in grupos.items():
            for nome, res, det, _ in execs:
                for cid in criterios:
                    w.writerow([g, nome, cid, requisito_de(cid), int(res[cid]), det.get(cid, "")])


def _relatorio(grupos, criterios, args) -> str:
    k = len(criterios)
    meta, alfa = args.meta, args.alfa
    linhas = ["# Resultados do experimento", "",
              "> Arquivo gerado por `experimento/avaliacao/avaliar.py`. Não edite à mão; rode o script de novo.", ""]

    na_espec = criterios_da_especificacao(args.especificacao)
    sem_teste = sorted(set(na_espec) - set(criterios), key=ordem_criterio)
    sem_espec = sorted(set(criterios) - set(na_espec), key=ordem_criterio)
    linhas += ["## Cobertura do oráculo", "",
               f"- Critérios na especificação: **{len(na_espec)}**; critérios com teste: **{k}**; "
               f"cobertura: **{pct(len(set(na_espec) & set(criterios)) / max(len(na_espec), 1))}**.",
               f"- Critérios sem teste: {', '.join(sem_teste) or 'nenhum'}.",
               f"- Testes sem critério na especificação: {', '.join(sem_espec) or 'nenhum'}.", ""]

    medidas: dict[str, list[float]] = {}
    for g, execs in grupos.items():
        c = [sum(r.values()) / k for _, r, _, _ in execs]
        medidas[g] = c
        linhas += [f"## Grupo `{g}`", "", "### Conformidade por execução", "",
                   "| Execução | Critérios atendidos | Conformidade | Atinge a meta? | Observação |",
                   "|---|---|---|---|---|"]
        for (nome, r, _, obs), ci in zip(execs, c):
            linhas.append(f"| {nome} | {sum(r.values())}/{k} | {pct(ci)} | {'sim' if ci >= meta else 'não'} | {obs} |")

        d = est.descritiva(c)
        linhas += ["", "### Estatística descritiva", "",
                   "| n | Média | Desvio-padrão | Mediana | Mínimo | Máximo | Execuções ≥ meta |",
                   "|---|---|---|---|---|---|---|",
                   f"| {d['n']} | {pct(d['media'])} | {pct(d['dp'])} | {pct(d['mediana'])} | {pct(d['min'])} | "
                   f"{pct(d['max'])} | {sum(x >= meta for x in c)}/{d['n']} |", ""]

        if len(c) >= 2:
            w, p_w, n_w = est.wilcoxon_unilateral(c, meta)
            t, p_t = est.t_unilateral(c, meta)
            li, ls = est.ic_bootstrap(c)
            decisao = "**H0 rejeitada**" if p_w < alfa else "**H0 não rejeitada**"
            linhas += ["### Teste de hipótese (uma amostra, unilateral)", "",
                       f"- H0: μ ≤ {pct(meta)} · H1: μ > {pct(meta)} · α = {num(alfa, 2)}",
                       f"- **Primário — Wilcoxon de postos sinalizados (exato):** W+ = {num(w, 1)}, "
                       f"n efetivo = {n_w}, p = {p_valor(p_w)} → {decisao}.",
                       f"- Secundário — t de Student: t = {num(t, 3)}, gl = {len(c) - 1}, p = {p_valor(p_t)}.",
                       f"- IC 95% bootstrap da média (10.000 reamostras): [{pct(li)}; {pct(ls)}].", ""]
            if n_w and 0.5 ** n_w >= alfa:
                linhas += [f"> ⚠️ Com n efetivo = {n_w}, o menor p possível do Wilcoxon é {num(0.5 ** n_w)} ≥ α: "
                           "aumente o número de execuções.", ""]

        linhas += ["### Conformidade por requisito (média das execuções)", "",
                   "| Requisito | Critérios | Conformidade média |", "|---|---|---|"]
        por_req = defaultdict(list)
        for cid in criterios:
            por_req[requisito_de(cid)].append(cid)
        for req, cids in por_req.items():
            taxa = sum(r[cid] for _, r, _, _ in execs for cid in cids) / (len(cids) * len(execs))
            linhas.append(f"| {req} | {len(cids)} | {pct(taxa)} |")

        falhas = []
        for cid in criterios:
            n_falhas = sum(not r[cid] for _, r, _, _ in execs)
            if n_falhas:
                exemplos = [det[cid] for _, _, det, _ in execs if cid in det]
                exemplo = next((e for e in exemplos if "não foi possível importar" not in e), exemplos[0] if exemplos else "")
                falhas.append((n_falhas, cid, exemplo))
        linhas += ["", "### Critérios reprovados (insumo para a análise de causa-raiz D/G/T)", ""]
        if falhas:
            linhas += ["| Critério | Execuções reprovadas | Exemplo de falha | Causa (D/G/T) |", "|---|---|---|---|"]
            for n_f, cid, ex in sorted(falhas, key=lambda f: (-f[0], ordem_criterio(f[1]))):
                ex = ex.replace("|", "\\|")[:160]
                linhas.append(f"| {cid} | {n_f}/{len(execs)} | `{ex}` | _preencher_ |")
        else:
            linhas.append("Nenhum critério reprovado.")
        linhas.append("")

    nomes = list(medidas)
    if len(nomes) >= 2:
        a, b = nomes[0], nomes[1]
        p = est.permutacao_unilateral(medidas[a], medidas[b])
        dif = est.descritiva(medidas[a])["media"] - est.descritiva(medidas[b])["media"]
        linhas += [f"## Comparação entre grupos: `{a}` × `{b}`", "",
                   f"- H0: μ({a}) ≤ μ({b}) · H1: μ({a}) > μ({b}) · α = {num(alfa, 2)}",
                   f"- Diferença das médias: {num(100 * dif, 1)} pontos percentuais.",
                   f"- Teste de permutação (unilateral): p = {p_valor(p)} → "
                   f"{'**H0 rejeitada**' if p < alfa else '**H0 não rejeitada**'}.",
                   f"- Tamanho de efeito (delta de Cliff): {num(est.delta_de_cliff(medidas[a], medidas[b]), 3)}.", ""]
    return "\n".join(linhas)


if __name__ == "__main__":
    main()
