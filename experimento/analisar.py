"""Analisa resultados/execucoes.csv e escreve resultados/resumo.md com as estatísticas do relatório."""
import csv
import statistics
import sys
from collections import Counter
from pathlib import Path

import numpy as np
from scipy import stats

RAIZ = Path(__file__).resolve().parent
CSV = RAIZ / "resultados" / "execucoes.csv"
SAIDA = RAIZ / "resultados" / "resumo.md"
LIMIAR = 0.85
ALFA = 0.05


GRUPOS_CA = [
    ("Transportadoras", lambda i: i.startswith("CA-T")),
    ("Cotação", lambda i: i.startswith("CA-C")),
    ("Envios", lambda i: i.startswith("CA-E")),
    ("Rastreamento", lambda i: i.startswith("CA-R0")),
    ("Notificações", lambda i: i.startswith("CA-N")),
    ("Transversais (reset, RNF, integrado)", lambda i: i.startswith(("CA-X", "CA-RNF", "CA-INT"))),
]


def _ids_criterios():
    import re
    texto = (RAIZ / "especificacoes" / "ERS-LOG-001.md").read_text(encoding="utf-8")
    return re.findall(r"\*\*(CA-[A-Z0-9]+-\d{2})\*\*", texto)


def carregar():
    with CSV.open(encoding="utf-8", newline="") as f:
        linhas = list(csv.DictReader(f))
    grupos = {}
    for l in linhas:
        grupos.setdefault(l["condicao"], []).append(l)
    return grupos


def descrever(valores):
    x = np.array(valores)
    if np.ptp(x) == 0:
        ic_inf = ic_sup = x[0]
    else:
        ic = stats.bootstrap((x,), np.mean, confidence_level=0.95, n_resamples=10000,
                             method="percentile", random_state=2026).confidence_interval
        ic_inf, ic_sup = ic.low, ic.high
    return {
        "n": len(x),
        "media": x.mean(),
        "dp": x.std(ddof=1) if len(x) > 1 else 0.0,
        "mediana": float(np.median(x)),
        "min": x.min(),
        "max": x.max(),
        "ic_inf": ic_inf,
        "ic_sup": ic_sup,
        "acima": int((x >= LIMIAR).sum()),
    }


def pct(v):
    return f"{100 * v:.1f}%"


