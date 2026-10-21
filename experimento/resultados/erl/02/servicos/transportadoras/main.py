# arquivo: servicos/transportadoras/main.py
import copy
import re
import threading
from typing import Any, Optional

from fastapi import Body, FastAPI, Query
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
async def _tratar_validacao(request, exc):
    return erro(422, "DADOS_INVALIDOS", "Dados inválidos na requisição.")


@app.exception_handler(StarletteHTTPException)
async def _tratar_http(request, exc):
    codigos = {404: "RECURSO_NAO_ENCONTRADO", 405: "METODO_NAO_PERMITIDO"}
    return erro(exc.status_code, codigos.get(exc.status_code, "ERRO_HTTP"), str(exc.detail))


def _eh_int(v) -> bool:
    return type(v) is int


def _validar_transportadora(p) -> Optional[str]:
    if not isinstance(p, dict):
        return "Corpo deve ser um objeto JSON."
    tid = p.get("id")
    if not isinstance(tid, str) or not ID_RE.match(tid):
        return "Campo 'id' inválido."
    nome = p.get("nome")
    if not isinstance(nome, str) or not nome.strip():
        return "Campo 'nome' inválido."
    ufs = p.get("ufs_atendidas")
    if not isinstance(ufs, list) or len(ufs) == 0:
        return "Campo 'ufs_atendidas' inválido."
    for uf in ufs:
        if not isinstance(uf, str) or uf not in UFS_VALIDAS:
            return "Campo 'ufs_atendidas' contém UF inválida."
    if len(set(ufs)) != len(ufs):
        return "Campo 'ufs_atendidas' contém UF repetida."
    limites = {
        "tarifa_base_centavos": (0, None),
        "tarifa_kg_centavos": (0, None),
        "adicional_interestadual_pct": (0, 100),
        "prazo_dias_uteis": (1, None),
        "dias_adicionais_interestadual": (0, None),
        "peso_maximo_g": (1, None),
    }
    for campo, (minimo, maximo) in limites.items():
        v = p.get(campo)
        if not _eh_int(v):
            return f"Campo '{campo}' inválido."
        if v < minimo or (maximo is not None and v > maximo):
            return f"Campo '{campo}' fora dos limites."
    return None


@app.post("/admin/reset", status_code=204)
def reset():
    with _lock:
        _transportadoras.clear()
    return Response(status_code=204)


@app.post("/transportadoras", status_code=201)
def cadastrar(payload: Any = Body(None)):
    msg = _validar_transportadora(payload)
    if msg:
        return erro(422, "DADOS_INVALIDOS", msg)
    with _lock:
        if payload["id"] in _transportadoras:
            return erro(409, "TRANSPORTADORA_DUPLICADA", "Já existe transportadora com este id.")
        t = {
            "id": payload["id"],
            "nome": payload["nome"],
            "ufs_atendidas": list(payload["ufs_atendidas"]),
            "tarifa_base_centavos": payload["tarifa_base_centavos"],
            "tarifa_kg_centavos": payload["tarifa_kg_centavos"],
            "adicional_interestadual_pct": payload["adicional_interestadual_pct"],
            "prazo_dias_uteis": payload["prazo_dias_uteis"],
            "dias_adicionais_interestadual": payload["dias_adicionais_interestadual"],
            "peso_maximo_g": payload["peso_maximo_g"],
            "ativa": True,
        }
        _transportadoras[t["id"]] = t
        return JSONResponse(status_code=201, content=copy.deepcopy(t))


@app.get("/transportadoras")
def listar(somente_ativas: Optional[bool] = Query(None)):
    with _lock:
        itens = [copy.deepcopy(_transportadoras[k]) for k in sorted(_transportadoras)]
    if somente_ativas:
        itens = [t for t in itens if t["ativa"]]
    return JSONResponse(status_code=200, content=itens)


@app.get("/transportadoras/{tid}")
def consultar(tid: str):
    with _lock:
        t = _transportadoras.get(tid)
        if t is None:
            return erro(404, "TRANSPORTADORA_NAO_ENCONTRADA", "Transportadora não encontrada.")
        return JSONResponse(status_code=200, content=copy.deepcopy(t))


@app.patch("/transportadoras/{tid}")
def alterar_ativa(tid: str, payload: Any = Body(None)):
    if not isinstance(payload, dict) or type(payload.get("ativa")) is not bool:
        return erro(422, "DADOS_INVALIDOS", "Campo 'ativa' ausente ou inválido.")
    with _lock:
        t = _transportadoras.get(tid)
        if t is None:
            return erro(404, "TRANSPORTADORA_NAO_ENCONTRADA", "Transportadora não encontrada.")
        t["ativa"] = payload["ativa"]
        return JSONResponse(status_code=200, content=copy.deepcopy(t))
