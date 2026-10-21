# arquivo: servicos/transportadoras/main.py
import re
from typing import Any

from fastapi import FastAPI, Request
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse, Response
from starlette.exceptions import HTTPException as StarletteHTTPException

app = FastAPI()

UFS_VALIDAS = {"SP", "RJ", "ES", "MG", "BA", "PR", "SC", "RS"}
ID_RE = re.compile(r"^[A-Z0-9_]{2,20}$")

_transportadoras: dict[str, dict] = {}


def erro(status: int, codigo: str, mensagem: str) -> JSONResponse:
    return JSONResponse(status_code=status, content={"erro": codigo, "mensagem": mensagem})


def invalido(mensagem: str = "Dados inválidos.") -> JSONResponse:
    return erro(422, "DADOS_INVALIDOS", mensagem)


@app.exception_handler(RequestValidationError)
async def _validacao(request: Request, exc: RequestValidationError):
    return invalido("Dados inválidos na requisição.")


@app.exception_handler(StarletteHTTPException)
async def _http(request: Request, exc: StarletteHTTPException):
    if exc.status_code == 404:
        return erro(404, "RECURSO_NAO_ENCONTRADO", "Recurso não encontrado.")
    if exc.status_code == 405:
        return erro(405, "METODO_NAO_PERMITIDO", "Método não permitido.")
    return erro(exc.status_code, "ERRO", str(exc.detail))


async def ler_json(request: Request) -> tuple[bool, Any]:
    try:
        return True, await request.json()
    except Exception:
        return False, None


def eh_int(v: Any) -> bool:
    return isinstance(v, int) and not isinstance(v, bool)


def parse_bool(v: str | None) -> bool | None:
    if v is None:
        return None
    s = v.strip().lower()
    if s in ("true", "1", "yes", "on"):
        return True
    if s in ("false", "0", "no", "off"):
        return False
    raise ValueError("bool inválido")


def validar_transportadora(corpo: Any) -> str | None:
    if not isinstance(corpo, dict):
        return "Corpo deve ser um objeto JSON."
    tid = corpo.get("id")
    if not isinstance(tid, str) or not ID_RE.match(tid):
        return "Campo 'id' inválido."
    nome = corpo.get("nome")
    if not isinstance(nome, str) or not nome.strip():
        return "Campo 'nome' inválido."
    ufs = corpo.get("ufs_atendidas")
    if not isinstance(ufs, list) or not ufs:
        return "Campo 'ufs_atendidas' inválido."
    for uf in ufs:
        if not isinstance(uf, str) or uf not in UFS_VALIDAS:
            return "UF inválida em 'ufs_atendidas'."
    if len(set(ufs)) != len(ufs):
        return "UF repetida em 'ufs_atendidas'."
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
        if not eh_int(v):
            return f"Campo '{campo}' inválido."
        if v < minimo or (maximo is not None and v > maximo):
            return f"Campo '{campo}' fora dos limites."
    return None


@app.post("/transportadoras")
async def cadastrar(request: Request):
    ok, corpo = await ler_json(request)
    if not ok:
        return invalido("JSON inválido.")
    msg = validar_transportadora(corpo)
    if msg:
        return invalido(msg)
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
    return JSONResponse(status_code=201, content=t)


@app.get("/transportadoras")
async def listar(request: Request):
    try:
        somente = parse_bool(request.query_params.get("somente_ativas"))
    except ValueError:
        return invalido("Parâmetro 'somente_ativas' inválido.")
    itens = [_transportadoras[k] for k in sorted(_transportadoras)]
    if somente:
        itens = [t for t in itens if t["ativa"]]
    return JSONResponse(status_code=200, content=itens)


@app.get("/transportadoras/{tid}")
async def consultar(tid: str):
    t = _transportadoras.get(tid)
    if t is None:
        return erro(404, "TRANSPORTADORA_NAO_ENCONTRADA", "Transportadora não encontrada.")
    return JSONResponse(status_code=200, content=t)


@app.patch("/transportadoras/{tid}")
async def alterar(tid: str, request: Request):
    ok, corpo = await ler_json(request)
    if not ok or not isinstance(corpo, dict) or not isinstance(corpo.get("ativa"), bool):
        return invalido("Campo 'ativa' ausente ou inválido.")
    t = _transportadoras.get(tid)
    if t is None:
        return erro(404, "TRANSPORTADORA_NAO_ENCONTRADA", "Transportadora não encontrada.")
    t["ativa"] = corpo["ativa"]
    return JSONResponse(status_code=200, content=t)


@app.post("/admin/reset")
async def reset():
    _transportadoras.clear()
    return Response(status_code=204)