def main():
    sys.stdout.reconfigure(encoding="utf-8")
    grupos = carregar()
    conf = {c: [float(l["conformidade"]) for l in ls] for c, ls in grupos.items()}
    out = ["# Resumo estatístico", ""]
    l0 = next(iter(grupos.values()))[0]
    out += [f"Modelo: `{l0['modelo']}` · CAs por execução: {l0['total_ca']}", ""]

    out += ["## Estatística descritiva", "",
            "| Condição | n | Média | DP | Mediana | Mín | Máx | IC 95% da média (bootstrap) | Execuções ≥ 85% |",
            "|---|---|---|---|---|---|---|---|---|"]
    for c, v in conf.items():
        d = descrever(v)
        out.append(f"| {c} | {d['n']} | {pct(d['media'])} | {pct(d['dp'])} | {pct(d['mediana'])} | "
                   f"{pct(d['min'])} | {pct(d['max'])} | [{pct(d['ic_inf'])}; {pct(d['ic_sup'])}] | "
                   f"{d['acima']}/{d['n']} |")
    situacoes = Counter((l["condicao"], l["situacao"]) for ls in grupos.values() for l in ls)
    out += ["", "Situação das execuções: " + ", ".join(f"{c}/{s}: {n}" for (c, s), n in sorted(situacoes.items())), ""]

    if "erl" in conf:
        x = np.array(conf["erl"])
        w = stats.wilcoxon(x - LIMIAR, alternative="greater")
        out += ["## H1 — conformidade da condição ERL acima de 85%", "",
                "H0: mediana (e média) da conformidade ≤ 0,85. H1: > 0,85. Teste unilateral, α = 0,05.", "",
                "| Teste | Estatística | p-valor | Decisão |", "|---|---|---|---|",
                f"| Wilcoxon de postos sinalizados (principal) | W = {w.statistic:.1f} | {w.pvalue:.2e} | "
                f"{'rejeita H0' if w.pvalue < ALFA else 'não rejeita H0'} |"]
        if np.ptp(x) == 0:
            out.append("| t de Student para uma amostra (complementar) | indefinido: variância zero | — | — |")
        else:
            t = stats.ttest_1samp(x, LIMIAR, alternative="greater")
            out.append(f"| t de Student para uma amostra (complementar) | t = {t.statistic:.2f} | {t.pvalue:.2e} | "
                       f"{'rejeita H0' if t.pvalue < ALFA else 'não rejeita H0'} |")
        binom = stats.binomtest(int((x > LIMIAR).sum()), len(x), 0.5, alternative="greater")
        out += [f"| Sinal (binomial, robusto a empates) | {int((x > LIMIAR).sum())}/{len(x)} acima | {binom.pvalue:.2e} | "
                f"{'rejeita H0' if binom.pvalue < ALFA else 'não rejeita H0'} |", ""]

    if "erl" in conf and "narrativa" in conf:
        a, b = np.array(conf["erl"]), np.array(conf["narrativa"])
        out += ["## H2 — ERL contra especificação narrativa", "",
                "H0: a conformidade da ERL não é maior que a da narrativa. H1: é maior. Teste unilateral, α = 0,05.", "",
                "| Teste | Estatística | p-valor | Tamanho de efeito | Decisão |", "|---|---|---|---|---|"]
        if np.ptp(np.concatenate([a, b])) == 0:
            out += ["| Mann-Whitney U | indefinido: todas as execuções têm o mesmo valor | — | r = 0 | não rejeita H0 |", ""]
        else:
            u = stats.mannwhitneyu(a, b, alternative="greater")
            rb = 2 * u.statistic / (len(a) * len(b)) - 1
            out += [f"| Mann-Whitney U | U = {u.statistic:.1f} | {u.pvalue:.2e} | correlação rank-biserial r = {rb:.2f} | "
                    f"{'rejeita H0' if u.pvalue < ALFA else 'não rejeita H0'} |", ""]

    ids_todos = _ids_criterios()
    out += ["## Conformidade por serviço", "",
            "| Serviço | CAs | " + " | ".join(f"média em {c}" for c in grupos) + " |",
            "|---|---|" + "---|" * len(grupos)]
    for servico, prefixo in GRUPOS_CA:
        ids = [i for i in ids_todos if prefixo(i)]
        celulas = []
        for c, ls in grupos.items():
            taxas = [1 - len(set(l["ca_reprovados"].split()) & set(ids)) / len(ids) for l in ls]
            celulas.append(pct(sum(taxas) / len(taxas)))
        out.append(f"| {servico} | {len(ids)} | " + " | ".join(celulas) + " |")
    out += ["", "Linhas de código geradas (média): " + ", ".join(
        f"{c}: {sum(int(l['linhas_codigo']) for l in ls) / len(ls):.0f}" for c, ls in grupos.items()), ""]

    out += ["## Critérios de aceitação mais reprovados", "",
            "| CA | " + " | ".join(f"reprovações em {c}" for c in grupos) + " |",
            "|---|" + "---|" * len(grupos)]
    contagem = {c: Counter(ca for l in ls for ca in l["ca_reprovados"].split()) for c, ls in grupos.items()}
    todos = Counter()
    for cnt in contagem.values():
        todos.update(cnt)
    for ca, _ in todos.most_common(20):
        out.append(f"| {ca} | " + " | ".join(f"{contagem[c][ca]}/{len(grupos[c])}" for c in grupos) + " |")
    if not todos:
        out.append("| — | " + " | ".join("0" for _ in grupos) + " |")

    SAIDA.write_text("\n".join(out) + "\n", encoding="utf-8")
    print("\n".join(out))


if __name__ == "__main__":
    main()
