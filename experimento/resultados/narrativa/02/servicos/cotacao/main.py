# arquivo: servicos/cotacao/main.py
import copy
import os
import re
import threading
from datetime import date, timedelta

import httpx
from fastapi import FastAPI
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse, Response
from pydantic import BaseModel, Field, StrictInt, StrictStr, field_validator
from starlette.exceptions import HTTPException as StarletteHTTPException


class ErroAPI(Exception):
    def __init__(self, status: int, codigo: str, mensagem: str):
        super().__init__(mensagem)
        self.status = status
        self.codigo = codigo
        self.mensagem = mensagem


def _resposta_erro(status: int, codigo: str, mensagem: str) -> JSONResponse:
    return JSONResponse(status_code=status, content={"erro": codigo, "mensagem": mensagem})


app = FastAPI()


@app.exception_handler(ErroAPI)
async def _tratar_erro_api(request, exc: ErroAPI):
    return _resposta_erro(exc.status, exc.codigo, exc.mensagem)


@app.exception_handler(RequestValidationError)
async def _tratar_validacao(request, exc: RequestValidationError):
    return _resposta_erro(422, "DADOS_INVALIDOS", "Dados inválidos na requisição.")


@app.exception_handler(StarletteHTTPException)
async def _tratar_http(request, exc: StarletteHTTPException):
    codigos = {404: "NAO_ENCONTRADO", 405: "METODO_NAO_PERMITIDO"}
    return _resposta_erro(exc.status_code, codigos.get(exc.status_code, "ERRO"), str(exc.detail))


TIMEOUT = 10.0

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
_estado = {"contador": 0, "cotacoes": {}}


def _validar_data_texto(v: str) -> str:
    if not re.fullmatch(r"[0-9]{4}-[0-9]{2}-[0-9]{2}", v):
        raise ValueError("data mal formada")
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
    def _validar_cep(cls, v: str) -> str:
        if not re.fullmatch(r"[0-9]{8}", v):
            raise ValueError("CEP inválido")
        return v

    @field_validator("data")
    @classmethod
    def _validar_data(cls, v: str) -> str:
        return _validar_data_texto(v)


def _uf_do_cep(cep: str):
    numero = int(cep)
    for uf, inicio, fim in FAIXAS_CEP:
        if inicio <= numero <= fim:
            return uf
    return None


def _div_cima(a: int, b: int) -> int:
    return -((-a) // b)


def _somar_dias_uteis(inicio: date, dias: int) -> date:
    atual = inicio
    restantes = dias
    while restantes > 0:
        atual = atual + timedelta(days=1)
        if atual.weekday() < 5:
            restantes -= 1
    return atual


def _buscar_transportadoras_ativas() -> list:
    base = os.environ.get("URL_TRANSPORTADORAS", "")
    try:
        resp = httpx.get(
            f"{base}/transportadoras",
            params={"somente_ativas": "true"},
            timeout=TIMEOUT,
        )
    except Exception:
        raise ErroAPI(503, "SERVICO_INDISPONIVEL", "Serviço de transportadoras indisponível.")
    if resp.status_code != 200:
        raise ErroAPI(503, "SERVICO_INDISPONIVEL", "Serviço de transportadoras indisponível.")
    try:
        dados = resp.json()
    except Exception:
        raise ErroAPI(503, "SERVICO_INDISPONIVEL", "Resposta inválida do serviço de transportadoras.")
    if not isinstance(dados, list):
        raise ErroAPI(503, "SERVICO_INDISPONIVEL", "Resposta inválida do serviço de transportadoras.")
    return dados


@app.post("/admin/reset")
def reset():
    with _trava:
        _estado["contador"] = 0
        _estado["cotacoes"] = {}
    return Response(status_code=204)


@app.post("/cotacoes")
def criar_cotacao(dados: CotacaoEntrada):
    uf_origem = _uf_do_cep(dados.cep_origem)
    uf_destino = _uf_do_cep(dados.cep_destino)
    if uf_origem is None or uf_destino is None:
        raise ErroAPI(422, "CEP_FORA_DA_AREA", "CEP fora da área atendida.")

    pacote = dados.pacote
    peso_cubado = _div_cima(pacote.comprimento_cm * pacote.largura_cm * pacote.altura_cm, 6)
    peso_taxado = max(pacote.peso_g, peso_cubado)
    quilos = _div_cima(peso_taxado, 1000)
    interestadual = uf_origem != uf_destino
    data_cotacao = date.fromisoformat(dados.data)

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
        if int(t.get("peso_maximo_g", 0)) < peso_taxado:
            continue
        preco = int(t.get("tarifa_base_centavos", 0)) + int(t.get("tarifa_kg_centavos", 0)) * quilos
        prazo = int(t.get("prazo_dias_uteis", 1))
        if interestadual:
            adicional = int(t.get("adicional_interestadual_pct", 0))
            preco = _div_cima(preco * (100 + adicional), 100)
            prazo = prazo + int(t.get("dias_adicionais_interestadual", 0))
        opcoes.append({
            "transportadora_id": t.get("id"),
            "nome": t.get("nome"),
            "preco_centavos": preco,
            "prazo_dias_uteis": prazo,
            "data_prevista_entrega": _somar_dias_uteis(data_cotacao, prazo).isoformat(),
        })
    opcoes.sort(key=lambda o: (o["preco_centavos"], o["prazo_dias_uteis"], str(o["transportadora_id"])))

    with _trava:
        numero = _estado["contador"] + 1
        cotacao = {
            "id": f"COT-{numero:06d}",
            "cep_origem": dados.cep_origem,
            "cep_destino": dados.cep_destino,
            "uf_origem": uf_origem,
            "uf_destino": uf_destino,
            "peso_taxado_g": peso_taxado,
            "data": dados.data,
            "valida_ate": (data_cotacao + timedelta(days=7)).isoformat(),
            "opcoes": opcoes,
        }
        _estado["contador"] = numero
        _estado["cotacoes"][cotacao["id"]] = cotacao
        return JSONResponse(status_code=201, content=copy.deepcopy(cotacao))


@app.get("/cotacoes/{id}")
def obter_cotacao(id: str):
    with _trava:
        cotacao = _estado["cotacoes"].get(id)
        if cotacao is None:
            raise ErroAPI(404, "COTACAO_NAO_ENCONTRADA", "Cotação não encontrada.")
        return JSONResponse(status_code=200, content=copy.deepcopy(cotacao))
