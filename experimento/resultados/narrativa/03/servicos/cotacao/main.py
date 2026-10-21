# arquivo: servicos/cotacao/main.py
import copy
import os
import re
import threading
from datetime import date, timedelta
from typing import Annotated

import httpx
from fastapi import FastAPI
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse, Response
from pydantic import AfterValidator, BaseModel, Field
from starlette.exceptions import HTTPException as StarletteHTTPException


class ErroAPI(Exception):
    def __init__(self, status: int, codigo: str, mensagem: str):
        super().__init__(mensagem)
        self.status = status
        self.codigo = codigo
        self.mensagem = mensagem


app = FastAPI()


@app.exception_handler(ErroAPI)
async def _tratar_erro_api(request, exc: ErroAPI):
    return JSONResponse(status_code=exc.status, content={"erro": exc.codigo, "mensagem": exc.mensagem})


@app.exception_handler(RequestValidationError)
async def _tratar_validacao(request, exc):
    return JSONResponse(status_code=422, content={"erro": "DADOS_INVALIDOS", "mensagem": "Dados invalidos."})


@app.exception_handler(StarletteHTTPException)
async def _tratar_http(request, exc: StarletteHTTPException):
    codigo = "NAO_ENCONTRADO" if exc.status_code == 404 else "ERRO_HTTP"
    return JSONResponse(status_code=exc.status_code, content={"erro": codigo, "mensagem": str(exc.detail)})


_RE_DATA = re.compile(r"^[0-9]{4}-[0-9]{2}-[0-9]{2}$")


def _validar_data(v: str) -> str:
    if not _RE_DATA.match(v):
        raise ValueError("data mal formada")
    try:
        date.fromisoformat(v)
    except ValueError:
        raise ValueError("data invalida")
    return v


Data = Annotated[str, Field(strict=True), AfterValidator(_validar_data)]
Cep = Annotated[str, Field(strict=True, pattern=r"^[0-9]{8}$")]
Medida = Annotated[int, Field(strict=True, ge=1, le=200)]


class Pacote(BaseModel):
    peso_g: Annotated[int, Field(strict=True, ge=1, le=100000)]
    comprimento_cm: Medida
    largura_cm: Medida
    altura_cm: Medida


class NovaCotacao(BaseModel):
    cep_origem: Cep
    cep_destino: Cep
    pacote: Pacote
    data: Data


FAIXAS_CEP = [
    (1000000, 19999999, "SP"),
    (20000000, 28999999, "RJ"),
    (29000000, 29999999, "ES"),
    (30000000, 39999999, "MG"),
    (40000000, 48999999, "BA"),
    (80000000, 87999999, "PR"),
    (88000000, 89999999, "SC"),
    (90000000, 99999999, "RS"),
]


def _uf_do_cep(cep: str):
    n = int(cep)
    for ini, fim, uf in FAIXAS_CEP:
        if ini <= n <= fim:
            return uf
    return None


def _teto_div(a: int, b: int) -> int:
    return -(-a // b)


def _somar_dias_uteis(d: date, n: int) -> date:
    while n > 0:
        d = d + timedelta(days=1)
        if d.weekday() < 5:
            n -= 1
    return d


_cliente = httpx.Client(timeout=10.0)


def _buscar_transportadoras_ativas():
    url = os.environ.get("URL_TRANSPORTADORAS", "") + "/transportadoras"
    try:
        r = _cliente.get(url, params={"somente_ativas": "true"})
    except Exception:
        raise ErroAPI(503, "SERVICO_INDISPONIVEL", "Servico de transportadoras indisponivel.")
    if r.status_code != 200:
        raise ErroAPI(503, "SERVICO_INDISPONIVEL", "Servico de transportadoras indisponivel.")
    try:
        dados = r.json()
    except Exception:
        raise ErroAPI(503, "SERVICO_INDISPONIVEL", "Resposta invalida do servico de transportadoras.")
    if not isinstance(dados, list):
        raise ErroAPI(503, "SERVICO_INDISPONIVEL", "Resposta invalida do servico de transportadoras.")
    return dados


_trava = threading.RLock()
_cotacoes: dict = {}
_contador = {"cotacao": 0}


@app.post("/admin/reset", status_code=204)
def reset():
    with _trava:
        _cotacoes.clear()
        _contador["cotacao"] = 0
    return Response(status_code=204)


@app.post("/cotacoes", status_code=201)
def criar_cotacao(corpo: NovaCotacao):
    uf_origem = _uf_do_cep(corpo.cep_origem)
    uf_destino = _uf_do_cep(corpo.cep_destino)
    if uf_origem is None or uf_destino is None:
        raise ErroAPI(422, "CEP_FORA_DA_AREA", "CEP fora da area atendida.")

    p = corpo.pacote
    peso_cubado = _teto_div(p.comprimento_cm * p.largura_cm * p.altura_cm, 6)
    peso_taxado = max(p.peso_g, peso_cubado)
    kgs = _teto_div(peso_taxado, 1000)
    interestadual = uf_origem != uf_destino
    data_cotacao = date.fromisoformat(corpo.data)

    transportadoras = _buscar_transportadoras_ativas()

    opcoes = []
    for t in transportadoras:
        if not isinstance(t, dict):
            continue
        if t.get("ativa") is False:
            continue
        ufs = t.get("ufs_atendidas") or []
        if uf_origem not in ufs or uf_destino not in ufs:
            continue
        if t.get("peso_maximo_g", 0) < peso_taxado:
            continue
        preco = t["tarifa_base_centavos"] + t["tarifa_kg_centavos"] * kgs
        prazo = t["prazo_dias_uteis"]
        if interestadual:
            preco = _teto_div(preco * (100 + t["adicional_interestadual_pct"]), 100)
            prazo = prazo + t["dias_adicionais_interestadual"]
        opcoes.append({
            "transportadora_id": t["id"],
            "nome": t["nome"],
            "preco_centavos": preco,
            "prazo_dias_uteis": prazo,
            "data_prevista_entrega": _somar_dias_uteis(data_cotacao, prazo).isoformat(),
        })
    opcoes.sort(key=lambda o: (o["preco_centavos"], o["prazo_dias_uteis"], o["transportadora_id"]))

    with _trava:
        _contador["cotacao"] += 1
        id_cotacao = "COT-%06d" % _contador["cotacao"]
        cotacao = {
            "id": id_cotacao,
            "cep_origem": corpo.cep_origem,
            "cep_destino": corpo.cep_destino,
            "uf_origem": uf_origem,
            "uf_destino": uf_destino,
            "peso_taxado_g": peso_taxado,
            "data": corpo.data,
            "valida_ate": (data_cotacao + timedelta(days=7)).isoformat(),
            "opcoes": opcoes,
        }
        _cotacoes[id_cotacao] = cotacao
        return JSONResponse(status_code=201, content=copy.deepcopy(cotacao))


@app.get("/cotacoes/{id}")
def obter_cotacao(id: str):
    with _trava:
        c = _cotacoes.get(id)
        if c is None:
            raise ErroAPI(404, "COTACAO_NAO_ENCONTRADA", "Cotacao nao encontrada.")
        return copy.deepcopy(c)
