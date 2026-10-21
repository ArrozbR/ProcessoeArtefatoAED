# arquivo: servicos/cotacao/main.py
import copy
import os
import re
import threading
from datetime import date, datetime, timedelta
from typing import Annotated

import httpx
from fastapi import FastAPI, Request
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse, Response
from pydantic import AfterValidator, BaseModel, Field, StrictInt, StrictStr
from starlette.exceptions import HTTPException as StarletteHTTPException


class ErroAPI(Exception):
    def __init__(self, status: int, codigo: str, mensagem: str):
        super().__init__(mensagem)
        self.status = status
        self.codigo = codigo
        self.mensagem = mensagem


app = FastAPI()


@app.exception_handler(ErroAPI)
async def _tratar_erro_api(request: Request, exc: ErroAPI):
    return JSONResponse(status_code=exc.status, content={"erro": exc.codigo, "mensagem": exc.mensagem})


@app.exception_handler(RequestValidationError)
async def _tratar_validacao(request: Request, exc: RequestValidationError):
    return JSONResponse(status_code=422, content={"erro": "DADOS_INVALIDOS", "mensagem": "Dados inválidos."})


@app.exception_handler(StarletteHTTPException)
async def _tratar_http(request: Request, exc: StarletteHTTPException):
    codigo = "NAO_ENCONTRADO" if exc.status_code == 404 else "ERRO_HTTP"
    return JSONResponse(status_code=exc.status_code, content={"erro": codigo, "mensagem": str(exc.detail)})


_RE_CEP = re.compile(r"[0-9]{8}")
_RE_DATA = re.compile(r"[0-9]{4}-[0-9]{2}-[0-9]{2}")


def _validar_cep(v: str) -> str:
    if not _RE_CEP.fullmatch(v):
        raise ValueError("CEP inválido")
    return v


def _validar_data(v: str) -> str:
    if not _RE_DATA.fullmatch(v):
        raise ValueError("data inválida")
    datetime.strptime(v, "%Y-%m-%d")
    return v


Medida = Annotated[StrictInt, Field(ge=1, le=200)]


class Pacote(BaseModel):
    peso_g: Annotated[StrictInt, Field(ge=1, le=100000)]
    comprimento_cm: Medida
    largura_cm: Medida
    altura_cm: Medida


class CotacaoEntrada(BaseModel):
    cep_origem: Annotated[StrictStr, AfterValidator(_validar_cep)]
    cep_destino: Annotated[StrictStr, AfterValidator(_validar_cep)]
    pacote: Pacote
    data: Annotated[StrictStr, AfterValidator(_validar_data)]


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


def _somar_dias_uteis(inicio: date, dias: int) -> date:
    d = inicio
    restantes = dias
    while restantes > 0:
        d = d + timedelta(days=1)
        if d.weekday() < 5:
            restantes -= 1
    return d


def _ceil_div(a: int, b: int) -> int:
    return -(-a // b)


_cliente = httpx.Client(timeout=10.0)
_lock = threading.Lock()
_cotacoes = {}
_contador = {"n": 0}


def _indisponivel():
    return ErroAPI(503, "SERVICO_INDISPONIVEL", "Serviço de transportadoras indisponível.")


def _buscar_transportadoras_ativas():
    url = os.environ.get("URL_TRANSPORTADORAS", "") + "/transportadoras"
    try:
        resp = _cliente.get(url, params={"somente_ativas": "true"})
    except Exception:
        raise _indisponivel()
    if resp.status_code != 200:
        raise _indisponivel()
    try:
        dados = resp.json()
    except Exception:
        raise _indisponivel()
    if not isinstance(dados, list):
        raise _indisponivel()
    return dados


@app.post("/admin/reset", status_code=204)
def reset():
    with _lock:
        _cotacoes.clear()
        _contador["n"] = 0
    return Response(status_code=204)


@app.post("/cotacoes", status_code=201)
def criar_cotacao(entrada: CotacaoEntrada):
    uf_origem = _uf_do_cep(entrada.cep_origem)
    uf_destino = _uf_do_cep(entrada.cep_destino)
    if uf_origem is None or uf_destino is None:
        raise ErroAPI(422, "CEP_FORA_DA_AREA", "CEP fora da área atendida.")

    p = entrada.pacote
    peso_cubado = _ceil_div(p.comprimento_cm * p.largura_cm * p.altura_cm, 6)
    peso_taxado = max(p.peso_g, peso_cubado)
    quilos = _ceil_div(peso_taxado, 1000)
    interestadual = uf_origem != uf_destino
    data_cotacao = datetime.strptime(entrada.data, "%Y-%m-%d").date()

    transportadoras = _buscar_transportadoras_ativas()

    opcoes = []
    for t in transportadoras:
        try:
            if not t.get("ativa", True):
                continue
            ufs = t["ufs_atendidas"]
            if uf_origem not in ufs or uf_destino not in ufs:
                continue
            if t["peso_maximo_g"] < peso_taxado:
                continue
            preco = t["tarifa_base_centavos"] + t["tarifa_kg_centavos"] * quilos
            prazo = t["prazo_dias_uteis"]
            if interestadual:
                preco = _ceil_div(preco * (100 + t["adicional_interestadual_pct"]), 100)
                prazo = prazo + t["dias_adicionais_interestadual"]
            entrega = _somar_dias_uteis(data_cotacao, prazo)
            opcoes.append({
                "transportadora_id": t["id"],
                "nome": t["nome"],
                "preco_centavos": preco,
                "prazo_dias_uteis": prazo,
                "data_prevista_entrega": entrega.isoformat(),
            })
        except (KeyError, TypeError):
            raise _indisponivel()

    opcoes.sort(key=lambda o: (o["preco_centavos"], o["prazo_dias_uteis"], o["transportadora_id"]))

    with _lock:
        _contador["n"] += 1
        cid = "COT-%06d" % _contador["n"]
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
        return JSONResponse(status_code=201, content=copy.deepcopy(cotacao))


@app.get("/cotacoes/{id}")
def obter_cotacao(id: str):
    with _lock:
        c = _cotacoes.get(id)
        if c is None:
            raise ErroAPI(404, "COTACAO_NAO_ENCONTRADA", "Cotação não encontrada.")
        return copy.deepcopy(c)
