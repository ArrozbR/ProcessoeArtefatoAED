# arquivo: servicos/transportadoras/main.py
import re
import threading
from typing import Any

from fastapi import Body, FastAPI, Request
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse, Response
from starlette.exceptions import HTTPException as StarletteHTTPException

app = FastAPI()

UFS_VALIDAS = {"SP", "RJ", "ES", "MG", "BA", "PR", "SC", "RS"}
ID_RE = re.compile(r"^[A-Z0-9_]{2,20}$")

_lock = threading.Lock()
_transportadoras: dict = {}


def erro(status: int, codigo: str, mensagem: str) -> JSONResponse:
    return JSONResponse(status_code=status, content={"erro": codigo, "mensagem": mensagem})


@app.exception_handler(RequestValidationError)
async def _validacao(request: Request, exc: RequestValidationError):
    return erro(422, "DADOS_INVALIDOS", "Dados inválidos na requisição.")


@app.exception_handler(StarletteHTTPException)
async def _http(request: Request, exc: StarletteHTTPException):
    if exc.status_code == 404:
        return erro(404, "RECURSO_NAO_ENCONTRADO", "Recurso não encontrado.")
    if exc.status_code == 422:
        return erro(422, "DADOS_INVALIDOS", "Dados inválidos na requisição.")
    return erro(exc.status_code, "ERRO_HTTP", str(exc.detail))


def _eh_int(v: Any) -> bool:
    return type(v) is int


def _validar_transportadora(corpo: Any):
    if not isinstance(corpo, dict):
        return None
    id_ = corpo.get("id")
    if not isinstance(id_, str) or not ID_RE.match(id_):
        return None
    nome = corpo.get("nome")
    if not isinstance(nome, str) or not nome.strip():
        return None
    ufs = corpo.get("ufs_atendidas")
    if not isinstance(ufs, list) or len(ufs) == 0:
        return None
    for uf in ufs:
        if not isinstance(uf, str) or uf not in UFS_VALIDAS:
            return None
    if len(set(ufs)) != len(ufs):
        return None
    limites = [
        ("tarifa_base_centavos", 0, None),
        ("tarifa_kg_centavos", 0, None),
        ("adicional_interestadual_pct", 0, 100),
        ("prazo_dias_uteis", 1, None),
        ("dias_adicionais_interestadual", 0, None),
        ("peso_maximo_g", 1, None),
    ]
    for campo, minimo, maximo in limites:
        v = corpo.get(campo)
        if not _eh_int(v):
            return None
        if v < minimo:
            return None
        if maximo is not None and v > maximo:
            return None
    return {
        "id": id_,
        "nome": nome,
        "ufs_atendidas": list(ufs),
        "tarifa_base_centavos": corpo["tarifa_base_centavos"],
        "tarifa_kg_centavos": corpo["tarifa_kg_centavos"],
        "adicional_interestadual_pct": corpo["adicional_interestadual_pct"],
        "prazo_dias_uteis": corpo["prazo_dias_uteis"],
        "dias_adicionais_interestadual": corpo["dias_adicionais_interestadual"],
        "peso_maximo_g": corpo["peso_maximo_g"],
        "ativa": True,
    }


def _copia(t: dict) -> dict:
    c = dict(t)
    c["ufs_atendidas"] = list(t["ufs_atendidas"])
    return c


@app.post("/transportadoras")
def cadastrar(corpo: Any = Body(default=None)):
    t = _validar_transportadora(corpo)
    if t is None:
        return erro(422, "DADOS_INVALIDOS", "Dados da transportadora inválidos.")
    with _lock:
        if t["id"] in _transportadoras:
            return erro(409, "TRANSPORTADORA_DUPLICADA", "Já existe transportadora com esse id.")
        _transportadoras[t["id"]] = t
        return JSONResponse(status_code=201, content=_copia(t))


def _parse_bool(valor: str):
    v = valor.strip().lower()
    if v in ("true", "1", "yes", "on", "t", "y"):
        return True
    if v in ("false", "0", "no", "off", "f", "n"):
        return False
    return None


@app.get("/transportadoras")
def listar(request: Request):
    somente = False
    if "somente_ativas" in request.query_params:
        b = _parse_bool(request.query_params["somente_ativas"])
        if b is None:
            return erro(422, "DADOS_INVALIDOS", "Parâmetro somente_ativas inválido.")
        somente = b
    with _lock:
        itens = [
            _copia(_transportadoras[k])
            for k in sorted(_transportadoras.keys())
            if not somente or _transportadoras[k]["ativa"]
        ]
    return JSONResponse(status_code=200, content=itens)


@app.get("/transportadoras/{id_}")
def consultar(id_: str):
    with _lock:
        t = _transportadoras.get(id_)
        if t is None:
            return erro(404, "TRANSPORTADORA_NAO_ENCONTRADA", "Transportadora não encontrada.")
        return JSONResponse(status_code=200, content=_copia(t))


@app.patch("/transportadoras/{id_}")
def alterar(id_: str, corpo: Any = Body(default=None)):
    if not isinstance(corpo, dict) or "ativa" not in corpo or not isinstance(corpo["ativa"], bool):
        return erro(422, "DADOS_INVALIDOS", "Campo ativa ausente ou inválido.")
    with _lock:
        t = _transportadoras.get(id_)
        if t is None:
            return erro(404, "TRANSPORTADORA_NAO_ENCONTRADA", "Transportadora não encontrada.")
        t["ativa"] = corpo["ativa"]
        return JSONResponse(status_code=200, content=_copia(t))


@app.post("/admin/reset")
def reset():
    with _lock:
        _transportadoras.clear()
    return Response(status_code=204)
