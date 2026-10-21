# arquivo: servicos/cotacao/main.py
import os
import re
import threading
from datetime import date, timedelta

import httpx
from fastapi import FastAPI, Request
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse, Response
from pydantic import BaseModel, StrictInt, StrictStr, field_validator
from starlette.exceptions import HTTPException as StarletteHTTPException

app = FastAPI()

RE_CEP = re.compile(r"[0-9]{8}")
RE_DATA = re.compile(r"[0-9]{4}-[0-9]{2}-[0-9]{2}")

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
_contador = {"cotacao": 0}
_cliente = httpx.Client(timeout=10.0)


class ErroAPI(Exception):
    def __init__(self, status: int, codigo: str, mensagem: str):
        super().__init__(mensagem)
        self.status = status
        self.codigo = codigo
        self.mensagem = mensagem


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


def _validar_data(v: str) -> str:
    if not RE_DATA.fullmatch(v):
        raise ValueError("data mal formada")
    date.fromisoformat(v)
    return v


class Pacote(BaseModel):
    peso_g: StrictInt
    comprimento_cm: StrictInt
    largura_cm: StrictInt
    altura_cm: StrictInt

    @field_validator("peso_g")
    @classmethod
    def _v_peso(cls, v):
        if v < 1 or v > 100000:
            raise ValueError("peso fora da faixa")
        return v

    @field_validator("comprimento_cm", "largura_cm", "altura_cm")
    @classmethod
    def _v_medida(cls, v):
        if v < 1 or v > 200:
            raise ValueError("medida fora da faixa")
        return v


class CotacaoEntrada(BaseModel):
    cep_origem: StrictStr
    cep_destino: StrictStr
    pacote: Pacote
    data: StrictStr

    @field_validator("cep_origem", "cep_destino")
    @classmethod
    def _v_cep(cls, v):
        if not RE_CEP.fullmatch(v):
            raise ValueError("CEP inválido")
        return v

    @field_validator("data")
    @classmethod
    def _v_data(cls, v):
        return _validar_data(v)


def _uf_do_cep(cep: str):
    numero = int(cep)
    for uf, inicio, fim in FAIXAS_CEP:
        if inicio <= numero <= fim:
            return uf
    return None


def _somar_dias_uteis(inicio: date, dias: int) -> date:
    atual = inicio
    restantes = dias
    while restantes > 0:
        atual = atual + timedelta(days=1)
        if atual.weekday() < 5:
            restantes -= 1
    return atual


def _buscar_transportadoras_ativas():
    base = os.environ.get("URL_TRANSPORTADORAS", "")
    if not base:
        raise ErroAPI(503, "SERVICO_INDISPONIVEL", "Serviço de transportadoras indisponível.")
    try:
        resp = _cliente.get(base + "/transportadoras", params={"somente_ativas": "true"}, timeout=10.0)
    except httpx.HTTPError:
        raise ErroAPI(503, "SERVICO_INDISPONIVEL", "Serviço de transportadoras indisponível.")
    if resp.status_code >= 500 or resp.status_code != 200:
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
    with _lock:
        _cotacoes.clear()
        _contador["cotacao"] = 0
    return Response(status_code=204)


@app.post("/cotacoes", status_code=201)
def criar_cotacao(entrada: CotacaoEntrada):
    uf_origem = _uf_do_cep(entrada.cep_origem)
    uf_destino = _uf_do_cep(entrada.cep_destino)
    if uf_origem is None or uf_destino is None:
        raise ErroAPI(422, "CEP_FORA_DA_AREA", "CEP fora da área atendida.")

    pacote = entrada.pacote
    volume = pacote.comprimento_cm * pacote.largura_cm * pacote.altura_cm
    peso_cubado = (volume + 5) // 6
    peso_taxado = max(pacote.peso_g, peso_cubado)
    quilos = (peso_taxado + 999) // 1000
    interestadual = uf_origem != uf_destino
    data_cotacao = date.fromisoformat(entrada.data)

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
            preco = (preco * (100 + adicional) + 99) // 100
            prazo += int(t.get("dias_adicionais_interestadual", 0))
        opcoes.append({
            "transportadora_id": t.get("id"),
            "nome": t.get("nome"),
            "preco_centavos": preco,
            "prazo_dias_uteis": prazo,
            "data_prevista_entrega": _somar_dias_uteis(data_cotacao, prazo).isoformat(),
        })
    opcoes.sort(key=lambda o: (o["preco_centavos"], o["prazo_dias_uteis"], str(o["transportadora_id"])))

    with _lock:
        _contador["cotacao"] += 1
        cotacao_id = "COT-%06d" % _contador["cotacao"]
        cotacao = {
            "id": cotacao_id,
            "cep_origem": entrada.cep_origem,
            "cep_destino": entrada.cep_destino,
            "uf_origem": uf_origem,
            "uf_destino": uf_destino,
            "peso_taxado_g": peso_taxado,
            "data": entrada.data,
            "valida_ate": (data_cotacao + timedelta(days=7)).isoformat(),
            "opcoes": opcoes,
        }
        _cotacoes[cotacao_id] = cotacao
    return JSONResponse(status_code=201, content=cotacao)


@app.get("/cotacoes/{id}")
def obter_cotacao(id: str):
    with _lock:
        cotacao = _cotacoes.get(id)
        if cotacao is None:
            raise ErroAPI(404, "COTACAO_NAO_ENCONTRADA", "Cotação não encontrada.")
        return cotacao
