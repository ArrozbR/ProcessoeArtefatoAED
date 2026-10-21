# arquivo: servicos/transportadoras/main.py
import copy
import re

from fastapi import FastAPI, Request
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse, Response
from starlette.exceptions import HTTPException as StarletteHTTPException

app = FastAPI()

UFS_VALIDAS = {"SP", "RJ", "ES", "MG", "BA", "PR", "SC", "RS"}
RE_ID = re.compile(r"[A-Z0-9_]{2,20}")

CAMPOS_INT = {
    "tarifa_base_centavos": (0, None),
    "tarifa_kg_centavos": (0, None),
    "adicional_interestadual_pct": (0, 100),
    "prazo_dias_uteis": (1, None),
    "dias_adicionais_interestadual": (0, None),
    "peso_maximo_g": (1, None),
}

_estado = {"transportadoras": {}}


def erro(status, codigo, mensagem):
    return JSONResponse(status_code=status, content={"erro": codigo, "mensagem": mensagem})


def dados_invalidos(mensagem="Dados inválidos."):
    return erro(422, "DADOS_INVALIDOS", mensagem)


@app.exception_handler(RequestValidationError)
async def _handler_validacao(request, exc):
    return dados_invalidos("Requisição com dados inválidos.")


@app.exception_handler(StarletteHTTPException)
async def _handler_http(request, exc):
    if exc.status_code == 404:
        return erro(404, "NAO_ENCONTRADO", "Rota não encontrada.")
    if exc.status_code == 405:
        return erro(405, "METODO_NAO_PERMITIDO", "Método não permitido.")
    return erro(exc.status_code, "ERRO", str(exc.detail))


_INVALIDO = object()


async def ler_json(request):
    try:
        return await request.json()
    except Exception:
        return _INVALIDO


def eh_int(v):
    return type(v) is int


def validar_transportadora(corpo):
    if not isinstance(corpo, dict):
        return "Corpo deve ser um objeto JSON."
    tid = corpo.get("id")
    if not isinstance(tid, str) or not RE_ID.fullmatch(tid):
        return "Campo 'id' inválido."
    nome = corpo.get("nome")
    if not isinstance(nome, str) or not nome.strip():
        return "Campo 'nome' inválido."
    ufs = corpo.get("ufs_atendidas")
    if not isinstance(ufs, list) or len(ufs) == 0:
        return "Campo 'ufs_atendidas' inválido."
    vistos = set()
    for uf in ufs:
        if not isinstance(uf, str) or uf not in UFS_VALIDAS or uf in vistos:
            return "Campo 'ufs_atendidas' inválido."
        vistos.add(uf)
    for campo, (minimo, maximo) in CAMPOS_INT.items():
        v = corpo.get(campo)
        if not eh_int(v):
            return f"Campo '{campo}' inválido."
        if minimo is not None and v < minimo:
            return f"Campo '{campo}' inválido."
        if maximo is not None and v > maximo:
            return f"Campo '{campo}' inválido."
    return None


def parse_bool(valor):
    v = valor.strip().lower()
    if v in ("true", "1", "yes", "on", "t", "y"):
        return True
    if v in ("false", "0", "no", "off", "f", "n"):
        return False
    return None


@app.post("/admin/reset")
async def reset():
    _estado["transportadoras"] = {}
    return Response(status_code=204)


@app.post("/transportadoras")
async def cadastrar(request: Request):
    corpo = await ler_json(request)
    if corpo is _INVALIDO:
        return dados_invalidos("Corpo JSON inválido.")
    problema = validar_transportadora(corpo)
    if problema:
        return dados_invalidos(problema)
    if corpo["id"] in _estado["transportadoras"]:
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
    _estado["transportadoras"][t["id"]] = t
    return JSONResponse(status_code=201, content=copy.deepcopy(t))


@app.get("/transportadoras")
async def listar(request: Request):
    somente = request.query_params.get("somente_ativas")
    somente_ativas = False
    if somente is not None:
        b = parse_bool(somente)
        if b is None:
            return dados_invalidos("Parâmetro 'somente_ativas' inválido.")
        somente_ativas = b
    itens = [
        copy.deepcopy(t)
        for _, t in sorted(_estado["transportadoras"].items())
        if (t["ativa"] or not somente_ativas)
    ]
    return JSONResponse(status_code=200, content=itens)


@app.get("/transportadoras/{tid}")
async def consultar(tid: str):
    t = _estado["transportadoras"].get(tid)
    if t is None:
        return erro(404, "TRANSPORTADORA_NAO_ENCONTRADA", "Transportadora não encontrada.")
    return JSONResponse(status_code=200, content=copy.deepcopy(t))


@app.patch("/transportadoras/{tid}")
async def alterar(tid: str, request: Request):
    corpo = await ler_json(request)
    if corpo is _INVALIDO or not isinstance(corpo, dict):
        return dados_invalidos("Corpo JSON inválido.")
    ativa = corpo.get("ativa")
    if type(ativa) is not bool:
        return dados_invalidos("Campo 'ativa' inválido.")
    t = _estado["transportadoras"].get(tid)
    if t is None:
        return erro(404, "TRANSPORTADORA_NAO_ENCONTRADA", "Transportadora não encontrada.")
    t["ativa"] = ativa
    return JSONResponse(status_code=200, content=copy.deepcopy(t))
