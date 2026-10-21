# arquivo: servicos/transportadoras/main.py
import re
import threading

from fastapi import FastAPI, Request
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse, Response
from starlette.exceptions import HTTPException as StarletteHTTPException

app = FastAPI()

UFS_VALIDAS = {"SP", "RJ", "ES", "MG", "BA", "PR", "SC", "RS"}
ID_RE = re.compile(r"^[A-Z0-9_]{2,20}$")

_lock = threading.Lock()
_transportadoras: dict = {}


class ErroAPI(Exception):
    def __init__(self, status: int, erro: str, mensagem: str):
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
        return _erro(404, "RECURSO_NAO_ENCONTRADO", "Recurso não encontrado.")
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


def _int(corpo: dict, campo: str, minimo=None, maximo=None) -> int:
    if campo not in corpo:
        raise _invalido(f"Campo '{campo}' ausente.")
    valor = corpo[campo]
    if isinstance(valor, bool) or not isinstance(valor, int):
        raise _invalido(f"Campo '{campo}' deve ser inteiro.")
    if minimo is not None and valor < minimo:
        raise _invalido(f"Campo '{campo}' abaixo do mínimo.")
    if maximo is not None and valor > maximo:
        raise _invalido(f"Campo '{campo}' acima do máximo.")
    return valor


def _validar_transportadora(corpo: dict) -> dict:
    if "id" not in corpo:
        raise _invalido("Campo 'id' ausente.")
    id_ = corpo["id"]
    if not isinstance(id_, str) or not ID_RE.match(id_):
        raise _invalido("Campo 'id' inválido.")

    if "nome" not in corpo:
        raise _invalido("Campo 'nome' ausente.")
    nome = corpo["nome"]
    if not isinstance(nome, str) or not nome.strip():
        raise _invalido("Campo 'nome' inválido.")

    if "ufs_atendidas" not in corpo:
        raise _invalido("Campo 'ufs_atendidas' ausente.")
    ufs = corpo["ufs_atendidas"]
    if not isinstance(ufs, list) or len(ufs) == 0:
        raise _invalido("Campo 'ufs_atendidas' deve ser uma lista não vazia.")
    for uf in ufs:
        if not isinstance(uf, str) or uf not in UFS_VALIDAS:
            raise _invalido("UF inválida em 'ufs_atendidas'.")
    if len(set(ufs)) != len(ufs):
        raise _invalido("UF repetida em 'ufs_atendidas'.")

    return {
        "id": id_,
        "nome": nome,
        "ufs_atendidas": list(ufs),
        "tarifa_base_centavos": _int(corpo, "tarifa_base_centavos", minimo=0),
        "tarifa_kg_centavos": _int(corpo, "tarifa_kg_centavos", minimo=0),
        "adicional_interestadual_pct": _int(corpo, "adicional_interestadual_pct", minimo=0, maximo=100),
        "prazo_dias_uteis": _int(corpo, "prazo_dias_uteis", minimo=1),
        "dias_adicionais_interestadual": _int(corpo, "dias_adicionais_interestadual", minimo=0),
        "peso_maximo_g": _int(corpo, "peso_maximo_g", minimo=1),
        "ativa": True,
    }


def _copia(t: dict) -> dict:
    c = dict(t)
    c["ufs_atendidas"] = list(t["ufs_atendidas"])
    return c


@app.post("/transportadoras", status_code=201)
async def cadastrar(request: Request):
    corpo = await _ler_corpo(request)
    transportadora = _validar_transportadora(corpo)
    with _lock:
        if transportadora["id"] in _transportadoras:
            raise ErroAPI(409, "TRANSPORTADORA_DUPLICADA", "Já existe transportadora com este id.")
        _transportadoras[transportadora["id"]] = transportadora
        return JSONResponse(status_code=201, content=_copia(transportadora))


@app.get("/transportadoras")
def listar(somente_ativas: bool = False):
    with _lock:
        itens = [_copia(_transportadoras[k]) for k in sorted(_transportadoras)]
    if somente_ativas:
        itens = [t for t in itens if t["ativa"]]
    return itens


@app.get("/transportadoras/{id_}")
def consultar(id_: str):
    with _lock:
        t = _transportadoras.get(id_)
        if t is None:
            raise ErroAPI(404, "TRANSPORTADORA_NAO_ENCONTRADA", "Transportadora não encontrada.")
        return _copia(t)


@app.patch("/transportadoras/{id_}")
async def alterar_ativa(id_: str, request: Request):
    corpo = await _ler_corpo(request)
    if "ativa" not in corpo or not isinstance(corpo["ativa"], bool):
        raise _invalido("Campo 'ativa' ausente ou inválido.")
    with _lock:
        t = _transportadoras.get(id_)
        if t is None:
            raise ErroAPI(404, "TRANSPORTADORA_NAO_ENCONTRADA", "Transportadora não encontrada.")
        t["ativa"] = corpo["ativa"]
        return _copia(t)


@app.post("/admin/reset", status_code=204)
def reset():
    with _lock:
        _transportadoras.clear()
    return Response(status_code=204)
