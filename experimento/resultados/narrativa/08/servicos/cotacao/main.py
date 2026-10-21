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
from pydantic import BaseModel, StrictInt, StrictStr, field_validator
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
    return JSONResponse(status_code=422, content={"erro": "DADOS_INVALIDOS", "mensagem": "Dados inválidos."})


@app.exception_handler(StarletteHTTPException)
async def _tratar_http(request, exc: StarletteHTTPException):
    codigos = {404: "NAO_ENCONTRADO", 405: "METODO_NAO_PERMITIDO"}
    return JSONResponse(
        status_code=exc.status_code,
        content={"erro": codigos.get(exc.status_code, "ERRO"), "mensagem": str(exc.detail)},
    )


# ---------------------------------------------------------------- HTTP
_cliente = None
_cliente_lock = threading.Lock()


def _http():
    global _cliente
    with _cliente_lock:
        if _cliente is None:
            _cliente = httpx.Client(timeout=10.0)
        return _cliente


def _indisponivel():
    return ErroAPI(503, "SERVICO_INDISPONIVEL", "Serviço dependente indisponível.")


def _chamar(metodo, url, **kwargs):
    try:
        r = _http().request(metodo, url, **kwargs)
    except httpx.HTTPError:
        raise _indisponivel()
    if r.status_code >= 500:
        raise _indisponivel()
    return r


# ---------------------------------------------------------------- domínio
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


def _uf_do_cep(cep: str):
    n = int(cep)
    for uf, ini, fim in FAIXAS_CEP:
        if ini <= n <= fim:
            return uf
    return None


def _ceil_div(a: int, b: int) -> int:
    return -(-a // b)


def _somar_dias_uteis(inicio: date, dias: int) -> date:
    d = inicio
    contados = 0
    while contados < dias:
        d = d + timedelta(days=1)
        if d.weekday() < 5:
            contados += 1
    return d


def _valida_data(v: str) -> str:
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
        return _valida_data(v)


_lock = threading.Lock()
_cotacoes = {}
_contador = 0


@app.post("/admin/reset", status_code=204)
def reset():
    global _contador
    with _lock:
        _cotacoes.clear()
        _contador = 0
    return Response(status_code=204)


@app.post("/cotacoes", status_code=201)
def criar_cotacao(entrada: CotacaoEntrada):
    global _contador
    uf_origem = _uf_do_cep(entrada.cep_origem)
    uf_destino = _uf_do_cep(entrada.cep_destino)
    if uf_origem is None or uf_destino is None:
        raise ErroAPI(422, "CEP_FORA_DA_AREA", "CEP fora da área atendida.")

    base = os.environ.get("URL_TRANSPORTADORAS", "")
    r = _chamar("GET", f"{base}/transportadoras", params={"somente_ativas": "true"})
    if r.status_code != 200:
        raise _indisponivel()
    try:
        transportadoras = r.json()
    except ValueError:
        raise _indisponivel()
    if not isinstance(transportadoras, list):
        raise _indisponivel()

    p = entrada.pacote
    peso_cubado = _ceil_div(p.comprimento_cm * p.largura_cm * p.altura_cm, 6)
    peso_taxado = max(p.peso_g, peso_cubado)
    kg = _ceil_div(peso_taxado, 1000)
    interestadual = uf_origem != uf_destino
    data_cot = date.fromisoformat(entrada.data)

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
            preco = t["tarifa_base_centavos"] + t["tarifa_kg_centavos"] * kg
            prazo = t["prazo_dias_uteis"]
            if interestadual:
                preco = _ceil_div(preco * (100 + t["adicional_interestadual_pct"]), 100)
                prazo = prazo + t["dias_adicionais_interestadual"]
            opcoes.append({
                "transportadora_id": t["id"],
                "nome": t["nome"],
                "preco_centavos": preco,
                "prazo_dias_uteis": prazo,
                "data_prevista_entrega": _somar_dias_uteis(data_cot, prazo).isoformat(),
            })
        except (KeyError, TypeError):
            raise _indisponivel()

    opcoes.sort(key=lambda o: (o["preco_centavos"], o["prazo_dias_uteis"], o["transportadora_id"]))

    with _lock:
        _contador += 1
        cot_id = f"COT-{_contador:06d}"
        cotacao = {
            "id": cot_id,
            "cep_origem": entrada.cep_origem,
            "cep_destino": entrada.cep_destino,
            "uf_origem": uf_origem,
            "uf_destino": uf_destino,
            "peso_taxado_g": peso_taxado,
            "data": entrada.data,
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
