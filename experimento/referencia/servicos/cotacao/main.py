import os
import re
from datetime import date, timedelta

import httpx
from fastapi import FastAPI, Request, Response
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse
from pydantic import BaseModel, Field, field_validator

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
VALIDADE_DIAS = 7

app = FastAPI(title="Cotação")
_cotacoes = {}
_contador = 0


class ErroApi(Exception):
    def __init__(self, status, erro, mensagem):
        self.status, self.erro, self.mensagem = status, erro, mensagem


@app.exception_handler(ErroApi)
def _erro_api(_: Request, e: ErroApi):
    return JSONResponse({"erro": e.erro, "mensagem": e.mensagem}, status_code=e.status)


@app.exception_handler(RequestValidationError)
def _erro_validacao(_: Request, e: RequestValidationError):
    return JSONResponse({"erro": "DADOS_INVALIDOS", "mensagem": "dados inválidos"}, status_code=422)


class Pacote(BaseModel):
    peso_g: int = Field(ge=1, le=100000)
    comprimento_cm: int = Field(ge=1, le=200)
    largura_cm: int = Field(ge=1, le=200)
    altura_cm: int = Field(ge=1, le=200)


class PedidoCotacao(BaseModel):
    cep_origem: str
    cep_destino: str
    pacote: Pacote
    data: date

    @field_validator("cep_origem", "cep_destino")
    @classmethod
    def _cep(cls, v):
        if not re.fullmatch(r"[0-9]{8}", v):
            raise ValueError("CEP deve ter 8 dígitos")
        return v


@app.post("/cotacoes", status_code=201)
def cotar(pedido: PedidoCotacao):
    global _contador
    uf_origem = _uf(pedido.cep_origem)
    uf_destino = _uf(pedido.cep_destino)
    if uf_origem is None or uf_destino is None:
        raise ErroApi(422, "CEP_FORA_DA_AREA", "CEP fora da área atendida")
    transportadoras = _transportadoras_ativas()

    p = pedido.pacote
    peso_cubado = -(-(p.comprimento_cm * p.largura_cm * p.altura_cm) // 6)
    peso_taxado = max(p.peso_g, peso_cubado)
    kg_cobrados = -(-peso_taxado // 1000)
    interestadual = uf_origem != uf_destino

    opcoes = []
    for t in transportadoras:
        if not t["ativa"] or uf_origem not in t["ufs_atendidas"] or uf_destino not in t["ufs_atendidas"]:
            continue
        if peso_taxado > t["peso_maximo_g"]:
            continue
        preco = t["tarifa_base_centavos"] + t["tarifa_kg_centavos"] * kg_cobrados
        prazo = t["prazo_dias_uteis"]
        if interestadual:
            preco = -(-(preco * (100 + t["adicional_interestadual_pct"])) // 100)
            prazo += t["dias_adicionais_interestadual"]
        opcoes.append({
            "transportadora_id": t["id"],
            "nome": t["nome"],
            "preco_centavos": preco,
            "prazo_dias_uteis": prazo,
            "data_prevista_entrega": _somar_dias_uteis(pedido.data, prazo).isoformat(),
        })
    opcoes.sort(key=lambda o: (o["preco_centavos"], o["prazo_dias_uteis"], o["transportadora_id"]))

    _contador += 1
    cotacao = {
        "id": f"COT-{_contador:06d}",
        "cep_origem": pedido.cep_origem,
        "cep_destino": pedido.cep_destino,
        "uf_origem": uf_origem,
        "uf_destino": uf_destino,
        "peso_taxado_g": peso_taxado,
        "data": pedido.data.isoformat(),
        "valida_ate": (pedido.data + timedelta(days=VALIDADE_DIAS)).isoformat(),
        "opcoes": opcoes,
    }
    _cotacoes[cotacao["id"]] = cotacao
    return cotacao


@app.get("/cotacoes/{id}")
def consultar(id: str):
    if id not in _cotacoes:
        raise ErroApi(404, "COTACAO_NAO_ENCONTRADA", "cotação não encontrada")
    return _cotacoes[id]


@app.post("/admin/reset", status_code=204)
def reset():
    global _contador
    _cotacoes.clear()
    _contador = 0
    return Response(status_code=204)


def _uf(cep):
    n = int(cep)
    for uf, inicio, fim in FAIXAS_CEP:
        if inicio <= n <= fim:
            return uf
    return None


def _somar_dias_uteis(inicio, dias):
    d = inicio
    while dias > 0:
        d += timedelta(days=1)
        if d.weekday() < 5:
            dias -= 1
    return d


def _transportadoras_ativas():
    try:
        r = httpx.get(f"{os.environ['URL_TRANSPORTADORAS']}/transportadoras",
                      params={"somente_ativas": "true"}, timeout=10)
    except httpx.HTTPError:
        raise ErroApi(503, "SERVICO_INDISPONIVEL", "serviço de transportadoras indisponível")
    if r.status_code >= 500:
        raise ErroApi(503, "SERVICO_INDISPONIVEL", "serviço de transportadoras indisponível")
    return r.json()
