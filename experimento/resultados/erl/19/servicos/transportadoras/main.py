# arquivo: servicos/transportadoras/main.py
import re
import threading

from fastapi import FastAPI, Request
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse, Response
from starlette.exceptions import HTTPException as StarletteHTTPException

UFS_VALIDAS = {"SP", "RJ", "ES", "MG", "BA", "PR", "SC", "RS"}
ID_RE = re.compile(r"[A-Z0-9_]{2,20}")


class ErroAPI(Exception):
    def __init__(self, status: int, erro: str, mensagem: str):
        super().__init__(mensagem)
        self.status = status
        self.erro = erro
        self.mensagem = mensagem


app = FastAPI()

_lock = threading.Lock()
_transportadoras: dict = {}


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
        raise _invalido("Corpo JSON inválido ou ausente.")
    if not isinstance(corpo, dict):
        raise _invalido("O corpo deve ser um objeto JSON.")
    return corpo


def _e_int(v) -> bool:
    return isinstance(v, int) and not isinstance(v, bool)


def _validar_int(corpo: dict, campo: str, minimo=None, maximo=None) -> int:
    if campo not in corpo or not _e_int(corpo[campo]):
        raise _invalido(f"Campo '{campo}' ausente ou não é inteiro.")
    v = corpo[campo]
    if minimo is not None and v < minimo:
        raise _invalido(f"Campo '{campo}' abaixo do mínimo.")
    if maximo is not None and v > maximo:
        raise _invalido(f"Campo '{campo}' acima do máximo.")
    return v


def _validar_transportadora(corpo: dict) -> dict:
    tid = corpo.get("id")
    if not isinstance(tid, str) or not ID_RE.fullmatch(tid):
        raise _invalido("Campo 'id' inválido.")
    nome = corpo.get("nome")
    if not isinstance(nome, str) or nome.strip() == "":
        raise _invalido("Campo 'nome' inválido.")
    ufs = corpo.get("ufs_atendidas")
    if not isinstance(ufs, list) or len(ufs) == 0:
        raise _invalido("Campo 'ufs_atendidas' inválido.")
    vistos = set()
    for uf in ufs:
        if not isinstance(uf, str) or uf not in UFS_VALIDAS or uf in vistos:
            raise _invalido("Campo 'ufs_atendidas' inválido.")
        vistos.add(uf)
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


def _copia(t: dict) -> dict:
    c = dict(t)
    c["ufs_atendidas"] = list(t["ufs_atendidas"])
    return c


@app.post("/transportadoras")
async def cadastrar(request: Request):
    corpo = await _ler_corpo(request)
    nova = _validar_transportadora(corpo)
    with _lock:
        if nova["id"] in _transportadoras:
            raise ErroAPI(409, "TRANSPORTADORA_DUPLICADA", "Já existe transportadora com esse id.")
        _transportadoras[nova["id"]] = nova
        resposta = _copia(nova)
    return JSONResponse(status_code=201, content=resposta)


def _parse_bool(valor: str) -> bool:
    v = valor.strip().lower()
    if v in ("true", "1"):
        return True
    if v in ("false", "0"):
        return False
    raise _invalido("Parâmetro 'somente_ativas' inválido.")


@app.get("/transportadoras")
async def listar(request: Request):
    somente_ativas = False
    if "somente_ativas" in request.query_params:
        somente_ativas = _parse_bool(request.query_params["somente_ativas"])
    with _lock:
        lista = [
            _copia(_transportadoras[k])
            for k in sorted(_transportadoras)
            if not somente_ativas or _transportadoras[k]["ativa"]
        ]
    return JSONResponse(status_code=200, content=lista)


@app.get("/transportadoras/{tid}")
async def consultar(tid: str):
    with _lock:
        t = _transportadoras.get(tid)
        if t is None:
            raise ErroAPI(404, "TRANSPORTADORA_NAO_ENCONTRADA", "Transportadora não encontrada.")
        resposta = _copia(t)
    return JSONResponse(status_code=200, content=resposta)


@app.patch("/transportadoras/{tid}")
async def ativar_desativar(tid: str, request: Request):
    corpo = await _ler_corpo(request)
    if "ativa" not in corpo or not isinstance(corpo["ativa"], bool):
        raise _invalido("Campo 'ativa' ausente ou não é booleano.")
    with _lock:
        t = _transportadoras.get(tid)
        if t is None:
            raise ErroAPI(404, "TRANSPORTADORA_NAO_ENCONTRADA", "Transportadora não encontrada.")
        t["ativa"] = corpo["ativa"]
        resposta = _copia(t)
    return JSONResponse(status_code=200, content=resposta)


@app.post("/admin/reset")
async def reset():
    with _lock:
        _transportadoras.clear()
    return Response(status_code=204)
