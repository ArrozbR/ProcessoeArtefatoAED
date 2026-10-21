# arquivo: servicos/cotacao/main.py
import math
import os
import re
import threading
from datetime import date, timedelta
from fractions import Fraction

import httpx
from fastapi import FastAPI, Request
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse, Response
from pydantic import BaseModel, Field, StrictInt, StrictStr, field_validator
from starlette.exceptions import HTTPException as StarletteHTTPException

app = FastAPI()

RE_DATA = re.compile(r"[0-9]{4}-[0-9]{2}-[0-9]{2}")

FAIXAS_UF = [
    ("SP", 1000000, 19999999),
    ("RJ", 20000000, 28999999),
    ("ES", 29000000, 29999999),
    ("MG", 30000000, 39999999),
    ("BA", 40000000, 48999999),
    ("PR", 80000000, 87999999),
    ("SC", 88000000, 89999999),
    ("RS", 90000000, 99999999),
]


class ErroAPI(Exception):
    def __init__(self, status: int, codigo: str, mensagem: str):
        self.status = status
        self.codigo = codigo
        self.mensagem = mensagem


@app.exception_handler(ErroAPI)
async def _erro_api(request: Request, exc: ErroAPI):
    return JSONResponse(status_code=exc.status, content={"erro": exc.codigo, "mensagem": exc.mensagem})


@app.exception_handler(RequestValidationError)
async def _erro_validacao(request: Request, exc: RequestValidationError):
    return JSONResponse(status_code=422, content={"erro": "DADOS_INVALIDOS", "mensagem": "Dados inválidos."})


@app.exception_handler(StarletteHTTPException)
async def _erro_http(request: Request, exc: StarletteHTTPException):
    codigo = "NAO_ENCONTRADO" if exc.status_code == 404 else "ERRO_HTTP"
    return JSONResponse(status_code=exc.status_code, content={"erro": codigo, "mensagem": str(exc.detail)})


class Pacote(BaseModel):
    peso_g: StrictInt = Field(ge=1, le=100000)
    comprimento_cm: StrictInt = Field(ge=1, le=200)
    largura_cm: StrictInt = Field(ge=1, le=200)
    altura_cm: StrictInt = Field(ge=1, le=200)


class CotacaoEntrada(BaseModel):
    cep_origem: StrictStr = Field(pattern=r"^[0-9]{8}$")
    cep_destino: StrictStr = Field(pattern=r"^[0-9]{8}$")
    pacote: Pacote
    data: StrictStr

    @field_validator("data")
    @classmethod
    def _data_valida(cls, v):
        if not RE_DATA.fullmatch(v):
            raise ValueError("data inválida")
        date.fromisoformat(v)
        return v


_lock = threading.Lock()
_cotacoes = {}
_contador = [0]


def _uf_do_cep(cep: str):
    n = int(cep)
    for uf, ini, fim in FAIXAS_UF:
        if ini <= n <= fim:
            return uf
    return None


def _somar_dias_uteis(inicio: date, dias: int) -> date:
    d = inicio
    contados = 0
    while contados < dias:
        d = d + timedelta(days=1)
        if d.weekday() < 5:
            contados += 1
    return d


def _buscar_transportadoras():
    base = os.environ.get("URL_TRANSPORTADORAS", "")
    try:
        with httpx.Client(timeout=10.0) as cliente:
            r = cliente.get(base + "/transportadoras", params={"somente_ativas": "true"})
    except Exception:
        raise ErroAPI(503, "SERVICO_INDISPONIVEL", "Serviço de transportadoras indisponível.")
    if r.status_code >= 500 or r.status_code != 200:
        raise ErroAPI(503, "SERVICO_INDISPONIVEL", "Serviço de transportadoras indisponível.")
    try:
        dados = r.json()
    except Exception:
        raise ErroAPI(503, "SERVICO_INDISPONIVEL", "Resposta inválida do serviço de transportadoras.")
    if not isinstance(dados, list):
        raise ErroAPI(503, "SERVICO_INDISPONIVEL", "Resposta inválida do serviço de transportadoras.")
    return dados


@app.post("/admin/reset", status_code=204)
def reset():
    with _lock:
        _cotacoes.clear()
        _contador[0] = 0
    return Response(status_code=204)


@app.post("/cotacoes", status_code=201)
def criar(entrada: CotacaoEntrada):
    uf_origem = _uf_do_cep(entrada.cep_origem)
    uf_destino = _uf_do_cep(entrada.cep_destino)
    if uf_origem is None or uf_destino is None:
        raise ErroAPI(422, "CEP_FORA_DA_AREA", "CEP fora da área atendida.")

    p = entrada.pacote
    volume = p.comprimento_cm * p.largura_cm * p.altura_cm
    peso_cubado = -(-volume // 6)
    peso_taxado = max(p.peso_g, peso_cubado)
    quilos = -(-peso_taxado // 1000)
    data_cotacao = date.fromisoformat(entrada.data)
    interestadual = uf_origem != uf_destino

    transportadoras = _buscar_transportadoras()

    opcoes = []
    for t in transportadoras:
        try:
            ufs = t.get("ufs_atendidas") or []
            if t.get("ativa") is False:
                continue
            if uf_origem not in ufs or uf_destino not in ufs:
                continue
            if t["peso_maximo_g"] < peso_taxado:
                continue
            preco = t["tarifa_base_centavos"] + t["tarifa_kg_centavos"] * quilos
            prazo = t["prazo_dias_uteis"]
            if interestadual:
                adicional = Fraction(str(t["adicional_interestadual_pct"]))
                preco = math.ceil(Fraction(preco) * (100 + adicional) / 100)
                prazo = prazo + t["dias_adicionais_interestadual"]
            opcoes.append({
                "transportadora_id": t["id"],
                "nome": t["nome"],
                "preco_centavos": int(preco),
                "prazo_dias_uteis": int(prazo),
                "data_prevista_entrega": _somar_dias_uteis(data_cotacao, int(prazo)).isoformat(),
            })
        except (KeyError, TypeError, ValueError):
            continue

    opcoes.sort(key=lambda o: (o["preco_centavos"], o["prazo_dias_uteis"], o["transportadora_id"]))

    with _lock:
        _contador[0] += 1
        cid = "COT-%06d" % _contador[0]
        cotacao = {
            "id": cid,
            "cep_origem": entrada.cep_origem,
            "cep_destino": entrada.cep_destino,
            "uf_origem": uf_origem,
            "uf_destino": uf_destino,
            "peso_taxado_g": peso_taxado,
            "data": entrada.data,
            "valida_ate": (data_cotacao + timedelta(days=7)).isoformat(),
            "opcoes": opcoes,
        }
        _cotacoes[cid] = cotacao
        return cotacao


@app.get("/cotacoes/{id}")
def obter(id: str):
    with _lock:
        c = _cotacoes.get(id)
        if c is None:
            raise ErroAPI(404, "COTACAO_NAO_ENCONTRADA", "Cotação não encontrada.")
        return c
