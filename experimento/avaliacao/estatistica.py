"""Estatística usada no experimento, somente com a biblioteca padrão.

- Wilcoxon de postos sinalizados, unilateral, exato (teste primário).
- t de Student para uma amostra, unilateral (teste secundário).
- Intervalo de confiança bootstrap (percentil) da média.
- Teste de permutação para duas amostras independentes, unilateral, e delta de Cliff.
"""

import math
import random
from itertools import combinations
from statistics import mean, median, stdev

SEMENTE = 2026


def descritiva(x: list[float]) -> dict:
    return {
        "n": len(x),
        "media": mean(x),
        "dp": stdev(x) if len(x) > 1 else 0.0,
        "mediana": median(x),
        "min": min(x),
        "max": max(x),
    }


def wilcoxon_unilateral(x: list[float], mu0: float) -> tuple[float, float, int]:
    """H0: mediana ≤ mu0; H1: mediana > mu0. Devolve (W+, p, n sem empates com mu0).

    Distribuição exata por programação dinâmica sobre os postos (empates recebem
    posto médio; postos são dobrados para ficarem inteiros).
    """
    d = [round(v - mu0, 12) for v in x]
    d = [v for v in d if v != 0]
    n = len(d)
    if n == 0:
        return 0.0, 1.0, 0
    ordem = sorted(range(n), key=lambda i: abs(d[i]))
    postos = [0.0] * n
    i = 0
    while i < n:
        j = i
        while j + 1 < n and abs(d[ordem[j + 1]]) == abs(d[ordem[i]]):
            j += 1
        for k in range(i, j + 1):
            postos[ordem[k]] = (i + j) / 2 + 1
        i = j + 1
    w_mais = sum(p for p, v in zip(postos, d) if v > 0)

    dobrados = [round(2 * p) for p in postos]
    contagem = {0: 1}
    for r in dobrados:
        nova = dict(contagem)
        for soma, vezes in contagem.items():
            nova[soma + r] = nova.get(soma + r, 0) + vezes
        contagem = nova
    alvo = round(2 * w_mais)
    p = sum(v for s, v in contagem.items() if s >= alvo) / 2 ** n
    return w_mais, p, n


def t_unilateral(x: list[float], mu0: float) -> tuple[float, float]:
    """H0: μ ≤ mu0; H1: μ > mu0. Devolve (t, p)."""
    n = len(x)
    m = mean(x)
    s = stdev(x) if n > 1 else 0.0
    if s == 0:
        return (math.inf, 0.0) if m > mu0 else (-math.inf if m < mu0 else 0.0, 1.0)
    t = (m - mu0) / (s / math.sqrt(n))
    return t, 1 - _t_cdf(t, n - 1)


def _t_cdf(t: float, gl: int) -> float:
    x = gl / (gl + t * t)
    cauda = 0.5 * _beta_incompleta(gl / 2, 0.5, x)
    return 1 - cauda if t > 0 else cauda


def _beta_incompleta(a: float, b: float, x: float) -> float:
    """Função beta incompleta regularizada I_x(a, b) (fração contínua de Lentz)."""
    if x <= 0:
        return 0.0
    if x >= 1:
        return 1.0
    ln_frente = math.lgamma(a + b) - math.lgamma(a) - math.lgamma(b) + a * math.log(x) + b * math.log(1 - x)
    if x > (a + 1) / (a + b + 2):
        return 1 - _beta_incompleta(b, a, 1 - x)
    tiny = 1e-300
    f, c, dd = 1.0, 1.0, 0.0
    for i in range(400):
        m = i // 2
        if i == 0:
            num = 1.0
        elif i % 2 == 0:
            num = m * (b - m) * x / ((a + 2 * m - 1) * (a + 2 * m))
        else:
            num = -((a + m) * (a + b + m) * x) / ((a + 2 * m) * (a + 2 * m + 1))
        dd = 1 + num * dd
        dd = 1 / (dd if abs(dd) > tiny else tiny)
        c = 1 + num / c
        c = c if abs(c) > tiny else tiny
        f *= c * dd
        if abs(1 - c * dd) < 1e-14:
            break
    return math.exp(ln_frente) * (f - 1) / a


def ic_bootstrap(x: list[float], confianca: float = 0.95, reamostras: int = 10_000) -> tuple[float, float]:
    rnd = random.Random(SEMENTE)
    medias = sorted(mean(rnd.choices(x, k=len(x))) for _ in range(reamostras))
    alfa = (1 - confianca) / 2
    return medias[int(alfa * reamostras)], medias[int((1 - alfa) * reamostras) - 1]


def permutacao_unilateral(a: list[float], b: list[float]) -> float:
    """H0: μa ≤ μb; H1: μa > μb. p exato se viável; senão Monte Carlo (100 mil)."""
    obs = mean(a) - mean(b)
    todos = a + b
    na, total = len(a), sum(a) + sum(b)
    n = len(todos)

    def diferenca(soma_a: float) -> float:
        return soma_a / na - (total - soma_a) / (n - na)

    if math.comb(n, na) <= 200_000:
        extremos = casos = 0
        for idx in combinations(range(n), na):
            casos += 1
            extremos += diferenca(sum(todos[i] for i in idx)) >= obs - 1e-12
        return extremos / casos
    rnd = random.Random(SEMENTE)
    extremos = 0
    for _ in range(100_000):
        amostra = rnd.sample(todos, n)
        extremos += diferenca(sum(amostra[:na])) >= obs - 1e-12
    return (extremos + 1) / 100_001


def delta_de_cliff(a: list[float], b: list[float]) -> float:
    return sum((x > y) - (x < y) for x in a for y in b) / (len(a) * len(b))
