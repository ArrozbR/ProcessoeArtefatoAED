# arquivo: servicos/transportadoras/main.py
import copy
import re

from fastapi import FastAPI, Request
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse, Response
from starlette.exceptions import HTTPException as StarletteHTTPException

app = FastAPI()

UFS_VALIDAS = {"SP", "RJ", "ES", "MG", "BA", "PR", "SC", "RS"}
ID_RE = re.compile(r"[A-Z0-9_]{2,20}")

_transportadoras = {}

_INVALIDO = object()


def _erro(status, codigo, mensagem):
    return JSONResponse(status_code=status, content={"erro": codigo, "mensagem": mensagem})


@app.exception_handler(RequestValidationError)
async def _tratar_validacao(request, exc):
    return _erro(422, "DADOS_INVALIDOS", "Dados inválidos na requisição.")


@app.exception_handler(StarletteHTTPException)
async def _tratar_http(request, exc):
    if exc.status_code == 404:
        return _erro(404, "NAO_ENCONTRADO", "Rota não encontrada.")
    if exc.status_code == 405:
        return _erro(405, "METODO_NAO_PERMITIDO", "Método não permitido.")
    return _erro(exc.status_code, "ERRO", str(exc.detail))


async def _ler_json(request):
    try:
        return await request.json()
    except Exception:
        return _INVALIDO


def _eh_int(v):
    return isinstance(v, int) and not isinstance(v, bool)


def _validar_cadastro(corpo):
    if not isinstance(corpo, dict):
        return "Corpo deve ser um objeto JSON."
    tid = corpo.get("id")
    if not isinstance(tid, str) or not ID_RE.fullmatch(tid):
        return "Campo 'id' inválido."
    nome = corpo.get("nome")
    if not isinstance(nome, str) or not nome.strip():
        return "Campo 'nome' inválido."
    ufs = corpo.get("ufs_atendidas")
    if not isinstance(ufs, list) or not ufs:
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
        v = corpo.get(campo)
        if not _eh_int(v):
            return f"Campo '{campo}' inválido."
        if v < minimo or (maximo is not None and v > maximo):
            return f"Campo '{campo}' fora dos limites."
    return None


@app.post("/transportadoras")
async def cadastrar(request: Request):
    corpo = await _ler_json(request)
    if corpo is _INVALIDO:
        return _erro(422, "DADOS_INVALIDOS", "Corpo JSON inválido.")
    problema = _validar_cadastro(corpo)
    if problema:
        return _erro(422, "DADOS_INVALIDOS", problema)
    if corpo["id"] in _transportadoras:
        return _erro(409, "TRANSPORTADORA_DUPLICADA", "Já existe transportadora com esse id.")
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


def _ler_bool(valor):
    v = valor.strip().lower()
    if v in ("true", "1", "yes", "on"):
        return True
    if v in ("false", "0", "no", "off"):
        return False
    return None


@app.get("/transportadoras")
async def listar(request: Request):
    somente_ativas = False
    bruto = request.query_params.get("somente_ativas")
    if bruto is not None:
        somente_ativas = _ler_bool(bruto)
        if somente_ativas is None:
            return _erro(422, "DADOS_INVALIDOS", "Parâmetro 'somente_ativas' inválido.")
    itens = [
        copy.deepcopy(_transportadoras[k])
        for k in sorted(_transportadoras)
        if not somente_ativas or _transportadoras[k]["ativa"]
    ]
    return JSONResponse(status_code=200, content=itens)


@app.get("/transportadoras/{tid}")
async def consultar(tid: str):
    t = _transportadoras.get(tid)
    if t is None:
        return _erro(404, "TRANSPORTADORA_NAO_ENCONTRADA", "Transportadora não encontrada.")
    return JSONResponse(status_code=200, content=copy.deepcopy(t))


@app.patch("/transportadoras/{tid}")
async def alterar(tid: str, request: Request):
    corpo = await _ler_json(request)
    if corpo is _INVALIDO or not isinstance(corpo, dict) or not isinstance(corpo.get("ativa"), bool):
        return _erro(422, "DADOS_INVALIDOS", "Campo 'ativa' ausente ou inválido.")
    t = _transportadoras.get(tid)
    if t is None:
        return _erro(404, "TRANSPORTADORA_NAO_ENCONTRADA", "Transportadora não encontrada.")
    t["ativa"] = corpo["ativa"]
    return JSONResponse(status_code=200, content=copy.deepcopy(t))


@app.post("/admin/reset")
async def reset():
    _transportadoras.clear()
    return Response(status_code=204)
