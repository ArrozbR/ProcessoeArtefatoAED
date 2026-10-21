# arquivo: servicos/transportadoras/main.py
import copy
import re
import threading
from typing import Any

from fastapi import Body, FastAPI, Request
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse, Response
from starlette.exceptions import HTTPException as StarletteHTTPException

app = FastAPI()

UFS_VALIDAS = {"SP", "RJ", "ES", "MG", "BA", "PR", "SC", "RS"}
RE_ID = re.compile(r"[A-Z0-9_]{2,20}")

_lock = threading.RLock()
_transportadoras: dict = {}


class ErroAPI(Exception):
    def __init__(self, status: int, erro: str, mensagem: str):
        super().__init__(mensagem)
        self.status = status
        self.erro = erro
        self.mensagem = mensagem


def _erro(status: int, erro: str, mensagem: str) -> JSONResponse:
    return JSONResponse(status_code=status, content={"erro": erro, "mensagem": mensagem})


@app.exception_handler(ErroAPI)
async def _tratar_erro_api(request: Request, exc: ErroAPI):
    return _erro(exc.status, exc.erro, exc.mensagem)


@app.exception_handler(RequestValidationError)
async def _tratar_validacao(request: Request, exc: RequestValidationError):
    return _erro(422, "DADOS_INVALIDOS", "Dados inválidos na requisição.")


@app.exception_handler(StarletteHTTPException)
async def _tratar_http(request: Request, exc: StarletteHTTPException):
    if exc.status_code == 404:
        return _erro(404, "NAO_ENCONTRADO", "Recurso não encontrado.")
    if exc.status_code == 405:
        return _erro(405, "METODO_NAO_PERMITIDO", "Método não permitido.")
    return _erro(exc.status_code, "ERRO_HTTP", str(exc.detail))


def _invalido(msg: str) -> ErroAPI:
    return ErroAPI(422, "DADOS_INVALIDOS", msg)


def _eh_int(v: Any) -> bool:
    return isinstance(v, int) and not isinstance(v, bool)


def _validar_int(corpo: dict, campo: str, minimo: int, maximo: int | None = None) -> int:
    if campo not in corpo or not _eh_int(corpo[campo]):
        raise _invalido(f"Campo '{campo}' ausente ou não inteiro.")
    v = corpo[campo]
    if v < minimo or (maximo is not None and v > maximo):
        raise _invalido(f"Campo '{campo}' fora dos limites.")
    return v


def _validar_transportadora(corpo: Any) -> dict:
    if not isinstance(corpo, dict):
        raise _invalido("Corpo deve ser um objeto JSON.")
    tid = corpo.get("id")
    if not isinstance(tid, str) or not RE_ID.fullmatch(tid):
        raise _invalido("Campo 'id' inválido.")
    nome = corpo.get("nome")
    if not isinstance(nome, str) or not nome.strip():
        raise _invalido("Campo 'nome' inválido.")
    ufs = corpo.get("ufs_atendidas")
    if not isinstance(ufs, list) or len(ufs) == 0:
        raise _invalido("Campo 'ufs_atendidas' inválido.")
    for uf in ufs:
        if not isinstance(uf, str) or uf not in UFS_VALIDAS:
            raise _invalido("UF inválida em 'ufs_atendidas'.")
    if len(set(ufs)) != len(ufs):
        raise _invalido("UF repetida em 'ufs_atendidas'.")
    return {
        "id": tid,
        "nome": nome,
        "ufs_atendidas": list(ufs),
        "tarifa_base_centavos": _validar_int(corpo, "tarifa_base_centavos", 0),
        "tarifa_kg_centavos": _validar_int(corpo, "tarifa_kg_centavos", 0),
        "adicional_interestadual_pct": _validar_int(corpo, "adicional_interestadual_pct", 0, 100),
        "prazo_dias_uteis": _validar_int(corpo, "prazo_dias_uteis", 1),
        "dias_adicionais_interestadual": _validar_int(corpo, "dias_adicionais_interestadual", 0),
        "peso_maximo_g": _validar_int(corpo, "peso_maximo_g", 1),
        "ativa": True,
    }


def _parse_bool(valor: str) -> bool:
    v = valor.strip().lower()
    if v in ("true", "1", "yes", "on"):
        return True
    if v in ("false", "0", "no", "off"):
        return False
    raise _invalido("Parâmetro 'somente_ativas' inválido.")


@app.post("/transportadoras", status_code=201)
def cadastrar_transportadora(corpo: Any = Body(default=None)):
    dados = _validar_transportadora(corpo)
    with _lock:
        if dados["id"] in _transportadoras:
            raise ErroAPI(409, "TRANSPORTADORA_DUPLICADA", "Já existe transportadora com este id.")
        _transportadoras[dados["id"]] = dados
        return JSONResponse(status_code=201, content=copy.deepcopy(dados))


@app.get("/transportadoras")
def listar_transportadoras(request: Request):
    bruto = request.query_params.get("somente_ativas")
    somente_ativas = False if bruto is None else _parse_bool(bruto)
    with _lock:
        itens = [copy.deepcopy(_transportadoras[k]) for k in sorted(_transportadoras)]
    if somente_ativas:
        itens = [t for t in itens if t["ativa"]]
    return itens


@app.get("/transportadoras/{tid}")
def consultar_transportadora(tid: str):
    with _lock:
        t = _transportadoras.get(tid)
        if t is None:
            raise ErroAPI(404, "TRANSPORTADORA_NAO_ENCONTRADA", "Transportadora não encontrada.")
        return copy.deepcopy(t)


@app.patch("/transportadoras/{tid}")
def alterar_ativa(tid: str, corpo: Any = Body(default=None)):
    if not isinstance(corpo, dict) or not isinstance(corpo.get("ativa"), bool):
        raise _invalido("Campo 'ativa' ausente ou inválido.")
    with _lock:
        t = _transportadoras.get(tid)
        if t is None:
            raise ErroAPI(404, "TRANSPORTADORA_NAO_ENCONTRADA", "Transportadora não encontrada.")
        t["ativa"] = corpo["ativa"]
        return copy.deepcopy(t)


@app.post("/admin/reset", status_code=204)
def reset():
    with _lock:
        _transportadoras.clear()
    return Response(status_code=204)
