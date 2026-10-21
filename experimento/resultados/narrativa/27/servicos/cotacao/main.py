# arquivo: servicos/cotacao/main.py
import os
import re
import threading
from datetime import date, timedelta
from urllib.parse import quote

import httpx
from fastapi import FastAPI, Request
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse, Response
from pydantic import BaseModel, StrictInt, StrictStr, field_validator
from starlette.exceptions import HTTPException as StarletteHTTPException

app = FastAPI()

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

_lock = threading.RLock()
_cotacoes = {}
_contador = {"valor": 0}


class ErroApi(Exception):
    def __init__(self, status, codigo, mensagem):
        self.status = status
        self.codigo = codigo
        self.mensagem = mensagem


def _erro(status, codigo, mensagem):
    return JSONResponse(status_code=status, content={"erro": codigo, "mensagem": mensagem})


@app.exception_handler(ErroApi)
async def _tratar_erro_api(request: Request, exc: ErroApi):
    return _erro(exc.status, exc.codigo, exc.mensagem)


@app.exception_handler(RequestValidationError)
async def _tratar_validacao(request: Request, exc: RequestValidationError):
    return _erro(422, "DADOS_INVALIDOS", "Dados inválidos.")


@app.exception_handler(StarletteHTTPException)
async def _tratar_http(request: Request, exc: StarletteHTTPException):
    if exc.status_code == 404:
        return _erro(404, "NAO_ENCONTRADO", "Recurso não encontrado.")
    if exc.status_code == 405:
        return _erro(405, "METODO_NAO_PERMITIDO", "Método não permitido.")
    return _erro(exc.status_code, "ERRO", str(exc.detail))


def _validar_data(v):
    if not re.fullmatch(r"[0-9]{4}-[0-9]{2}-[0-9]{2}", v):
        raise ValueError("data inválida")
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
        if not re.fullmatch(r"[0-9]{8}", v):
            raise ValueError("CEP inválido")
        return v

    @field_validator("data")
    @classmethod
    def _v_data(cls, v):
        return _validar_data(v)


def _uf_do_cep(cep):
    numero = int(cep)
    for uf, inicio, fim in FAIXAS_CEP:
        if inicio <= numero <= fim:
            return uf
    return None


def _somar_dias_uteis(inicio, dias):
    atual = inicio
    restantes = dias
    while restantes > 0:
        atual = atual + timedelta(days=1)
        if atual.weekday() < 5:
            restantes -= 1
    return atual


def _buscar_transportadoras_ativas():
    base = os.environ.get("URL_TRANSPORTADORAS", "")
    try:
        resposta = httpx.get(
            f"{base}/transportadoras", params={"somente_ativas": "true"}, timeout=TIMEOUT
        )
    except Exception:
        raise ErroApi(503, "SERVICO_INDISPONIVEL", "Serviço de transportadoras indisponível.")
    if resposta.status_code != 200:
        raise ErroApi(503, "SERVICO_INDISPONIVEL", "Serviço de transportadoras indisponível.")
    try:
        dados = resposta.json()
    except Exception:
        raise ErroApi(503, "SERVICO_INDISPONIVEL", "Serviço de transportadoras indisponível.")
    if not isinstance(dados, list):
        raise ErroApi(503, "SERVICO_INDISPONIVEL", "Serviço de transportadoras indisponível.")
    return dados


@app.post("/admin/reset")
def reset():
    with _lock:
        _cotacoes.clear()
        _contador["valor"] = 0
    return Response(status_code=204)


@app.post("/cotacoes", status_code=201)
def criar_cotacao(entrada: CotacaoEntrada):
    uf_origem = _uf_do_cep(entrada.cep_origem)
    uf_destino = _uf_do_cep(entrada.cep_destino)
    if uf_origem is None or uf_destino is None:
        raise ErroApi(422, "CEP_FORA_DA_AREA", "CEP fora da área atendida.")

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
            preco = (preco * (100 + t["adicional_interestadual_pct"]) + 99) // 100
            prazo = prazo + t["dias_adicionais_interestadual"]
        entrega = _somar_dias_uteis(data_cotacao, prazo)
        opcoes.append(
            {
                "transportadora_id": t["id"],
                "nome": t["nome"],
                "preco_centavos": preco,
                "prazo_dias_uteis": prazo,
                "data_prevista_entrega": entrega.isoformat(),
            }
        )
    opcoes.sort(key=lambda o: (o["preco_centavos"], o["prazo_dias_uteis"], o["transportadora_id"]))

    with _lock:
        _contador["valor"] += 1
        cotacao_id = f"COT-{_contador['valor']:06d}"
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
    return cotacao


@app.get("/cotacoes/{cotacao_id}")
def obter_cotacao(cotacao_id: str):
    with _lock:
        cotacao = _cotacoes.get(cotacao_id)
    if cotacao is None:
        raise ErroApi(404, "COTACAO_NAO_ENCONTRADA", "Cotação não encontrada.")
    return cotacao
