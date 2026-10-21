# arquivo: servicos/transportadoras/main.py
import re
import threading

from fastapi import FastAPI, Request
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


def dados_invalidos(mensagem: str = "Dados inválidos.") -> JSONResponse:
    return erro(422, "DADOS_INVALIDOS", mensagem)


@app.exception_handler(RequestValidationError)
async def _handler_validacao(request: Request, exc: RequestValidationError):
    return dados_invalidos("Requisição com dados inválidos.")


@app.exception_handler(StarletteHTTPException)
async def _handler_http(request: Request, exc: StarletteHTTPException):
    if exc.status_code == 404:
        return erro(404, "RECURSO_NAO_ENCONTRADO", "Recurso não encontrado.")
    if exc.status_code == 405:
        return erro(405, "METODO_NAO_PERMITIDO", "Método não permitido.")
    return erro(exc.status_code, "ERRO", str(exc.detail))


async def ler_json(request: Request):
    try:
        return await request.json()
    except Exception:
        return None


def is_int(v) -> bool:
    return isinstance(v, int) and not isinstance(v, bool)


def parse_bool(valor: str):
    v = valor.strip().lower()
    if v in ("true", "1", "yes", "on", "t", "y"):
        return True
    if v in ("false", "0", "no", "off", "f", "n"):
        return False
    return None


def validar_transportadora(corpo):
    if not isinstance(corpo, dict):
        return None
    tid = corpo.get("id")
    if not isinstance(tid, str) or not ID_RE.match(tid):
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
    limites = {
        "tarifa_base_centavos": (0, None),
        "tarifa_kg_centavos": (0, None),
        "adicional_interestadual_pct": (0, 100),
        "prazo_dias_uteis": (1, None),
        "dias_adicionais_interestadual": (0, None),
        "peso_maximo_g": (1, None),
    }
    valores = {}
    for campo, (minimo, maximo) in limites.items():
        v = corpo.get(campo)
        if not is_int(v):
            return None
        if v < minimo:
            return None
        if maximo is not None and v > maximo:
            return None
        valores[campo] = v
    return {
        "id": tid,
        "nome": nome,
        "ufs_atendidas": list(ufs),
        "tarifa_base_centavos": valores["tarifa_base_centavos"],
        "tarifa_kg_centavos": valores["tarifa_kg_centavos"],
        "adicional_interestadual_pct": valores["adicional_interestadual_pct"],
        "prazo_dias_uteis": valores["prazo_dias_uteis"],
        "dias_adicionais_interestadual": valores["dias_adicionais_interestadual"],
        "peso_maximo_g": valores["peso_maximo_g"],
        "ativa": True,
    }


def copia(t: dict) -> dict:
    c = dict(t)
    c["ufs_atendidas"] = list(t["ufs_atendidas"])
    return c


@app.post("/transportadoras")
async def cadastrar(request: Request):
    corpo = await ler_json(request)
    t = validar_transportadora(corpo)
    if t is None:
        return dados_invalidos("Transportadora com campos ausentes ou fora das restrições.")
    with _lock:
        if t["id"] in _transportadoras:
            return erro(409, "TRANSPORTADORA_DUPLICADA", "Já existe transportadora com esse id.")
        _transportadoras[t["id"]] = t
        return JSONResponse(status_code=201, content=copia(t))


@app.get("/transportadoras")
async def listar(request: Request):
    somente_ativas = False
    if "somente_ativas" in request.query_params:
        valor = parse_bool(request.query_params.get("somente_ativas", ""))
        if valor is None:
            return dados_invalidos("Parâmetro somente_ativas inválido.")
        somente_ativas = valor
    with _lock:
        itens = [copia(_transportadoras[k]) for k in sorted(_transportadoras)]
    if somente_ativas:
        itens = [t for t in itens if t["ativa"]]
    return JSONResponse(status_code=200, content=itens)


@app.get("/transportadoras/{tid}")
async def consultar(tid: str):
    with _lock:
        t = _transportadoras.get(tid)
        if t is None:
            return erro(404, "TRANSPORTADORA_NAO_ENCONTRADA", "Transportadora não encontrada.")
        return JSONResponse(status_code=200, content=copia(t))


@app.patch("/transportadoras/{tid}")
async def alterar(tid: str, request: Request):
    corpo = await ler_json(request)
    if not isinstance(corpo, dict) or "ativa" not in corpo or not isinstance(corpo["ativa"], bool):
        return dados_invalidos("Campo ativa ausente ou com tipo errado.")
    with _lock:
        t = _transportadoras.get(tid)
        if t is None:
            return erro(404, "TRANSPORTADORA_NAO_ENCONTRADA", "Transportadora não encontrada.")
        t["ativa"] = corpo["ativa"]
        return JSONResponse(status_code=200, content=copia(t))


@app.post("/admin/reset")
async def reset():
    with _lock:
        _transportadoras.clear()
    return Response(status_code=204)
