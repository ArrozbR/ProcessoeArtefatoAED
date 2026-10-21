# arquivo: servicos/cotacao/main.py
import os
import re
import threading
from datetime import date, timedelta
from fractions import Fraction
from typing import Annotated

import httpx
from fastapi import FastAPI, Request
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse, Response
from pydantic import BaseModel, Field, field_validator
from starlette.exceptions import HTTPException as StarletteHTTPException


class ErroAPI(Exception):
    def __init__(self, status: int, codigo: str, mensagem: str):
        super().__init__(mensagem)
        self.status = status
        self.codigo = codigo
        self.mensagem = mensagem


def _erro(status: int, codigo: str, mensagem: str) -> JSONResponse:
    return JSONResponse(status_code=status, content={"erro": codigo, "mensagem": mensagem})


app = FastAPI(title="Cotacao")


@app.exception_handler(ErroAPI)
async def _tratar_erro_api(request: Request, exc: ErroAPI):
    return _erro(exc.status, exc.codigo, exc.mensagem)


@app.exception_handler(RequestValidationError)
async def _tratar_validacao(request: Request, exc: RequestValidationError):
    partes = []
    for e in exc.errors():
        loc = ".".join(str(x) for x in e.get("loc", ()))
        partes.append(f"{loc}: {e.get('msg', '')}")
    return _erro(422, "DADOS_INVALIDOS", "; ".join(partes) or "Dados invalidos.")


@app.exception_handler(StarletteHTTPException)
async def _tratar_http(request: Request, exc: StarletteHTTPException):
    if exc.status_code == 404:
        return _erro(404, "NAO_ENCONTRADO", "Recurso nao encontrado.")
    if exc.status_code == 405:
        return _erro(405, "METODO_NAO_PERMITIDO", "Metodo nao permitido.")
    return _erro(exc.status_code, "ERRO", str(exc.detail))


_RE_DATA = re.compile(r"^[0-9]{4}-[0-9]{2}-[0-9]{2}$")

FAIXAS_CEP = [
    ("SP", 1000000, 19999999),
    ("RJ", 20000000, 28999999),
    ("ES", 29000000, 29999999),
    ("MG", 30000000, 39999999),
    ("BA", 40000000, 48999999),
    ("PR", 80000000, 87999999),
    ("SC", 88000000, 89999999),
    ("RS", 90000000, 99999999),
]

_trava = threading.RLock()
_cotacoes: dict = {}
_contador = {"cotacao": 0}

_cliente_http = None
_trava_cliente = threading.Lock()


def _cliente() -> httpx.Client:
    global _cliente_http
    with _trava_cliente:
        if _cliente_http is None:
            _cliente_http = httpx.Client(timeout=10.0)
        return _cliente_http


class Pacote(BaseModel):
    peso_g: Annotated[int, Field(strict=True, ge=1, le=100000)]
    comprimento_cm: Annotated[int, Field(strict=True, ge=1, le=200)]
    largura_cm: Annotated[int, Field(strict=True, ge=1, le=200)]
    altura_cm: Annotated[int, Field(strict=True, ge=1, le=200)]


class CotacaoEntrada(BaseModel):
    cep_origem: Annotated[str, Field(strict=True, pattern=r"^[0-9]{8}$")]
    cep_destino: Annotated[str, Field(strict=True, pattern=r"^[0-9]{8}$")]
    pacote: Pacote
    data: Annotated[str, Field(strict=True)]

    @field_validator("data")
    @classmethod
    def _validar_data(cls, v: str) -> str:
        if not _RE_DATA.match(v):
            raise ValueError("data deve estar no formato AAAA-MM-DD")
        date.fromisoformat(v)
        return v


def _uf_do_cep(cep: str):
    n = int(cep)
    for uf, ini, fim in FAIXAS_CEP:
        if ini <= n <= fim:
            return uf
    return None


def _somar_dias_uteis(inicio: date, dias: int) -> date:
    atual = inicio
    contados = 0
    while contados < dias:
        atual = atual + timedelta(days=1)
        if atual.weekday() < 5:
            contados += 1
    return atual


