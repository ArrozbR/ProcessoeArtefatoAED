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
from pydantic import BaseModel, Field, StrictInt, StrictStr, field_validator
from starlette.exceptions import HTTPException as StarletteHTTPException

app = FastAPI(title="Cotação")

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
_contador = {"valor": 0}
_cliente = httpx.Client(timeout=10.0)


class ServicoIndisponivel(Exception):
    pass


def erro(status, codigo, mensagem):
    return JSONResponse(status_code=status, content={"erro": codigo, "mensagem": mensagem})


def _descrever_erros(exc):
    partes = []
    try:
        for e in exc.errors():
            loc = ".".join(str(p) for p in e.get("loc", ()))
            partes.append(f"{loc}: {e.get('msg', '')}")
    except Exception:
        pass
    return "Dados inválidos" + (": " + "; ".join(partes) if partes else ".")


@app.exception_handler(RequestValidationError)
async def _validacao(request: Request, exc: RequestValidationError):
    return erro(422, "DADOS_INVALIDOS", _descrever_erros(exc))


@app.exception_handler(StarletteHTTPException)
async def _http(request: Request, exc: StarletteHTTPException):
    codigo = "RECURSO_NAO_ENCONTRADO" if exc.status_code == 404 else "ERRO_HTTP"
    return erro(exc.status_code, codigo, str(exc.detail))


def _validar_data(v):
    if not re.fullmatch(r"[0-9]{4}-[0-9]{2}-[0-9]{2}", v):
        raise ValueError("data deve estar no formato AAAA-MM-DD")
    try:
        date.fromisoformat(v)
    except ValueError:
        raise ValueError("data inexistente")
    return v


class Pacote(BaseModel):
    peso_g: Annotated[StrictInt, Field(ge=1, le=100000)]
    comprimento_cm: Annotated[StrictInt, Field(ge=1, le=200)]
    largura_cm: Annotated[StrictInt, Field(ge=1, le=200)]
    altura_cm: Annotated[StrictInt, Field(ge=1, le=200)]


class CotacaoEntrada(BaseModel):
    cep_origem: StrictStr
    cep_destino: StrictStr
    pacote: Pacote
    data: StrictStr

    @field_validator("cep_origem", "cep_destino")
    @classmethod
    def _validar_cep(cls, v):
        if not re.fullmatch(r"[0-9]{8}", v):
            raise ValueError("CEP deve ter exatamente 8 dígitos")
        return v

    @field_validator("data")
    @classmethod
    def _validar_data_campo(cls, v):
        return _validar_data(v)


def uf_do_cep(cep):
    n = int(cep)
    for uf, ini, fim in FAIXAS_CEP:
        if ini <= n <= fim:
            return uf
    return None


def somar_dias_uteis(inicio, dias):
    d = inicio
    restantes = dias
    while restantes > 0:
        d = d + timedelta(days=1)
        if d.weekday() < 5:
            restantes -= 1
    return d


def buscar_transportadoras_ativas():
    base = os.environ.get("URL_TRANSPORTADORAS", "")
    try:
        resp = _cliente.get(f"{base}/transportadoras", params={"somente_ativas": "true"}, timeout=10.0)
    except Exception:
        raise ServicoIndisponivel()
    if resp.status_code != 200:
        raise ServicoIndisponivel()
    try:
        dados = resp.json()
    except Exception:
        raise ServicoIndisponivel()
    if not isinstance(dados, list):
        raise ServicoIndisponivel()
    return dados


@app.post("/admin/reset")
def reset():
    with _lock:
        _cotacoes.clear()
        _contador["valor"] = 0
    return Response(status_code=204)


@app.post("/cotacoes")
def criar_cotacao(entrada: CotacaoEntrada):
    uf_origem = uf_do_cep(entrada.cep_origem)
    uf_destino = uf_do_cep(entrada.cep_destino)
    if uf_origem is None or uf_destino is None:
        return erro(422, "CEP_FORA_DA_AREA", "CEP fora da área atendida.")

    try:
        transportadoras = buscar_transportadoras_ativas()
    except ServicoIndisponivel:
        return erro(503, "SERVICO_INDISPONIVEL", "Serviço de transportadoras indisponível.")

    p = entrada.pacote
    volume = p.comprimento_cm * p.largura_cm * p.altura_cm
    peso_cubado = (volume + 5) // 6
    peso_taxado = max(p.peso_g, peso_cubado)
    quilos = (peso_taxado + 999) // 1000
    interestadual = uf_origem != uf_destino
    data_cotacao = date.fromisoformat(entrada.data)

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
                preco = (preco * (100 + t["adicional_interestadual_pct"]) + 99) // 100
                prazo = prazo + t["dias_adicionais_interestadual"]
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

    with _lock:
        _contador["valor"] += 1
        cotacao = {
            "id": f"COT-{_contador['valor']:06d}",
            "cep_origem": entrada.cep_origem,
            "cep_destino": entrada.cep_destino,
            "uf_origem": uf_origem,
            "uf_destino": uf_destino,
            "peso_taxado_g": peso_taxado,
            "data": entrada.data,
            "valida_ate": (data_cotacao + timedelta(days=7)).isoformat(),
            "opcoes": opcoes,
        }
        _cotacoes[cotacao["id"]] = cotacao
        return JSONResponse(status_code=201, content=copy.deepcopy(cotacao))


@app.get("/cotacoes/{cotacao_id}")
def obter_cotacao(cotacao_id: str):
    with _lock:
        c = _cotacoes.get(cotacao_id)
        if c is None:
            return erro(404, "COTACAO_NAO_ENCONTRADA", f"Cotação {cotacao_id} não encontrada.")
        return JSONResponse(status_code=200, content=copy.deepcopy(c))
