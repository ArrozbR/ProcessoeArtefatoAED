# arquivo: servicos/cotacao/main.py
import os
import re
from datetime import date, timedelta

import httpx
from fastapi import FastAPI, Request
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse, Response
from pydantic import BaseModel, Field, StrictInt, StrictStr, field_validator
from starlette.exceptions import HTTPException as StarletteHTTPException

app = FastAPI()

CEP_REGEX = re.compile(r"[0-9]{8}")
DATA_REGEX = re.compile(r"[0-9]{4}-[0-9]{2}-[0-9]{2}")

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

_cliente = httpx.Client(timeout=10.0)


class ErroApi(Exception):
    def __init__(self, status: int, codigo: str, mensagem: str):
        self.status = status
        self.codigo = codigo
        self.mensagem = mensagem


def resposta_erro(status: int, codigo: str, mensagem: str) -> JSONResponse:
    return JSONResponse(status_code=status, content={"erro": codigo, "mensagem": mensagem})


@app.exception_handler(ErroApi)
async def tratar_erro_api(request: Request, exc: ErroApi):
    return resposta_erro(exc.status, exc.codigo, exc.mensagem)


@app.exception_handler(RequestValidationError)
async def tratar_validacao(request: Request, exc: RequestValidationError):
    return resposta_erro(422, "DADOS_INVALIDOS", "Dados invalidos na requisicao.")


@app.exception_handler(StarletteHTTPException)
async def tratar_http(request: Request, exc: StarletteHTTPException):
    if exc.status_code == 404:
        return resposta_erro(404, "NAO_ENCONTRADO", "Recurso nao encontrado.")
    if exc.status_code == 405:
        return resposta_erro(405, "METODO_NAO_PERMITIDO", "Metodo nao permitido.")
    return resposta_erro(exc.status_code, "ERRO", str(exc.detail))


def validar_data_texto(v: str) -> str:
    if not DATA_REGEX.fullmatch(v):
        raise ValueError("data invalida")
    date.fromisoformat(v)
    return v


class Pacote(BaseModel):
    peso_g: StrictInt = Field(ge=1, le=100000)
    comprimento_cm: StrictInt = Field(ge=1, le=200)
    largura_cm: StrictInt = Field(ge=1, le=200)
    altura_cm: StrictInt = Field(ge=1, le=200)


class CotacaoEntrada(BaseModel):
    cep_origem: StrictStr
    cep_destino: StrictStr
    pacote: Pacote
    data: StrictStr

    @field_validator("cep_origem", "cep_destino")
    @classmethod
    def validar_cep(cls, v):
        if not CEP_REGEX.fullmatch(v):
            raise ValueError("cep invalido")
        return v

    @field_validator("data")
    @classmethod
    def validar_data(cls, v):
        return validar_data_texto(v)


cotacoes = {}
estado = {"contador": 0}


def uf_do_cep(cep: str):
    numero = int(cep)
    for uf, inicio, fim in FAIXAS_CEP:
        if inicio <= numero <= fim:
            return uf
    return None


def somar_dias_uteis(inicio: date, dias: int) -> date:
    atual = inicio
    restantes = dias
    while restantes > 0:
        atual = atual + timedelta(days=1)
        if atual.weekday() < 5:
            restantes -= 1
    return atual


def teto_div(a: int, b: int) -> int:
    return -((-a) // b)


def buscar_transportadoras_ativas():
    base = os.environ.get("URL_TRANSPORTADORAS", "")
    try:
        resp = _cliente.get(base + "/transportadoras", params={"somente_ativas": "true"})
    except Exception:
        raise ErroApi(503, "SERVICO_INDISPONIVEL", "Servico de transportadoras indisponivel.")
    if resp.status_code != 200:
        raise ErroApi(503, "SERVICO_INDISPONIVEL", "Servico de transportadoras indisponivel.")
    try:
        dados = resp.json()
    except Exception:
        raise ErroApi(503, "SERVICO_INDISPONIVEL", "Servico de transportadoras indisponivel.")
    if not isinstance(dados, list):
        raise ErroApi(503, "SERVICO_INDISPONIVEL", "Servico de transportadoras indisponivel.")
    return dados


@app.post("/admin/reset", status_code=204)
def reset():
    cotacoes.clear()
    estado["contador"] = 0
    return Response(status_code=204)


@app.post("/cotacoes", status_code=201)
def criar_cotacao(dados: CotacaoEntrada):
    uf_origem = uf_do_cep(dados.cep_origem)
    uf_destino = uf_do_cep(dados.cep_destino)
    if uf_origem is None or uf_destino is None:
        raise ErroApi(422, "CEP_FORA_DA_AREA", "CEP fora da area atendida.")

    transportadoras = buscar_transportadoras_ativas()

    p = dados.pacote
    peso_cubado = teto_div(p.comprimento_cm * p.largura_cm * p.altura_cm, 6)
    peso_taxado = max(p.peso_g, peso_cubado)
    quilos = teto_div(peso_taxado, 1000)
    interestadual = uf_origem != uf_destino
    data_cotacao = date.fromisoformat(dados.data)

    opcoes = []
    for t in transportadoras:
        try:
            if not t.get("ativa", True):
                continue
            ufs = t.get("ufs_atendidas") or []
            if uf_origem not in ufs or uf_destino not in ufs:
                continue
            if t["peso_maximo_g"] < peso_taxado:
                continue
            preco = t["tarifa_base_centavos"] + t["tarifa_kg_centavos"] * quilos
            prazo = t["prazo_dias_uteis"]
            if interestadual:
                preco = teto_div(preco * (100 + t["adicional_interestadual_pct"]), 100)
                prazo = prazo + t["dias_adicionais_interestadual"]
            preco = int(preco)
            prazo = int(prazo)
            opcoes.append({
                "transportadora_id": t["id"],
                "nome": t["nome"],
                "preco_centavos": preco,
                "prazo_dias_uteis": prazo,
                "data_prevista_entrega": somar_dias_uteis(data_cotacao, prazo).isoformat(),
            })
        except (KeyError, TypeError):
            continue

    opcoes.sort(key=lambda o: (o["preco_centavos"], o["prazo_dias_uteis"], o["transportadora_id"]))

    numero = estado["contador"] + 1
    cotacao = {
        "id": "COT-%06d" % numero,
        "cep_origem": dados.cep_origem,
        "cep_destino": dados.cep_destino,
        "uf_origem": uf_origem,
        "uf_destino": uf_destino,
        "peso_taxado_g": peso_taxado,
        "data": dados.data,
        "valida_ate": (data_cotacao + timedelta(days=7)).isoformat(),
        "opcoes": opcoes,
    }
    estado["contador"] = numero
    cotacoes[cotacao["id"]] = cotacao
    return JSONResponse(status_code=201, content=cotacao)


@app.get("/cotacoes/{id}")
def obter_cotacao(id: str):
    if id not in cotacoes:
        raise ErroApi(404, "COTACAO_NAO_ENCONTRADA", "Cotacao nao encontrada.")
    return cotacoes[id]
