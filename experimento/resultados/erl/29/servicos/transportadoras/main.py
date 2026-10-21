# arquivo: servicos/transportadoras/main.py
import re
import threading
from typing import Any, Optional

from fastapi import Body, FastAPI, Request
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse, Response
from starlette.exceptions import HTTPException as StarletteHTTPException

app = FastAPI()


class ErroAPI(Exception):
    def __init__(self, status: int, codigo: str, mensagem: str):
        self.status = status
        self.codigo = codigo
        self.mensagem = mensagem


@app.exception_handler(ErroAPI)
async def _erro_api(request: Request, exc: ErroAPI):
    return JSONResponse(status_code=exc.status, content={"erro": exc.codigo, "mensagem": exc.mensagem})


@app.exception_handler(RequestValidationError)
async def _erro_validacao(request: Request, exc: RequestValidationError):
    return JSONResponse(status_code=422, content={"erro": "DADOS_INVALIDOS", "mensagem": "Dados inválidos."})


@app.exception_handler(StarletteHTTPException)
async def _erro_http(request: Request, exc: StarletteHTTPException):
    if exc.status_code == 404:
        codigo = "NAO_ENCONTRADO"
    elif exc.status_code == 405:
        codigo = "METODO_NAO_PERMITIDO"
    elif exc.status_code == 422:
        codigo = "DADOS_INVALIDOS"
    else:
        codigo = "ERRO"
    return JSONResponse(status_code=exc.status_code, content={"erro": codigo, "mensagem": str(exc.detail)})


def invalido(mensagem: str = "Dados inválidos.") -> ErroAPI:
    return ErroAPI(422, "DADOS_INVALIDOS", mensagem)


UFS = {"SP", "RJ", "ES", "MG", "BA", "PR", "SC", "RS"}
RE_ID = re.compile(r"^[A-Z0-9_]{2,20}$")

_lock = threading.RLock()
_transportadoras: dict = {}


def _eh_int(v: Any) -> bool:
    return isinstance(v, int) and not isinstance(v, bool)


def _validar_int(body: dict, campo: str, minimo: Optional[int] = None, maximo: Optional[int] = None) -> int:
    if campo not in body:
        raise invalido(f"Campo '{campo}' ausente.")
    v = body[campo]
    if not _eh_int(v):
        raise invalido(f"Campo '{campo}' deve ser inteiro.")
    if minimo is not None and v < minimo:
        raise invalido(f"Campo '{campo}' abaixo do mínimo.")
    if maximo is not None and v > maximo:
        raise invalido(f"Campo '{campo}' acima do máximo.")
    return v


def _validar_transportadora(body: Any) -> dict:
    if not isinstance(body, dict):
        raise invalido("Corpo deve ser um objeto JSON.")
    tid = body.get("id")
    if not isinstance(tid, str) or not RE_ID.match(tid):
        raise invalido("Campo 'id' inválido.")
    nome = body.get("nome")
    if not isinstance(nome, str) or not nome.strip():
        raise invalido("Campo 'nome' inválido.")
    ufs = body.get("ufs_atendidas")
    if not isinstance(ufs, list) or len(ufs) == 0:
        raise invalido("Campo 'ufs_atendidas' inválido.")
    vistos = set()
    for uf in ufs:
        if not isinstance(uf, str) or uf not in UFS or uf in vistos:
            raise invalido("Campo 'ufs_atendidas' inválido.")
        vistos.add(uf)
    return {
        "id": tid,
        "nome": nome,
        "ufs_atendidas": list(ufs),
        "tarifa_base_centavos": _validar_int(body, "tarifa_base_centavos", 0),
        "tarifa_kg_centavos": _validar_int(body, "tarifa_kg_centavos", 0),
        "adicional_interestadual_pct": _validar_int(body, "adicional_interestadual_pct", 0, 100),
        "prazo_dias_uteis": _validar_int(body, "prazo_dias_uteis", 1),
        "dias_adicionais_interestadual": _validar_int(body, "dias_adicionais_interestadual", 0),
        "peso_maximo_g": _validar_int(body, "peso_maximo_g", 1),
        "ativa": True,
    }


def _copia(t: dict) -> dict:
    c = dict(t)
    c["ufs_atendidas"] = list(t["ufs_atendidas"])
    return c


@app.post("/transportadoras", status_code=201)
def cadastrar(body: Any = Body(None)):
    dados = _validar_transportadora(body)
    with _lock:
        if dados["id"] in _transportadoras:
            raise ErroAPI(409, "TRANSPORTADORA_DUPLICADA", "Já existe transportadora com este id.")
        _transportadoras[dados["id"]] = dados
        return JSONResponse(status_code=201, content=_copia(dados))


@app.get("/transportadoras")
def listar(somente_ativas: Optional[str] = None):
    filtro = False
    if somente_ativas is not None:
        v = somente_ativas.strip().lower()
        if v in ("true", "1", "yes", "on", "t", "y"):
            filtro = True
        elif v in ("false", "0", "no", "off", "f", "n"):
            filtro = False
        else:
            raise invalido("Parâmetro 'somente_ativas' inválido.")
    with _lock:
        itens = [
            _copia(_transportadoras[k])
            for k in sorted(_transportadoras)
            if not filtro or _transportadoras[k]["ativa"]
        ]
    return itens


@app.get("/transportadoras/{tid}")
def consultar(tid: str):
    with _lock:
        t = _transportadoras.get(tid)
        if t is None:
            raise ErroAPI(404, "TRANSPORTADORA_NAO_ENCONTRADA", "Transportadora não encontrada.")
        return _copia(t)


@app.patch("/transportadoras/{tid}")
def alterar_ativa(tid: str, body: Any = Body(None)):
    if not isinstance(body, dict) or "ativa" not in body or not isinstance(body["ativa"], bool):
        raise invalido("Campo 'ativa' ausente ou inválido.")
    with _lock:
        t = _transportadoras.get(tid)
        if t is None:
            raise ErroAPI(404, "TRANSPORTADORA_NAO_ENCONTRADA", "Transportadora não encontrada.")
        t["ativa"] = body["ativa"]
        return _copia(t)


@app.post("/admin/reset", status_code=204)
def reset():
    with _lock:
        _transportadoras.clear()
    return Response(status_code=204)
