# arquivo: servicos/transportadoras/main.py
import json
import re

from fastapi import FastAPI, Request
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse, Response
from starlette.exceptions import HTTPException as StarletteHTTPException

app = FastAPI()

UFS_VALIDAS = {"SP", "RJ", "ES", "MG", "BA", "PR", "SC", "RS"}
ID_RE = re.compile(r"[A-Z0-9_]{2,20}")

_transportadoras: dict = {}


def erro(status: int, codigo: str, mensagem: str) -> JSONResponse:
    return JSONResponse(status_code=status, content={"erro": codigo, "mensagem": mensagem})


def invalido(mensagem: str = "Dados inválidos.") -> JSONResponse:
    return erro(422, "DADOS_INVALIDOS", mensagem)


@app.exception_handler(RequestValidationError)
async def _validacao(request: Request, exc: RequestValidationError):
    return invalido("Requisição com dados inválidos.")


@app.exception_handler(StarletteHTTPException)
async def _http(request: Request, exc: StarletteHTTPException):
    if exc.status_code == 404:
        return erro(404, "NAO_ENCONTRADO", "Rota não encontrada.")
    if exc.status_code == 405:
        return erro(405, "METODO_NAO_PERMITIDO", "Método não permitido.")
    return erro(exc.status_code, "ERRO", str(exc.detail))


async def ler_json(request: Request):
    try:
        corpo = await request.body()
        dados = json.loads(corpo)
    except Exception:
        return None
    if not isinstance(dados, dict):
        return None
    return dados


def eh_int(v) -> bool:
    return isinstance(v, int) and not isinstance(v, bool)


def validar_transportadora(d: dict):
    tid = d.get("id")
    if not isinstance(tid, str) or not ID_RE.fullmatch(tid):
        return "Campo 'id' inválido."
    nome = d.get("nome")
    if not isinstance(nome, str) or not nome.strip():
        return "Campo 'nome' inválido."
    ufs = d.get("ufs_atendidas")
    if not isinstance(ufs, list) or not ufs:
        return "Campo 'ufs_atendidas' inválido."
    for uf in ufs:
        if not isinstance(uf, str) or uf not in UFS_VALIDAS:
            return "UF inválida em 'ufs_atendidas'."
    if len(set(ufs)) != len(ufs):
        return "UF repetida em 'ufs_atendidas'."
    regras = [
        ("tarifa_base_centavos", 0, None),
        ("tarifa_kg_centavos", 0, None),
        ("adicional_interestadual_pct", 0, 100),
        ("prazo_dias_uteis", 1, None),
        ("dias_adicionais_interestadual", 0, None),
        ("peso_maximo_g", 1, None),
    ]
    for campo, minimo, maximo in regras:
        v = d.get(campo)
        if not eh_int(v):
            return f"Campo '{campo}' inválido."
        if v < minimo or (maximo is not None and v > maximo):
            return f"Campo '{campo}' fora das restrições."
    return None


def copia(t: dict) -> dict:
    c = dict(t)
    c["ufs_atendidas"] = list(t["ufs_atendidas"])
    return c


@app.post("/admin/reset")
async def reset():
    _transportadoras.clear()
    return Response(status_code=204)


@app.post("/transportadoras")
async def cadastrar(request: Request):
    d = await ler_json(request)
    if d is None:
        return invalido("Corpo da requisição inválido.")
    msg = validar_transportadora(d)
    if msg:
        return invalido(msg)
    if d["id"] in _transportadoras:
        return erro(409, "TRANSPORTADORA_DUPLICADA", "Já existe transportadora com este id.")
    t = {
        "id": d["id"],
        "nome": d["nome"],
        "ufs_atendidas": list(d["ufs_atendidas"]),
        "tarifa_base_centavos": d["tarifa_base_centavos"],
        "tarifa_kg_centavos": d["tarifa_kg_centavos"],
        "adicional_interestadual_pct": d["adicional_interestadual_pct"],
        "prazo_dias_uteis": d["prazo_dias_uteis"],
        "dias_adicionais_interestadual": d["dias_adicionais_interestadual"],
        "peso_maximo_g": d["peso_maximo_g"],
        "ativa": True,
    }
    _transportadoras[t["id"]] = t
    return JSONResponse(status_code=201, content=copia(t))


def parse_bool(valor: str):
    v = valor.strip().lower()
    if v in ("true", "1", "yes", "on", "t", "y"):
        return True
    if v in ("false", "0", "no", "off", "f", "n"):
        return False
    return None


@app.get("/transportadoras")
async def listar(request: Request):
    somente_ativas = False
    if "somente_ativas" in request.query_params:
        b = parse_bool(request.query_params["somente_ativas"])
        if b is None:
            return invalido("Parâmetro 'somente_ativas' inválido.")
        somente_ativas = b
    lista = [copia(_transportadoras[k]) for k in sorted(_transportadoras)]
    if somente_ativas:
        lista = [t for t in lista if t["ativa"]]
    return JSONResponse(status_code=200, content=lista)


@app.get("/transportadoras/{tid}")
async def consultar(tid: str):
    t = _transportadoras.get(tid)
    if t is None:
        return erro(404, "TRANSPORTADORA_NAO_ENCONTRADA", "Transportadora não encontrada.")
    return JSONResponse(status_code=200, content=copia(t))


@app.patch("/transportadoras/{tid}")
async def alterar(tid: str, request: Request):
    d = await ler_json(request)
    if d is None:
        return invalido("Corpo da requisição inválido.")
    ativa = d.get("ativa")
    if not isinstance(ativa, bool):
        return invalido("Campo 'ativa' ausente ou inválido.")
    t = _transportadoras.get(tid)
    if t is None:
        return erro(404, "TRANSPORTADORA_NAO_ENCONTRADA", "Transportadora não encontrada.")
    t["ativa"] = ativa
    return JSONResponse(status_code=200, content=copia(t))
