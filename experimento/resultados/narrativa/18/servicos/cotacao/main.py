# arquivo: servicos/cotacao/main.py
import copy
import os
import re
import threading
from datetime import date, datetime, timedelta
from typing import Any

import httpx
from fastapi import Body, FastAPI
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse, Response
from starlette.exceptions import HTTPException as StarletteHTTPException

app = FastAPI()


class ErroAPI(Exception):
    def __init__(self, status: int, erro: str, mensagem: str):
        super().__init__(mensagem)
        self.status = status
        self.erro = erro
        self.mensagem = mensagem


def _resp_erro(status: int, erro: str, mensagem: str) -> JSONResponse:
    return JSONResponse(status_code=status, content={"erro": erro, "mensagem": mensagem})


@app.exception_handler(ErroAPI)
async def _tratar_erro_api(request, exc: ErroAPI):
    return _resp_erro(exc.status, exc.erro, exc.mensagem)


@app.exception_handler(RequestValidationError)
async def _tratar_validacao(request, exc):
    return _resp_erro(422, "DADOS_INVALIDOS", "Dados invalidos.")


@app.exception_handler(StarletteHTTPException)
async def _tratar_http(request, exc: StarletteHTTPException):
    codigos = {404: "NAO_ENCONTRADO", 405: "METODO_NAO_PERMITIDO"}
    return _resp_erro(exc.status_code, codigos.get(exc.status_code, "ERRO"), str(exc.detail))


def _invalido(msg: str = "Dados invalidos.") -> None:
    raise ErroAPI(422, "DADOS_INVALIDOS", msg)


def _indisponivel() -> None:
    raise ErroAPI(503, "SERVICO_INDISPONIVEL", "Servico dependente indisponivel.")


RE_CEP = re.compile(r"^[0-9]{8}$")
RE_DATA = re.compile(r"^[0-9]{4}-[0-9]{2}-[0-9]{2}$")

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
_cotacoes: dict = {}
_contador = {"valor": 0}
_cliente = httpx.Client(timeout=10.0)


def _eh_int(v: Any) -> bool:
    return isinstance(v, int) and not isinstance(v, bool)


def _parse_data(v: Any) -> date:
    if not isinstance(v, str) or not RE_DATA.match(v):
        _invalido("Data invalida.")
    try:
        return datetime.strptime(v, "%Y-%m-%d").date()
    except ValueError:
        _invalido("Data invalida.")


def _uf_do_cep(cep: str):
    n = int(cep)
    for uf, ini, fim in FAIXAS_CEP:
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
    return -((-a) // b)


def _buscar_transportadoras_ativas() -> list:
    base = os.environ.get("URL_TRANSPORTADORAS", "")
    try:
        r = _cliente.get(f"{base}/transportadoras", params={"somente_ativas": "true"})
    except httpx.HTTPError:
        _indisponivel()
    except Exception:
        _indisponivel()
    if r.status_code != 200:
        _indisponivel()
    try:
        dados = r.json()
    except ValueError:
        _indisponivel()
    if not isinstance(dados, list):
        _indisponivel()
    return dados


@app.post("/admin/reset", status_code=204)
def reset():
    with _lock:
        _cotacoes.clear()
        _contador["valor"] = 0
    return Response(status_code=204)


@app.post("/cotacoes", status_code=201)
def criar_cotacao(corpo: Any = Body(None)):
    if not isinstance(corpo, dict):
        _invalido("Corpo deve ser um objeto JSON.")
    cep_origem = corpo.get("cep_origem")
    cep_destino = corpo.get("cep_destino")
    if not isinstance(cep_origem, str) or not RE_CEP.match(cep_origem):
        _invalido("Campo 'cep_origem' invalido.")
    if not isinstance(cep_destino, str) or not RE_CEP.match(cep_destino):
        _invalido("Campo 'cep_destino' invalido.")
    pacote = corpo.get("pacote")
    if not isinstance(pacote, dict):
        _invalido("Campo 'pacote' invalido.")
    peso = pacote.get("peso_g")
    if not _eh_int(peso) or peso < 1 or peso > 100000:
        _invalido("Campo 'pacote.peso_g' invalido.")
    medidas = []
    for campo in ("comprimento_cm", "largura_cm", "altura_cm"):
        v = pacote.get(campo)
        if not _eh_int(v) or v < 1 or v > 200:
            _invalido(f"Campo 'pacote.{campo}' invalido.")
        medidas.append(v)
    data_str = corpo.get("data")
    data_cot = _parse_data(data_str)

    uf_origem = _uf_do_cep(cep_origem)
    uf_destino = _uf_do_cep(cep_destino)
    if uf_origem is None or uf_destino is None:
        raise ErroAPI(422, "CEP_FORA_DA_AREA", "CEP fora da area atendida.")

    peso_cubado = _ceil_div(medidas[0] * medidas[1] * medidas[2], 6)
    peso_taxado = max(peso, peso_cubado)
    quilos = _ceil_div(peso_taxado, 1000)
    interestadual = uf_origem != uf_destino

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
            opcoes.append({
                "transportadora_id": t["id"],
                "nome": t["nome"],
                "preco_centavos": int(preco),
                "prazo_dias_uteis": int(prazo),
                "data_prevista_entrega": _somar_dias_uteis(data_cot, int(prazo)).isoformat(),
            })
        except (KeyError, TypeError):
            continue
    opcoes.sort(key=lambda o: (o["preco_centavos"], o["prazo_dias_uteis"], o["transportadora_id"]))

    with _lock:
        _contador["valor"] += 1
        cid = f"COT-{_contador['valor']:06d}"
        cotacao = {
            "id": cid,
            "cep_origem": cep_origem,
            "cep_destino": cep_destino,
            "uf_origem": uf_origem,
            "uf_destino": uf_destino,
            "peso_taxado_g": peso_taxado,
            "data": data_str,
            "valida_ate": (data_cot + timedelta(days=7)).isoformat(),
            "opcoes": opcoes,
        }
        _cotacoes[cid] = cotacao
        return JSONResponse(status_code=201, content=copy.deepcopy(cotacao))


@app.get("/cotacoes/{cid}")
def obter_cotacao(cid: str):
    with _lock:
        c = _cotacoes.get(cid)
        if c is None:
            raise ErroAPI(404, "COTACAO_NAO_ENCONTRADA", "Cotacao nao encontrada.")
        return copy.deepcopy(c)
