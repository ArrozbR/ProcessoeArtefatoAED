# arquivo: servicos/cotacao/main.py
import copy
import os
import re
import threading
from datetime import date, timedelta
from typing import Annotated

import httpx
from fastapi import FastAPI, Request
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse, Response
from pydantic import AfterValidator, BaseModel, Field, StrictInt, StrictStr
from starlette.exceptions import HTTPException as StarletteHTTPException

app = FastAPI()

TIMEOUT = 10.0
CEP_RE = re.compile(r"^[0-9]{8}$")
DATA_RE = re.compile(r"^[0-9]{4}-[0-9]{2}-[0-9]{2}$")

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

_lock = threading.Lock()
_cotacoes = {}
_contador = 0


class ErroAPI(Exception):
    def __init__(self, status, codigo, mensagem):
        super().__init__(mensagem)
        self.status = status
        self.codigo = codigo
        self.mensagem = mensagem


def _erro(status, codigo, mensagem):
    return JSONResponse(status_code=status, content={"erro": codigo, "mensagem": mensagem})


@app.exception_handler(ErroAPI)
async def _tratar_erro_api(request: Request, exc: ErroAPI):
    return _erro(exc.status, exc.codigo, exc.mensagem)


@app.exception_handler(RequestValidationError)
async def _tratar_validacao(request: Request, exc: RequestValidationError):
    return _erro(422, "DADOS_INVALIDOS", "Dados inválidos na requisição.")


@app.exception_handler(StarletteHTTPException)
async def _tratar_http(request: Request, exc: StarletteHTTPException):
    if exc.status_code == 404:
        return _erro(404, "NAO_ENCONTRADO", "Recurso não encontrado.")
    if exc.status_code == 405:
        return _erro(405, "METODO_NAO_PERMITIDO", "Método não permitido.")
    return _erro(exc.status_code, "ERRO", str(exc.detail))


def _validar_cep(v):
    if not CEP_RE.match(v):
        raise ValueError("CEP inválido")
    return v


def _validar_data(v):
    if not DATA_RE.match(v):
        raise ValueError("data inválida")
    date.fromisoformat(v)
    return v


Cep = Annotated[StrictStr, AfterValidator(_validar_cep)]
Data = Annotated[StrictStr, AfterValidator(_validar_data)]
Medida = Annotated[StrictInt, Field(ge=1, le=200)]


class Pacote(BaseModel):
    peso_g: Annotated[StrictInt, Field(ge=1, le=100000)]
    comprimento_cm: Medida
    largura_cm: Medida
    altura_cm: Medida


class NovaCotacao(BaseModel):
    cep_origem: Cep
    cep_destino: Cep
    pacote: Pacote
    data: Data


def _uf_do_cep(cep):
    n = int(cep)
    for uf, ini, fim in FAIXAS_CEP:
        if ini <= n <= fim:
            return uf
    return None


def _div_cima(a, b):
    return -(-a // b)


def _somar_dias_uteis(d, n):
    atual = d
    restantes = n
    while restantes > 0:
        atual = atual + timedelta(days=1)
        if atual.weekday() < 5:
            restantes -= 1
    return atual


def _url(nome, padrao):
    return os.environ.get(nome, padrao).rstrip("/")


def _buscar_transportadoras_ativas():
    url = _url("URL_TRANSPORTADORAS", "http://localhost:8001") + "/transportadoras"
    try:
        resp = httpx.get(url, params={"somente_ativas": "true"}, timeout=TIMEOUT)
    except httpx.HTTPError:
        raise ErroAPI(503, "SERVICO_INDISPONIVEL", "Serviço de transportadoras indisponível.")
    if resp.status_code != 200:
        raise ErroAPI(503, "SERVICO_INDISPONIVEL", "Serviço de transportadoras indisponível.")
    try:
        dados = resp.json()
    except ValueError:
        raise ErroAPI(503, "SERVICO_INDISPONIVEL", "Resposta inválida do serviço de transportadoras.")
    if not isinstance(dados, list):
        raise ErroAPI(503, "SERVICO_INDISPONIVEL", "Resposta inválida do serviço de transportadoras.")
    return dados


@app.post("/admin/reset", status_code=204)
def reset():
    global _contador
    with _lock:
        _cotacoes.clear()
        _contador = 0
    return Response(status_code=204)


@app.post("/cotacoes", status_code=201)
def criar_cotacao(corpo: NovaCotacao):
    global _contador
    uf_origem = _uf_do_cep(corpo.cep_origem)
    uf_destino = _uf_do_cep(corpo.cep_destino)
    if uf_origem is None or uf_destino is None:
        raise ErroAPI(422, "CEP_FORA_DA_AREA", "CEP fora da área atendida.")

    p = corpo.pacote
    peso_cubado = _div_cima(p.comprimento_cm * p.largura_cm * p.altura_cm, 6)
    peso_taxado = max(p.peso_g, peso_cubado)
    quilos = _div_cima(peso_taxado, 1000)
    interestadual = uf_origem != uf_destino
    data_cot = date.fromisoformat(corpo.data)

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
                preco = _div_cima(preco * (100 + t["adicional_interestadual_pct"]), 100)
                prazo = prazo + t["dias_adicionais_interestadual"]
            opcoes.append({
                "transportadora_id": t["id"],
                "nome": t["nome"],
                "preco_centavos": preco,
                "prazo_dias_uteis": prazo,
                "data_prevista_entrega": _somar_dias_uteis(data_cot, prazo).isoformat(),
            })
        except (KeyError, TypeError):
            raise ErroAPI(503, "SERVICO_INDISPONIVEL", "Resposta inválida do serviço de transportadoras.")

    opcoes.sort(key=lambda o: (o["preco_centavos"], o["prazo_dias_uteis"], o["transportadora_id"]))

    with _lock:
        _contador += 1
        cot_id = "COT-%06d" % _contador
        cotacao = {
            "id": cot_id,
            "cep_origem": corpo.cep_origem,
            "cep_destino": corpo.cep_destino,
            "uf_origem": uf_origem,
            "uf_destino": uf_destino,
            "peso_taxado_g": peso_taxado,
            "data": corpo.data,
            "valida_ate": (data_cot + timedelta(days=7)).isoformat(),
            "opcoes": opcoes,
        }
        _cotacoes[cot_id] = cotacao
        return JSONResponse(status_code=201, content=copy.deepcopy(cotacao))


@app.get("/cotacoes/{id}")
def obter_cotacao(id: str):
    with _lock:
        c = _cotacoes.get(id)
        if c is None:
            raise ErroAPI(404, "COTACAO_NAO_ENCONTRADA", "Cotação não encontrada.")
        return copy.deepcopy(c)
