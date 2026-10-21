# arquivo: servicos/transportadoras/main.py
import re

from fastapi import FastAPI, Request
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse, Response
from starlette.exceptions import HTTPException as StarletteHTTPException

app = FastAPI()

UFS = {"SP", "RJ", "ES", "MG", "BA", "PR", "SC", "RS"}
ID_RE = re.compile(r"^[A-Z0-9_]{2,20}$")

_transportadoras = {}


def erro(status, codigo, mensagem):
    return JSONResponse(status_code=status, content={"erro": codigo, "mensagem": mensagem})


@app.exception_handler(RequestValidationError)
async def _validacao(request, exc):
    return erro(422, "DADOS_INVALIDOS", "Dados inválidos na requisição.")


@app.exception_handler(StarletteHTTPException)
async def _http(request, exc):
    if exc.status_code == 404:
        return erro(404, "NAO_ENCONTRADO", "Recurso não encontrado.")
    if exc.status_code == 405:
        return erro(405, "METODO_NAO_PERMITIDO", "Método não permitido.")
    return erro(exc.status_code, "ERRO", str(exc.detail))


class Invalido(Exception):
    pass


async def ler_json(request):
    try:
        corpo = await request.json()
    except Exception:
        raise Invalido()
    if not isinstance(corpo, dict):
        raise Invalido()
    return corpo


def eh_int(v):
    return isinstance(v, int) and not isinstance(v, bool)


def validar_int(corpo, campo, minimo=None, maximo=None):
    if campo not in corpo or not eh_int(corpo[campo]):
        raise Invalido()
    v = corpo[campo]
    if minimo is not None and v < minimo:
        raise Invalido()
    if maximo is not None and v > maximo:
        raise Invalido()
    return v


def validar_transportadora(corpo):
    tid = corpo.get("id")
    if not isinstance(tid, str) or not ID_RE.match(tid):
        raise Invalido()
    nome = corpo.get("nome")
    if not isinstance(nome, str) or not nome.strip():
        raise Invalido()
    ufs = corpo.get("ufs_atendidas")
    if not isinstance(ufs, list) or not ufs:
        raise Invalido()
    for uf in ufs:
        if not isinstance(uf, str) or uf not in UFS:
            raise Invalido()
    if len(set(ufs)) != len(ufs):
        raise Invalido()
    return {
        "id": tid,
        "nome": nome,
        "ufs_atendidas": list(ufs),
        "tarifa_base_centavos": validar_int(corpo, "tarifa_base_centavos", 0),
        "tarifa_kg_centavos": validar_int(corpo, "tarifa_kg_centavos", 0),
        "adicional_interestadual_pct": validar_int(corpo, "adicional_interestadual_pct", 0, 100),
        "prazo_dias_uteis": validar_int(corpo, "prazo_dias_uteis", 1),
        "dias_adicionais_interestadual": validar_int(corpo, "dias_adicionais_interestadual", 0),
        "peso_maximo_g": validar_int(corpo, "peso_maximo_g", 1),
        "ativa": True,
    }


def copia(t):
    c = dict(t)
    c["ufs_atendidas"] = list(t["ufs_atendidas"])
    return c


@app.post("/transportadoras")
async def cadastrar(request: Request):
    try:
        corpo = await ler_json(request)
        t = validar_transportadora(corpo)
    except Invalido:
        return erro(422, "DADOS_INVALIDOS", "Dados da transportadora inválidos.")
    if t["id"] in _transportadoras:
        return erro(409, "TRANSPORTADORA_DUPLICADA", "Já existe transportadora com este id.")
    _transportadoras[t["id"]] = t
    return JSONResponse(status_code=201, content=copia(t))


@app.get("/transportadoras")
async def listar(request: Request):
    valor = request.query_params.get("somente_ativas")
    somente_ativas = False
    if valor is not None:
        v = valor.strip().lower()
        if v in ("true", "1", "yes", "on", "t", "y"):
            somente_ativas = True
        elif v in ("false", "0", "no", "off", "f", "n"):
            somente_ativas = False
        else:
            return erro(422, "DADOS_INVALIDOS", "Parâmetro somente_ativas inválido.")
    lista = [
        copia(_transportadoras[k])
        for k in sorted(_transportadoras)
        if not somente_ativas or _transportadoras[k]["ativa"]
    ]
    return JSONResponse(status_code=200, content=lista)


@app.get("/transportadoras/{tid}")
async def consultar(tid: str):
    t = _transportadoras.get(tid)
    if t is None:
        return erro(404, "TRANSPORTADORA_NAO_ENCONTRADA", "Transportadora não encontrada.")
    return JSONResponse(status_code=200, content=copia(t))


@app.patch("/transportadoras/{tid}")
async def alterar(tid: str, request: Request):
    try:
        corpo = await ler_json(request)
        if "ativa" not in corpo or not isinstance(corpo["ativa"], bool):
            raise Invalido()
    except Invalido:
        return erro(422, "DADOS_INVALIDOS", "Campo ativa ausente ou inválido.")
    t = _transportadoras.get(tid)
    if t is None:
        return erro(404, "TRANSPORTADORA_NAO_ENCONTRADA", "Transportadora não encontrada.")
    t["ativa"] = corpo["ativa"]
    return JSONResponse(status_code=200, content=copia(t))


@app.post("/admin/reset")
async def reset():
    _transportadoras.clear()
    return Response(status_code=204)
