# arquivo: servicos/cotacao/main.py
import copy
import os
import threading
from datetime import date, datetime, timedelta
from typing import Annotated

import httpx
from fastapi import FastAPI, Request
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse, Response
from pydantic import AfterValidator, BaseModel, Field
from starlette.exceptions import HTTPException as StarletteHTTPException

app = FastAPI()


class ErroAPI(Exception):
    def __init__(self, status: int, codigo: str, mensagem: str):
        super().__init__(mensagem)
        self.status = status
        self.codigo = codigo
        self.mensagem = mensagem


def _erro(status: int, codigo: str, mensagem: str) -> JSONResponse:
    return JSONResponse(status_code=status, content={"erro": codigo, "mensagem": mensagem})


@app.exception_handler(ErroAPI)
async def _tratar_erro_api(request: Request, exc: ErroAPI):
    return _erro(exc.status, exc.codigo, exc.mensagem)


@app.exception_handler(RequestValidationError)
async def _tratar_validacao(request: Request, exc: RequestValidationError):
    return _erro(422, "DADOS_INVALIDOS", "Dados invalidos na requisicao.")


@app.exception_handler(StarletteHTTPException)
async def _tratar_http(request: Request, exc: StarletteHTTPException):
    codigos = {404: "NAO_ENCONTRADO", 405: "METODO_NAO_PERMITIDO"}
    return _erro(exc.status_code, codigos.get(exc.status_code, "ERRO"), str(exc.detail))


def _validar_data(v: str) -> str:
    datetime.strptime(v, "%Y-%m-%d")
    return v


Data = Annotated[str, Field(strict=True, pattern=r"^[0-9]{4}-[0-9]{2}-[0-9]{2}$"), AfterValidator(_validar_data)]
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
    numero = int(cep)
    for uf, inicio, fim in FAIXAS_CEP:
        if inicio <= numero <= fim:
            return uf
    return None


def _somar_dias_uteis(inicio: date, dias: int) -> date:
    atual = inicio
    restantes = dias
    while restantes > 0:
        atual += timedelta(days=1)
        if atual.weekday() < 5:
            restantes -= 1
    return atual


def _teto_div(a: int, b: int) -> int:
    return -(-a // b)


_cliente_trava = threading.Lock()
_cliente = None


def _http() -> httpx.Client:
    global _cliente
    with _cliente_trava:
        if _cliente is None:
            _cliente = httpx.Client(timeout=10.0)
        return _cliente


def _buscar_transportadoras_ativas():
    base = os.environ.get("URL_TRANSPORTADORAS", "")
    try:
        resp = _http().get(base + "/transportadoras", params={"somente_ativas": "true"})
    except httpx.HTTPError:
        raise ErroAPI(503, "SERVICO_INDISPONIVEL", "Servico de transportadoras indisponivel.")
    if resp.status_code >= 500 or resp.status_code != 200:
        raise ErroAPI(503, "SERVICO_INDISPONIVEL", "Servico de transportadoras indisponivel.")
    try:
        dados = resp.json()
    except ValueError:
        raise ErroAPI(503, "SERVICO_INDISPONIVEL", "Resposta invalida do servico de transportadoras.")
    if not isinstance(dados, list):
        raise ErroAPI(503, "SERVICO_INDISPONIVEL", "Resposta invalida do servico de transportadoras.")
    return dados


_trava = threading.RLock()
_cotacoes = {}
_contador = 0


@app.post("/admin/reset", status_code=204)
def reset():
    global _contador
    with _trava:
        _cotacoes.clear()
        _contador = 0
    return Response(status_code=204)


@app.post("/cotacoes", status_code=201)
def criar_cotacao(corpo: NovaCotacao):
    global _contador
    uf_origem = _uf_do_cep(corpo.cep_origem)
    uf_destino = _uf_do_cep(corpo.cep_destino)
    if uf_origem is None or uf_destino is None:
        raise ErroAPI(422, "CEP_FORA_DA_AREA", "CEP fora da area atendida.")

    p = corpo.pacote
    peso_cubado = _teto_div(p.comprimento_cm * p.largura_cm * p.altura_cm, 6)
    peso_taxado = max(p.peso_g, peso_cubado)
    quilos = _teto_div(peso_taxado, 1000)
    interestadual = uf_origem != uf_destino
    data_cotacao = datetime.strptime(corpo.data, "%Y-%m-%d").date()

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
            preco = _teto_div(preco * (100 + int(t.get("adicional_interestadual_pct", 0))), 100)
            prazo += int(t.get("dias_adicionais_interestadual", 0))
        opcoes.append({
            "transportadora_id": t.get("id"),
            "nome": t.get("nome"),
            "preco_centavos": preco,
            "prazo_dias_uteis": prazo,
            "data_prevista_entrega": _somar_dias_uteis(data_cotacao, prazo).isoformat(),
        })
    opcoes.sort(key=lambda o: (o["preco_centavos"], o["prazo_dias_uteis"], str(o["transportadora_id"])))

    with _trava:
        _contador += 1
        cotacao = {
            "id": "COT-%06d" % _contador,
            "cep_origem": corpo.cep_origem,
            "cep_destino": corpo.cep_destino,
            "uf_origem": uf_origem,
            "uf_destino": uf_destino,
            "peso_taxado_g": peso_taxado,
            "data": corpo.data,
            "valida_ate": (data_cotacao + timedelta(days=7)).isoformat(),
            "opcoes": opcoes,
        }
        _cotacoes[cotacao["id"]] = cotacao
        return JSONResponse(status_code=201, content=copy.deepcopy(cotacao))


@app.get("/cotacoes/{cotacao_id}")
def obter_cotacao(cotacao_id: str):
    with _trava:
        c = _cotacoes.get(cotacao_id)
        if c is None:
            raise ErroAPI(404, "COTACAO_NAO_ENCONTRADA", "Cotacao nao encontrada.")
        return copy.deepcopy(c)
