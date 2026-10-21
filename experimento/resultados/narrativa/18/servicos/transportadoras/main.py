# arquivo: servicos/transportadoras/main.py
import copy
import re
import threading
from typing import Any

from fastapi import Body, FastAPI, Query
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse, Response
from starlette.exceptions import HTTPException as StarletteHTTPException

app = FastAPI()


class ErroAPI(Exception):
    def __init__(self, status: int, erro: str, mensagem: str):
        super().__init__(mensagem)
        self.status = status
        self.erro = erro
        self.mensagem = mensagem


def _resp_erro(status: int, erro: str, mensagem: str) -> JSONResponse:
    return JSONResponse(status_code=status, content={"erro": erro, "mensagem": mensagem})


@app.exception_handler(ErroAPI)
async def _tratar_erro_api(request, exc: ErroAPI):
    return _resp_erro(exc.status, exc.erro, exc.mensagem)


@app.exception_handler(RequestValidationError)
async def _tratar_validacao(request, exc):
    return _resp_erro(422, "DADOS_INVALIDOS", "Dados invalidos.")


@app.exception_handler(StarletteHTTPException)
async def _tratar_http(request, exc: StarletteHTTPException):
    codigos = {404: "NAO_ENCONTRADO", 405: "METODO_NAO_PERMITIDO"}
    return _resp_erro(exc.status_code, codigos.get(exc.status_code, "ERRO"), str(exc.detail))


def _invalido(msg: str = "Dados invalidos.") -> None:
    raise ErroAPI(422, "DADOS_INVALIDOS", msg)


UFS_VALIDAS = {"SP", "RJ", "ES", "MG", "BA", "PR", "SC", "RS"}
RE_ID = re.compile(r"^[A-Z0-9_]{2,20}$")

_lock = threading.Lock()
_transportadoras: dict = {}


def _eh_int(v: Any) -> bool:
    return isinstance(v, int) and not isinstance(v, bool)


def _validar_transportadora(corpo: Any) -> dict:
    if not isinstance(corpo, dict):
        _invalido("Corpo deve ser um objeto JSON.")
    tid = corpo.get("id")
    if not isinstance(tid, str) or not RE_ID.match(tid):
        _invalido("Campo 'id' invalido.")
    nome = corpo.get("nome")
    if not isinstance(nome, str) or not nome.strip():
        _invalido("Campo 'nome' invalido.")
    ufs = corpo.get("ufs_atendidas")
    if not isinstance(ufs, list) or len(ufs) == 0:
        _invalido("Campo 'ufs_atendidas' invalido.")
    for uf in ufs:
        if not isinstance(uf, str) or uf not in UFS_VALIDAS:
            _invalido("UF invalida em 'ufs_atendidas'.")
    if len(set(ufs)) != len(ufs):
        _invalido("UF repetida em 'ufs_atendidas'.")

    def inteiro(nome_campo: str, minimo: int, maximo=None) -> int:
        v = corpo.get(nome_campo)
        if not _eh_int(v) or v < minimo or (maximo is not None and v > maximo):
            _invalido(f"Campo '{nome_campo}' invalido.")
        return v

    return {
        "id": tid,
        "nome": nome,
        "ufs_atendidas": list(ufs),
        "tarifa_base_centavos": inteiro("tarifa_base_centavos", 0),
        "tarifa_kg_centavos": inteiro("tarifa_kg_centavos", 0),
        "adicional_interestadual_pct": inteiro("adicional_interestadual_pct", 0, 100),
        "prazo_dias_uteis": inteiro("prazo_dias_uteis", 1),
        "dias_adicionais_interestadual": inteiro("dias_adicionais_interestadual", 0),
        "peso_maximo_g": inteiro("peso_maximo_g", 1),
        "ativa": True,
    }


@app.post("/admin/reset", status_code=204)
def reset():
    with _lock:
        _transportadoras.clear()
    return Response(status_code=204)


@app.post("/transportadoras", status_code=201)
def criar_transportadora(corpo: Any = Body(None)):
    dados = _validar_transportadora(corpo)
    with _lock:
        if dados["id"] in _transportadoras:
            raise ErroAPI(409, "TRANSPORTADORA_DUPLICADA", "Ja existe transportadora com esse id.")
        _transportadoras[dados["id"]] = dados
        return JSONResponse(status_code=201, content=copy.deepcopy(dados))


@app.get("/transportadoras")
def listar_transportadoras(somente_ativas: bool = Query(False)):
    with _lock:
        itens = [copy.deepcopy(t) for _, t in sorted(_transportadoras.items())]
    if somente_ativas:
        itens = [t for t in itens if t["ativa"]]
    return itens


@app.get("/transportadoras/{tid}")
def obter_transportadora(tid: str):
    with _lock:
        t = _transportadoras.get(tid)
        if t is None:
            raise ErroAPI(404, "TRANSPORTADORA_NAO_ENCONTRADA", "Transportadora nao encontrada.")
        return copy.deepcopy(t)


@app.patch("/transportadoras/{tid}")
def alterar_transportadora(tid: str, corpo: Any = Body(None)):
    if not isinstance(corpo, dict) or "ativa" not in corpo or not isinstance(corpo["ativa"], bool):
        _invalido("Campo 'ativa' obrigatorio e booleano.")
    with _lock:
        t = _transportadoras.get(tid)
        if t is None:
            raise ErroAPI(404, "TRANSPORTADORA_NAO_ENCONTRADA", "Transportadora nao encontrada.")
        t["ativa"] = corpo["ativa"]
        return copy.deepcopy(t)
