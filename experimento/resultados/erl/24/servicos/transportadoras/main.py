# arquivo: servicos/transportadoras/main.py
import json
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
_transportadoras = {}


def erro(status, codigo, mensagem):
    return JSONResponse(status_code=status, content={"erro": codigo, "mensagem": mensagem})


def dados_invalidos(mensagem="Dados inválidos."):
    return erro(422, "DADOS_INVALIDOS", mensagem)


@app.exception_handler(RequestValidationError)
async def _validacao(request, exc):
    return dados_invalidos("Requisição com dados inválidos.")


@app.exception_handler(StarletteHTTPException)
async def _http(request, exc):
    if exc.status_code == 404:
        return erro(404, "RECURSO_NAO_ENCONTRADO", "Recurso não encontrado.")
    if exc.status_code == 405:
        return erro(405, "METODO_NAO_PERMITIDO", "Método não permitido.")
    return erro(exc.status_code, "ERRO", str(exc.detail))


def eh_int(v):
    return isinstance(v, int) and not isinstance(v, bool)


async def ler_json(request):
    try:
        raw = await request.body()
        if not raw:
            return None, False
        return json.loads(raw), True
    except Exception:
        return None, False


def validar_transportadora(corpo):
    if not isinstance(corpo, dict):
        return None
    tid = corpo.get("id")
    if not isinstance(tid, str) or not ID_RE.fullmatch(tid):
        return None
    nome = corpo.get("nome")
    if not isinstance(nome, str) or not nome.strip():
        return None
    ufs = corpo.get("ufs_atendidas")
    if not isinstance(ufs, list) or len(ufs) == 0:
        return None
    vistos = set()
    for uf in ufs:
        if not isinstance(uf, str) or uf not in UFS_VALIDAS or uf in vistos:
            return None
        vistos.add(uf)
    limites = {
        "tarifa_base_centavos": (0, None),
        "tarifa_kg_centavos": (0, None),
        "adicional_interestadual_pct": (0, 100),
        "prazo_dias_uteis": (1, None),
        "dias_adicionais_interestadual": (0, None),
        "peso_maximo_g": (1, None),
    }
    resultado = {"id": tid, "nome": nome, "ufs_atendidas": list(ufs)}
    for campo, (minimo, maximo) in limites.items():
        v = corpo.get(campo)
        if not eh_int(v):
            return None
        if minimo is not None and v < minimo:
            return None
        if maximo is not None and v > maximo:
            return None
        resultado[campo] = v
    resultado["ativa"] = True
    return resultado


@app.post("/transportadoras")
async def cadastrar(request: Request):
    corpo, ok = await ler_json(request)
    if not ok:
        return dados_invalidos("Corpo ausente ou JSON inválido.")
    t = validar_transportadora(corpo)
    if t is None:
        return dados_invalidos("Campos ausentes ou fora das restrições.")
    with _lock:
        if t["id"] in _transportadoras:
            return erro(409, "TRANSPORTADORA_DUPLICADA", "Já existe transportadora com este id.")
        _transportadoras[t["id"]] = t
        return JSONResponse(status_code=201, content=dict(t))


def parse_bool(valor):
    v = valor.strip().lower()
    if v in ("true", "1", "yes", "on"):
        return True
    if v in ("false", "0", "no", "off"):
        return False
    return None


@app.get("/transportadoras")
async def listar(request: Request):
    somente = False
    if "somente_ativas" in request.query_params:
        b = parse_bool(request.query_params["somente_ativas"])
        if b is None:
            return dados_invalidos("Parâmetro somente_ativas inválido.")
        somente = b
    with _lock:
        itens = [dict(t) for _, t in sorted(_transportadoras.items())]
    if somente:
        itens = [t for t in itens if t["ativa"]]
    return JSONResponse(status_code=200, content=itens)


@app.get("/transportadoras/{tid}")
async def consultar(tid: str):
    with _lock:
        t = _transportadoras.get(tid)
        if t is None:
            return erro(404, "TRANSPORTADORA_NAO_ENCONTRADA", "Transportadora não encontrada.")
        return JSONResponse(status_code=200, content=dict(t))


@app.patch("/transportadoras/{tid}")
async def ativar(tid: str, request: Request):
    corpo, ok = await ler_json(request)
    if not ok or not isinstance(corpo, dict) or not isinstance(corpo.get("ativa"), bool):
        return dados_invalidos("Campo ativa ausente ou com tipo errado.")
    with _lock:
        t = _transportadoras.get(tid)
        if t is None:
            return erro(404, "TRANSPORTADORA_NAO_ENCONTRADA", "Transportadora não encontrada.")
        t["ativa"] = corpo["ativa"]
        return JSONResponse(status_code=200, content=dict(t))


@app.post("/admin/reset")
async def reset():
    with _lock:
        _transportadoras.clear()
    return Response(status_code=204)
