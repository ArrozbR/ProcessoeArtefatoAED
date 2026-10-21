# arquivo: servicos/transportadoras/main.py
import copy
import re
from typing import Any

from fastapi import Body, FastAPI, Request
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse, Response
from starlette.exceptions import HTTPException as StarletteHTTPException

app = FastAPI()

UFS_VALIDAS = {"SP", "RJ", "ES", "MG", "BA", "PR", "SC", "RS"}
RE_ID = re.compile(r"[A-Z0-9_]{2,20}")

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
    if exc.status_code == 405:
        return erro(405, "METODO_NAO_PERMITIDO", "Método não permitido.")
    return erro(exc.status_code, "ERRO", str(exc.detail))


def _eh_int(v: Any) -> bool:
    return isinstance(v, int) and not isinstance(v, bool)


def _validar_cadastro(corpo: Any) -> bool:
    if not isinstance(corpo, dict):
        return False
    tid = corpo.get("id")
    if not isinstance(tid, str) or not RE_ID.fullmatch(tid):
        return False
    nome = corpo.get("nome")
    if not isinstance(nome, str) or not nome.strip():
        return False
    ufs = corpo.get("ufs_atendidas")
    if not isinstance(ufs, list) or len(ufs) == 0:
        return False
    for uf in ufs:
        if not isinstance(uf, str) or uf not in UFS_VALIDAS:
            return False
    if len(set(ufs)) != len(ufs):
        return False
    limites = {
        "tarifa_base_centavos": (0, None),
        "tarifa_kg_centavos": (0, None),
        "adicional_interestadual_pct": (0, 100),
        "prazo_dias_uteis": (1, None),
        "dias_adicionais_interestadual": (0, None),
        "peso_maximo_g": (1, None),
    }
    for campo, (minimo, maximo) in limites.items():
        v = corpo.get(campo)
        if not _eh_int(v):
            return False
        if v < minimo:
            return False
        if maximo is not None and v > maximo:
            return False
    return True


@app.post("/admin/reset")
def reset():
    _transportadoras.clear()
    return Response(status_code=204)


@app.post("/transportadoras")
def cadastrar(corpo: Any = Body(None)):
    if not _validar_cadastro(corpo):
        return erro(422, "DADOS_INVALIDOS", "Dados da transportadora inválidos.")
    if corpo["id"] in _transportadoras:
        return erro(409, "TRANSPORTADORA_DUPLICADA", "Já existe transportadora com este id.")
    t = {
        "id": corpo["id"],
        "nome": corpo["nome"],
        "ufs_atendidas": list(corpo["ufs_atendidas"]),
        "tarifa_base_centavos": corpo["tarifa_base_centavos"],
        "tarifa_kg_centavos": corpo["tarifa_kg_centavos"],
        "adicional_interestadual_pct": corpo["adicional_interestadual_pct"],
        "prazo_dias_uteis": corpo["prazo_dias_uteis"],
        "dias_adicionais_interestadual": corpo["dias_adicionais_interestadual"],
        "peso_maximo_g": corpo["peso_maximo_g"],
        "ativa": True,
    }
    _transportadoras[t["id"]] = t
    return JSONResponse(status_code=201, content=copy.deepcopy(t))


def _ler_bool(valor: str):
    v = valor.strip().lower()
    if v in ("true", "1", "yes", "on"):
        return True
    if v in ("false", "0", "no", "off"):
        return False
    return None


@app.get("/transportadoras")
def listar(request: Request):
    somente_ativas = False
    if "somente_ativas" in request.query_params:
        b = _ler_bool(request.query_params["somente_ativas"])
        if b is None:
            return erro(422, "DADOS_INVALIDOS", "Parâmetro somente_ativas inválido.")
        somente_ativas = b
    lista = [
        copy.deepcopy(_transportadoras[k])
        for k in sorted(_transportadoras)
        if not somente_ativas or _transportadoras[k]["ativa"]
    ]
    return JSONResponse(status_code=200, content=lista)


@app.get("/transportadoras/{tid}")
def consultar(tid: str):
    t = _transportadoras.get(tid)
    if t is None:
        return erro(404, "TRANSPORTADORA_NAO_ENCONTRADA", "Transportadora não encontrada.")
    return JSONResponse(status_code=200, content=copy.deepcopy(t))


@app.patch("/transportadoras/{tid}")
def alterar_ativa(tid: str, corpo: Any = Body(None)):
    if not isinstance(corpo, dict) or not isinstance(corpo.get("ativa"), bool):
        return erro(422, "DADOS_INVALIDOS", "Campo ativa ausente ou inválido.")
    t = _transportadoras.get(tid)
    if t is None:
        return erro(404, "TRANSPORTADORA_NAO_ENCONTRADA", "Transportadora não encontrada.")
    t["ativa"] = corpo["ativa"]
    return JSONResponse(status_code=200, content=copy.deepcopy(t))
