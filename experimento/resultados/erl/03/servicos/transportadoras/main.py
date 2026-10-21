# arquivo: servicos/transportadoras/main.py
import copy
import re
import threading

from fastapi import FastAPI, Request
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse, Response
from starlette.exceptions import HTTPException as StarletteHTTPException

app = FastAPI()

UFS_VALIDAS = {"SP", "RJ", "ES", "MG", "BA", "PR", "SC", "RS"}
RE_ID = re.compile(r"[A-Z0-9_]{2,20}")

_lock = threading.Lock()
_transportadoras: dict = {}


class ErroApi(Exception):
    def __init__(self, status: int, erro: str, mensagem: str):
        super().__init__(mensagem)
        self.status = status
        self.erro = erro
        self.mensagem = mensagem


def _resposta_erro(status: int, erro: str, mensagem: str) -> JSONResponse:
    return JSONResponse(status_code=status, content={"erro": erro, "mensagem": mensagem})


@app.exception_handler(ErroApi)
async def _tratar_erro_api(request: Request, exc: ErroApi):
    return _resposta_erro(exc.status, exc.erro, exc.mensagem)


@app.exception_handler(RequestValidationError)
async def _tratar_validacao(request: Request, exc: RequestValidationError):
    return _resposta_erro(422, "DADOS_INVALIDOS", "Dados da requisição inválidos.")


@app.exception_handler(StarletteHTTPException)
async def _tratar_http(request: Request, exc: StarletteHTTPException):
    if exc.status_code == 404:
        return _resposta_erro(404, "RECURSO_NAO_ENCONTRADO", "Recurso não encontrado.")
    if exc.status_code == 405:
        return _resposta_erro(405, "METODO_NAO_PERMITIDO", "Método não permitido.")
    return _resposta_erro(exc.status_code, "ERRO_HTTP", str(exc.detail))


def _invalido(mensagem: str) -> ErroApi:
    return ErroApi(422, "DADOS_INVALIDOS", mensagem)


async def _ler_corpo(request: Request) -> dict:
    try:
        corpo = await request.json()
    except Exception:
        raise _invalido("Corpo da requisição ausente ou não é JSON válido.")
    if not isinstance(corpo, dict):
        raise _invalido("O corpo da requisição deve ser um objeto JSON.")
    return corpo


def _eh_int(valor) -> bool:
    return isinstance(valor, int) and not isinstance(valor, bool)


def _validar_int(corpo: dict, campo: str, minimo=None, maximo=None) -> int:
    if campo not in corpo or not _eh_int(corpo[campo]):
        raise _invalido(f"Campo '{campo}' ausente ou não é inteiro.")
    valor = corpo[campo]
    if minimo is not None and valor < minimo:
        raise _invalido(f"Campo '{campo}' abaixo do mínimo permitido.")
    if maximo is not None and valor > maximo:
        raise _invalido(f"Campo '{campo}' acima do máximo permitido.")
    return valor


def _validar_transportadora(corpo: dict) -> dict:
    tid = corpo.get("id")
    if not isinstance(tid, str) or not RE_ID.fullmatch(tid):
        raise _invalido("Campo 'id' inválido.")
    nome = corpo.get("nome")
    if not isinstance(nome, str) or not nome.strip():
        raise _invalido("Campo 'nome' inválido.")
    ufs = corpo.get("ufs_atendidas")
    if not isinstance(ufs, list) or not ufs:
        raise _invalido("Campo 'ufs_atendidas' deve ser uma lista não vazia.")
    for uf in ufs:
        if not isinstance(uf, str) or uf not in UFS_VALIDAS:
            raise _invalido("Campo 'ufs_atendidas' contém UF inválida.")
    if len(set(ufs)) != len(ufs):
        raise _invalido("Campo 'ufs_atendidas' contém UF repetida.")
    return {
        "id": tid,
        "nome": nome,
        "ufs_atendidas": list(ufs),
        "tarifa_base_centavos": _validar_int(corpo, "tarifa_base_centavos", minimo=0),
        "tarifa_kg_centavos": _validar_int(corpo, "tarifa_kg_centavos", minimo=0),
        "adicional_interestadual_pct": _validar_int(corpo, "adicional_interestadual_pct", minimo=0, maximo=100),
        "prazo_dias_uteis": _validar_int(corpo, "prazo_dias_uteis", minimo=1),
        "dias_adicionais_interestadual": _validar_int(corpo, "dias_adicionais_interestadual", minimo=0),
        "peso_maximo_g": _validar_int(corpo, "peso_maximo_g", minimo=1),
        "ativa": True,
    }


def _parse_bool(valor: str) -> bool:
    v = valor.strip().lower()
    if v in ("true", "1", "yes", "on", "t", "y"):
        return True
    if v in ("false", "0", "no", "off", "f", "n"):
        return False
    raise _invalido("Parâmetro 'somente_ativas' deve ser booleano.")


@app.post("/transportadoras")
async def cadastrar_transportadora(request: Request):
    corpo = await _ler_corpo(request)
    transportadora = _validar_transportadora(corpo)
    with _lock:
        if transportadora["id"] in _transportadoras:
            raise ErroApi(409, "TRANSPORTADORA_DUPLICADA", "Já existe transportadora com este id.")
        _transportadoras[transportadora["id"]] = transportadora
        return JSONResponse(status_code=201, content=copy.deepcopy(transportadora))


@app.get("/transportadoras")
async def listar_transportadoras(request: Request):
    params = request.query_params
    somente_ativas = False
    if "somente_ativas" in params:
        somente_ativas = _parse_bool(params["somente_ativas"])
    with _lock:
        lista = [
            copy.deepcopy(_transportadoras[tid])
            for tid in sorted(_transportadoras)
            if not somente_ativas or _transportadoras[tid]["ativa"]
        ]
    return JSONResponse(status_code=200, content=lista)


@app.get("/transportadoras/{tid}")
async def consultar_transportadora(tid: str):
    with _lock:
        transportadora = _transportadoras.get(tid)
        if transportadora is None:
            raise ErroApi(404, "TRANSPORTADORA_NAO_ENCONTRADA", "Transportadora não encontrada.")
        return JSONResponse(status_code=200, content=copy.deepcopy(transportadora))


@app.patch("/transportadoras/{tid}")
async def alterar_ativa(tid: str, request: Request):
    corpo = await _ler_corpo(request)
    ativa = corpo.get("ativa")
    if not isinstance(ativa, bool):
        raise _invalido("Campo 'ativa' ausente ou não é booleano.")
    with _lock:
        transportadora = _transportadoras.get(tid)
        if transportadora is None:
            raise ErroApi(404, "TRANSPORTADORA_NAO_ENCONTRADA", "Transportadora não encontrada.")
        transportadora["ativa"] = ativa
        return JSONResponse(status_code=200, content=copy.deepcopy(transportadora))


@app.post("/admin/reset")
async def reset():
    with _lock:
        _transportadoras.clear()
    return Response(status_code=204)
