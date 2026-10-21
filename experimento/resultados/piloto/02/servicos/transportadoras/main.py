# arquivo: servicos/transportadoras/main.py
import re

from fastapi import FastAPI, Request
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse, Response
from starlette.exceptions import HTTPException as StarletteHTTPException

app = FastAPI()

UFS_VALIDAS = {"SP", "RJ", "ES", "MG", "BA", "PR", "SC", "RS"}

_transportadoras: dict = {}


def erro(status: int, codigo: str, mensagem: str) -> JSONResponse:
    return JSONResponse(status_code=status, content={"erro": codigo, "mensagem": mensagem})


@app.exception_handler(RequestValidationError)
async def _validacao(request: Request, exc: RequestValidationError):
    return erro(422, "DADOS_INVALIDOS", "Dados inválidos.")


@app.exception_handler(StarletteHTTPException)
async def _http(request: Request, exc: StarletteHTTPException):
    if exc.status_code == 404:
        return erro(404, "ROTA_NAO_ENCONTRADA", "Rota não encontrada.")
    if exc.status_code == 405:
        return erro(405, "METODO_NAO_PERMITIDO", "Método não permitido.")
    return erro(exc.status_code, "ERRO", str(exc.detail))


async def ler_corpo(request: Request):
    try:
        return await request.json()
    except Exception:
        return None


def eh_int(v) -> bool:
    return isinstance(v, int) and not isinstance(v, bool)


def validar_transportadora(corpo):
    if not isinstance(corpo, dict):
        return None
    tid = corpo.get("id")
    if not isinstance(tid, str) or not re.fullmatch(r"[A-Z0-9_]{2,20}", tid):
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
        if not eh_int(v):
            return None
        if v < minimo or (maximo is not None and v > maximo):
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


@app.post("/transportadoras")
async def cadastrar(request: Request):
    corpo = await ler_corpo(request)
    t = validar_transportadora(corpo)
    if t is None:
        return erro(422, "DADOS_INVALIDOS", "Dados da transportadora inválidos.")
    if t["id"] in _transportadoras:
        return erro(409, "TRANSPORTADORA_DUPLICADA", "Já existe transportadora com este id.")
    _transportadoras[t["id"]] = t
    return JSONResponse(status_code=201, content=dict(t))


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
    resultado = []
    for tid in sorted(_transportadoras):
        t = _transportadoras[tid]
        if somente_ativas and not t["ativa"]:
            continue
        resultado.append(dict(t))
    return JSONResponse(status_code=200, content=resultado)


@app.get("/transportadoras/{tid}")
async def consultar(tid: str):
    t = _transportadoras.get(tid)
    if t is None:
        return erro(404, "TRANSPORTADORA_NAO_ENCONTRADA", "Transportadora não encontrada.")
    return JSONResponse(status_code=200, content=dict(t))


@app.patch("/transportadoras/{tid}")
async def alterar(tid: str, request: Request):
    corpo = await ler_corpo(request)
    if not isinstance(corpo, dict) or not isinstance(corpo.get("ativa"), bool):
        return erro(422, "DADOS_INVALIDOS", "Campo ativa ausente ou inválido.")
    t = _transportadoras.get(tid)
    if t is None:
        return erro(404, "TRANSPORTADORA_NAO_ENCONTRADA", "Transportadora não encontrada.")
    t["ativa"] = corpo["ativa"]
    return JSONResponse(status_code=200, content=dict(t))


@app.post("/admin/reset")
async def reset():
    _transportadoras.clear()
    return Response(status_code=204)