def _teto_div(a: int, b: int) -> int:
    return -((-a) // b)


def _buscar_transportadoras_ativas() -> list:
    base = os.environ.get("URL_TRANSPORTADORAS", "")
    try:
        resp = _cliente().get(f"{base}/transportadoras", params={"somente_ativas": "true"})
    except Exception:
        raise ErroAPI(503, "SERVICO_INDISPONIVEL", "Servico de transportadoras indisponivel.")
    if resp.status_code >= 500 or resp.status_code != 200:
        raise ErroAPI(503, "SERVICO_INDISPONIVEL", "Servico de transportadoras indisponivel.")
    try:
        dados = resp.json()
    except Exception:
        raise ErroAPI(503, "SERVICO_INDISPONIVEL", "Resposta invalida do servico de transportadoras.")
    if not isinstance(dados, list):
        raise ErroAPI(503, "SERVICO_INDISPONIVEL", "Resposta invalida do servico de transportadoras.")
    return [t for t in dados if isinstance(t, dict)]


def _calcular_opcao(t: dict, uf_origem: str, uf_destino: str, peso_taxado: int, data_cot: date):
    try:
        if not t.get("ativa", True):
            return None
        ufs = t.get("ufs_atendidas") or []
        if uf_origem not in ufs or uf_destino not in ufs:
            return None
        if int(t["peso_maximo_g"]) < peso_taxado:
            return None
        quilos = _teto_div(peso_taxado, 1000)
        preco = int(t["tarifa_base_centavos"]) + int(t["tarifa_kg_centavos"]) * quilos
        prazo = int(t["prazo_dias_uteis"])
        if uf_origem != uf_destino:
            adicional = Fraction(str(t.get("adicional_interestadual_pct", 0)))
            valor = Fraction(preco) * (100 + adicional) / 100
            preco = -((-valor.numerator) // valor.denominator)
            prazo += int(t.get("dias_adicionais_interestadual", 0))
        entrega = _somar_dias_uteis(data_cot, prazo)
        return {
            "transportadora_id": t["id"],
            "nome": t["nome"],
            "preco_centavos": int(preco),
            "prazo_dias_uteis": prazo,
            "data_prevista_entrega": entrega.isoformat(),
        }
    except (KeyError, TypeError, ValueError):
        return None


def _copia(c: dict) -> dict:
    r = dict(c)
    r["opcoes"] = [dict(o) for o in c["opcoes"]]
    return r


@app.post("/admin/reset", status_code=204)
def reset():
    with _trava:
        _cotacoes.clear()
        _contador["cotacao"] = 0
    return Response(status_code=204)


@app.post("/cotacoes", status_code=201)
def criar_cotacao(entrada: CotacaoEntrada):
    uf_origem = _uf_do_cep(entrada.cep_origem)
    uf_destino = _uf_do_cep(entrada.cep_destino)
    if uf_origem is None or uf_destino is None:
        raise ErroAPI(422, "CEP_FORA_DA_AREA", "CEP fora da area atendida.")

    p = entrada.pacote
    peso_cubado = _teto_div(p.comprimento_cm * p.largura_cm * p.altura_cm, 6)
    peso_taxado = max(p.peso_g, peso_cubado)
    data_cot = date.fromisoformat(entrada.data)

    transportadoras = _buscar_transportadoras_ativas()
    opcoes = []
    for t in transportadoras:
        op = _calcular_opcao(t, uf_origem, uf_destino, peso_taxado, data_cot)
        if op is not None:
            opcoes.append(op)
    opcoes.sort(key=lambda o: (o["preco_centavos"], o["prazo_dias_uteis"], o["transportadora_id"]))

    with _trava:
        _contador["cotacao"] += 1
        cid = f"COT-{_contador['cotacao']:06d}"
        cotacao = {
            "id": cid,
            "cep_origem": entrada.cep_origem,
            "cep_destino": entrada.cep_destino,
            "uf_origem": uf_origem,
            "uf_destino": uf_destino,
            "peso_taxado_g": peso_taxado,
            "data": entrada.data,
            "valida_ate": (data_cot + timedelta(days=7)).isoformat(),
            "opcoes": opcoes,
        }
        _cotacoes[cid] = cotacao
        return JSONResponse(status_code=201, content=_copia(cotacao))


@app.get("/cotacoes/{id}")
def obter_cotacao(id: str):
    with _trava:
        c = _cotacoes.get(id)
        if c is None:
            raise ErroAPI(404, "COTACAO_NAO_ENCONTRADA", f"Cotacao {id} nao encontrada.")
        return _copia(c)
