# arquivo: servicos/transportadoras/main.py
import re

from fastapi import FastAPI, Request
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse, Response
from starlette.exceptions import HTTPException as StarletteHTTPException

app = FastAPI()

UFS = {"SP", "RJ", "ES", "MG", "BA", "PR", "SC", "RS"}
RE_ID = re.compile(r"[A-Z0-9_]{2,20}")

_transportadoras: dict = {}


class ErroAPI(Exception):
    def __init__(self, status: int, codigo: str, mensagem: str):
        self.status = status
        self.codigo = codigo
        self.mensagem = mensagem


def _erro(status: int, codigo: str, mensagem: str) -> JSONResponse:
    return JSONResponse(status_code=status, content={"erro": codigo, "mensagem": mensagem})


@app.exception_handler(ErroAPI)
async def _tratar_erro_api(request: Request, exc: ErroAPI):
    return _erro(exc.status, exc.codigo, exc.mensagem)


@app.exception_handler(RequestValidationError)
async def _tratar_validacao(request: Request, exc: RequestValidationError):
    return _erro(422, "DADOS_INVALIDOS", "Dados inválidos na requisição.")


@app.exception_handler(StarletteHTTPException)
async def _tratar_http(request: Request, exc: StarletteHTTPException):
    if exc.status_code == 404:
        return _erro(404, "NAO_ENCONTRADO", "Rota não encontrada.")
    if exc.status_code == 405:
        return _erro(405, "METODO_NAO_PERMITIDO", "Método não permitido.")
    return _erro(exc.status_code, "ERRO", str(exc.detail))


def _invalido(mensagem: str) -> ErroAPI:
    return ErroAPI(422, "DADOS_INVALIDOS", mensagem)


async def _ler_corpo(request: Request) -> dict:
    try:
        corpo = await request.json()
    except Exception:
        raise _invalido("Corpo da requisição não é um JSON válido.")
    if not isinstance(corpo, dict):
        raise _invalido("Corpo da requisição deve ser um objeto JSON.")
    return corpo


def _eh_int(v) -> bool:
    return isinstance(v, int) and not isinstance(v, bool)


def _validar_int(corpo: dict, campo: str, minimo: int, maximo=None) -> int:
    if campo not in corpo:
        raise _invalido(f"Campo '{campo}' ausente.")
    v = corpo[campo]
    if not _eh_int(v):
        raise _invalido(f"Campo '{campo}' deve ser inteiro.")
    if v < minimo or (maximo is not None and v > maximo):
        raise _invalido(f"Campo '{campo}' fora dos limites.")
    return v


def _parse_bool_query(valor):
    if valor is None:
        return False
    v = valor.strip().lower()
    if v in ("true", "1", "yes", "on", "t", "y"):
        return True
    if v in ("false", "0", "no", "off", "f", "n"):
        return False
    raise _invalido("Parâmetro 'somente_ativas' deve ser booleano.")


@app.post("/admin/reset")
async def reset():
    _transportadoras.clear()
    return Response(status_code=204)


@app.post("/transportadoras")
async def cadastrar(request: Request):
    corpo = await _ler_corpo(request)

    if "id" not in corpo:
        raise _invalido("Campo 'id' ausente.")
    tid = corpo["id"]
    if not isinstance(tid, str) or not RE_ID.fullmatch(tid):
        raise _invalido("Campo 'id' inválido.")

    if "nome" not in corpo:
        raise _invalido("Campo 'nome' ausente.")
    nome = corpo["nome"]
    if not isinstance(nome, str) or nome.strip() == "":
        raise _invalido("Campo 'nome' inválido.")

    if "ufs_atendidas" not in corpo:
        raise _invalido("Campo 'ufs_atendidas' ausente.")
    ufs = corpo["ufs_atendidas"]
    if not isinstance(ufs, list) or len(ufs) == 0:
        raise _invalido("Campo 'ufs_atendidas' deve ser lista não vazia.")
    for uf in ufs:
        if not isinstance(uf, str) or uf not in UFS:
            raise _invalido("UF inválida em 'ufs_atendidas'.")
    if len(set(ufs)) != len(ufs):
        raise _invalido("UF repetida em 'ufs_atendidas'.")

    tarifa_base = _validar_int(corpo, "tarifa_base_centavos", 0)
    tarifa_kg = _validar_int(corpo, "tarifa_kg_centavos", 0)
    adicional = _validar_int(corpo, "adicional_interestadual_pct", 0, 100)
    prazo = _validar_int(corpo, "prazo_dias_uteis", 1)
    dias_adic = _validar_int(corpo, "dias_adicionais_interestadual", 0)
    peso_max = _validar_int(corpo, "peso_maximo_g", 1)

    if tid in _transportadoras:
        raise ErroAPI(409, "TRANSPORTADORA_DUPLICADA", "Já existe transportadora com este id.")

    transportadora = {
        "id": tid,
        "nome": nome,
        "ufs_atendidas": list(ufs),
        "tarifa_base_centavos": tarifa_base,
        "tarifa_kg_centavos": tarifa_kg,
        "adicional_interestadual_pct": adicional,
        "prazo_dias_uteis": prazo,
        "dias_adicionais_interestadual": dias_adic,
        "peso_maximo_g": peso_max,
        "ativa": True,
    }
    _transportadoras[tid] = transportadora
    return JSONResponse(status_code=201, content=transportadora)


@app.get("/transportadoras")
async def listar(request: Request):
    somente_ativas = _parse_bool_query(request.query_params.get("somente_ativas"))
    lista = [
        _transportadoras[k]
        for k in sorted(_transportadoras.keys())
        if not somente_ativas or _transportadoras[k]["ativa"]
    ]
    return JSONResponse(status_code=200, content=lista)


@app.get("/transportadoras/{tid}")
async def consultar(tid: str):
    if tid not in _transportadoras:
        raise ErroAPI(404, "TRANSPORTADORA_NAO_ENCONTRADA", "Transportadora não encontrada.")
    return JSONResponse(status_code=200, content=_transportadoras[tid])


@app.patch("/transportadoras/{tid}")
async def alterar_ativa(tid: str, request: Request):
    corpo = await _ler_corpo(request)
    if "ativa" not in corpo or not isinstance(corpo["ativa"], bool):
        raise _invalido("Campo 'ativa' ausente ou com tipo errado.")
    if tid not in _transportadoras:
        raise ErroAPI(404, "TRANSPORTADORA_NAO_ENCONTRADA", "Transportadora não encontrada.")
    _transportadoras[tid]["ativa"] = corpo["ativa"]
    return JSONResponse(status_code=200, content=_transportadoras[tid])
