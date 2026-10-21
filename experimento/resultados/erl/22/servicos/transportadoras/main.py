# arquivo: servicos/transportadoras/main.py
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


def dados_invalidos(mensagem: str = "Dados inválidos.") -> JSONResponse:
    return erro(422, "DADOS_INVALIDOS", mensagem)


@app.exception_handler(RequestValidationError)
async def _handler_validacao(request: Request, exc: RequestValidationError):
    return dados_invalidos("Requisição com dados inválidos.")


@app.exception_handler(StarletteHTTPException)
async def _handler_http(request: Request, exc: StarletteHTTPException):
    if exc.status_code == 404:
        return erro(404, "NAO_ENCONTRADO", "Recurso não encontrado.")
    if exc.status_code == 405:
        return erro(405, "METODO_NAO_PERMITIDO", "Método não permitido.")
    return erro(exc.status_code, "ERRO", str(exc.detail))


def _eh_int(v) -> bool:
    return isinstance(v, int) and not isinstance(v, bool)


async def _ler_json(request: Request):
    try:
        return True, await request.json()
    except Exception:
        return False, None


def _parse_bool(valor):
    if valor is None:
        return True, None
    v = valor.strip().lower()
    if v in ("true", "1", "yes", "on"):
        return True, True
    if v in ("false", "0", "no", "off"):
        return True, False
    return False, None


def _validar_transportadora(corpo):
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
    regras = [
        ("tarifa_base_centavos", 0, None),
        ("tarifa_kg_centavos", 0, None),
        ("adicional_interestadual_pct", 0, 100),
        ("prazo_dias_uteis", 1, None),
        ("dias_adicionais_interestadual", 0, None),
        ("peso_maximo_g", 1, None),
    ]
    for campo, minimo, maximo in regras:
        v = corpo.get(campo)
        if not _eh_int(v):
            return f"Campo '{campo}' inválido."
        if v < minimo or (maximo is not None and v > maximo):
            return f"Campo '{campo}' fora das restrições."
    return None


@app.post("/transportadoras")
async def cadastrar(request: Request):
    ok, corpo = await _ler_json(request)
    if not ok:
        return dados_invalidos("Corpo JSON inválido.")
    problema = _validar_transportadora(corpo)
    if problema:
        return dados_invalidos(problema)
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
    ok, somente = _parse_bool(request.query_params.get("somente_ativas"))
    if not ok:
        return dados_invalidos("Parâmetro 'somente_ativas' inválido.")
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
async def alterar_ativa(tid: str, request: Request):
    ok, corpo = await _ler_json(request)
    if not ok or not isinstance(corpo, dict):
        return dados_invalidos("Corpo JSON inválido.")
    ativa = corpo.get("ativa")
    if not isinstance(ativa, bool):
        return dados_invalidos("Campo 'ativa' inválido.")
    t = _transportadoras.get(tid)
    if t is None:
        return erro(404, "TRANSPORTADORA_NAO_ENCONTRADA", "Transportadora não encontrada.")
    t["ativa"] = ativa
    return JSONResponse(status_code=200, content=t)


@app.post("/admin/reset")
async def reset():
    _transportadoras.clear()
    return Response(status_code=204)
