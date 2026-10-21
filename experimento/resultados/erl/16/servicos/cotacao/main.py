# arquivo: servicos/cotacao/main.py
import copy
import os
import re
import threading
from datetime import date, timedelta
from typing import Any

import httpx
from fastapi import Body, FastAPI, Request
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse, Response
from starlette.exceptions import HTTPException as StarletteHTTPException

app = FastAPI()

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
RE_CEP = re.compile(r"[0-9]{8}")
RE_DATA = re.compile(r"[0-9]{4}-[0-9]{2}-[0-9]{2}")

_lock = threading.RLock()
_cotacoes: dict = {}
_contador = {"cotacao": 0}


class ErroAPI(Exception):
    def __init__(self, status: int, erro: str, mensagem: str):
        super().__init__(mensagem)
        self.status = status
        self.erro = erro
        self.mensagem = mensagem


def _erro(status: int, erro: str, mensagem: str) -> JSONResponse:
    return JSONResponse(status_code=status, content={"erro": erro, "mensagem": mensagem})


@app.exception_handler(ErroAPI)
async def _tratar_erro_api(request: Request, exc: ErroAPI):
    return _erro(exc.status, exc.erro, exc.mensagem)


@app.exception_handler(RequestValidationError)
async def _tratar_validacao(request: Request, exc: RequestValidationError):
    return _erro(422, "DADOS_INVALIDOS", "Dados inválidos na requisição.")


@app.exception_handler(StarletteHTTPException)
async def _tratar_http(request: Request, exc: StarletteHTTPException):
    if exc.status_code == 404:
        return _erro(404, "NAO_ENCONTRADO", "Recurso não encontrado.")
    if exc.status_code == 405:
        return _erro(405, "METODO_NAO_PERMITIDO", "Método não permitido.")
    return _erro(exc.status_code, "ERRO_HTTP", str(exc.detail))


def _invalido(msg: str) -> ErroAPI:
    return ErroAPI(422, "DADOS_INVALIDOS", msg)


def _eh_int(v: Any) -> bool:
    return isinstance(v, int) and not isinstance(v, bool)


def _int_faixa(obj: dict, campo: str, minimo: int, maximo: int) -> int:
    v = obj.get(campo)
    if not _eh_int(v) or v < minimo or v > maximo:
        raise _invalido(f"Campo '{campo}' inválido.")
    return v


def _validar_data(v: Any, campo: str) -> date:
    if not isinstance(v, str) or not RE_DATA.fullmatch(v):
        raise _invalido(f"Campo '{campo}' inválido.")
    try:
        return date.fromisoformat(v)
    except ValueError:
        raise _invalido(f"Campo '{campo}' inválido.")


def _uf_do_cep(cep: str):
    n = int(cep)
    for uf, ini, fim in FAIXAS_CEP:
        if ini <= n <= fim:
            return uf
    return None


def _teto_div(a: int, b: int) -> int:
    return -(-a // b)


def _somar_dias_uteis(d: date, n: int) -> date:
    atual = d
    while n > 0:
        atual += timedelta(days=1)
        if atual.weekday() < 5:
            n -= 1
    return atual


def _buscar_transportadoras_ativas() -> list:
    url = os.environ["URL_TRANSPORTADORAS"] + "/transportadoras"
    try:
        with httpx.Client(timeout=10.0) as cliente:
            resp = cliente.get(url, params={"somente_ativas": "true"})
    except httpx.HTTPError:
        raise ErroAPI(503, "SERVICO_INDISPONIVEL", "Serviço Transportadoras indisponível.")
    if resp.status_code >= 500 or resp.status_code != 200:
        raise ErroAPI(503, "SERVICO_INDISPONIVEL", "Serviço Transportadoras indisponível.")
    try:
        dados = resp.json()
    except ValueError:
        raise ErroAPI(503, "SERVICO_INDISPONIVEL", "Resposta inválida do serviço Transportadoras.")
    if not isinstance(dados, list):
        raise ErroAPI(503, "SERVICO_INDISPONIVEL", "Resposta inválida do serviço Transportadoras.")
    return dados


@app.post("/cotacoes", status_code=201)
def cotar(corpo: Any = Body(default=None)):
    if not isinstance(corpo, dict):
        raise _invalido("Corpo deve ser um objeto JSON.")
    cep_origem = corpo.get("cep_origem")
    cep_destino = corpo.get("cep_destino")
    if not isinstance(cep_origem, str) or not RE_CEP.fullmatch(cep_origem):
        raise _invalido("Campo 'cep_origem' inválido.")
    if not isinstance(cep_destino, str) or not RE_CEP.fullmatch(cep_destino):
        raise _invalido("Campo 'cep_destino' inválido.")
    pacote = corpo.get("pacote")
    if not isinstance(pacote, dict):
        raise _invalido("Campo 'pacote' inválido.")
    peso_g = _int_faixa(pacote, "peso_g", 1, 100000)
    comp = _int_faixa(pacote, "comprimento_cm", 1, 200)
    larg = _int_faixa(pacote, "largura_cm", 1, 200)
    alt = _int_faixa(pacote, "altura_cm", 1, 200)
    data_str = corpo.get("data")
    data_cot = _validar_data(data_str, "data")

    uf_origem = _uf_do_cep(cep_origem)
    uf_destino = _uf_do_cep(cep_destino)
    if uf_origem is None or uf_destino is None:
        raise ErroAPI(422, "CEP_FORA_DA_AREA", "CEP de origem ou destino fora da área atendida.")

    transportadoras = _buscar_transportadoras_ativas()

    peso_cubado = _teto_div(comp * larg * alt, 6)
    peso_taxado = max(peso_g, peso_cubado)
    kg = _teto_div(peso_taxado, 1000)
    interestadual = uf_origem != uf_destino

    opcoes = []
    for t in transportadoras:
        if not isinstance(t, dict):
            continue
        if not t.get("ativa", False):
            continue
        ufs = t.get("ufs_atendidas") or []
        if uf_origem not in ufs or uf_destino not in ufs:
            continue
        if peso_taxado > t["peso_maximo_g"]:
            continue
        preco = t["tarifa_base_centavos"] + t["tarifa_kg_centavos"] * kg
        prazo = t["prazo_dias_uteis"]
        if interestadual:
            preco = _teto_div(preco * (100 + t["adicional_interestadual_pct"]), 100)
            prazo += t["dias_adicionais_interestadual"]
        opcoes.append({
            "transportadora_id": t["id"],
            "nome": t["nome"],
            "preco_centavos": preco,
            "prazo_dias_uteis": prazo,
            "data_prevista_entrega": _somar_dias_uteis(data_cot, prazo).isoformat(),
        })
    opcoes.sort(key=lambda o: (o["preco_centavos"], o["prazo_dias_uteis"], o["transportadora_id"]))

    with _lock:
        _contador["cotacao"] += 1
        cid = f"COT-{_contador['cotacao']:06d}"
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
def consultar_cotacao(cid: str):
    with _lock:
        c = _cotacoes.get(cid)
        if c is None:
            raise ErroAPI(404, "COTACAO_NAO_ENCONTRADA", "Cotação não encontrada.")
        return copy.deepcopy(c)


@app.post("/admin/reset", status_code=204)
def reset():
    with _lock:
        _cotacoes.clear()
        _contador["cotacao"] = 0
    return Response(status_code=204)
