# arquivo: servicos/transportadoras/main.py
import re

from fastapi import FastAPI, Request
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse, Response
from starlette.exceptions import HTTPException as StarletteHTTPException

app = FastAPI()

UFS_VALIDAS = {"SP", "RJ", "ES", "MG", "BA", "PR", "SC", "RS"}
RE_ID = re.compile(r"^[A-Z0-9_]{2,20}$")

_transportadoras: dict = {}


class ErroAPI(Exception):
    def __init__(self, status: int, erro: str, mensagem: str):
        self.status = status
        self.erro = erro
        self.mensagem = mensagem


def _resposta_erro(status: int, erro: str, mensagem: str) -> JSONResponse:
    return JSONResponse(status_code=status, content={"erro": erro, "mensagem": mensagem})


@app.exception_handler(ErroAPI)
async def _tratar_erro_api(request: Request, exc: ErroAPI):
    return _resposta_erro(exc.status, exc.erro, exc.mensagem)


@app.exception_handler(RequestValidationError)
async def _tratar_validacao(request: Request, exc: RequestValidationError):
    return _resposta_erro(422, "DADOS_INVALIDOS", "Dados inválidos na requisição.")


@app.exception_handler(StarletteHTTPException)
async def _tratar_http(request: Request, exc: StarletteHTTPException):
    if exc.status_code == 404:
        return _resposta_erro(404, "RECURSO_NAO_ENCONTRADO", "Recurso não encontrado.")
    if exc.status_code == 422:
        return _resposta_erro(422, "DADOS_INVALIDOS", "Dados inválidos na requisição.")
    return _resposta_erro(exc.status_code, "ERRO_HTTP", str(exc.detail))


def _invalido(mensagem: str = "Dados inválidos na requisição."):
    raise ErroAPI(422, "DADOS_INVALIDOS", mensagem)


async def _ler_corpo(request: Request) -> dict:
    try:
        corpo = await request.json()
    except Exception:
        _invalido("Corpo da requisição não é um JSON válido.")
    if not isinstance(corpo, dict):
        _invalido("Corpo da requisição deve ser um objeto JSON.")
    return corpo


def _eh_int(v) -> bool:
    return isinstance(v, int) and not isinstance(v, bool)


def _validar_int(corpo: dict, campo: str, minimo=None, maximo=None) -> int:
    if campo not in corpo or not _eh_int(corpo[campo]):
        _invalido(f"Campo '{campo}' ausente ou não inteiro.")
    v = corpo[campo]
    if minimo is not None and v < minimo:
        _invalido(f"Campo '{campo}' abaixo do mínimo.")
    if maximo is not None and v > maximo:
        _invalido(f"Campo '{campo}' acima do máximo.")
    return v


def _validar_transportadora(corpo: dict) -> dict:
    tid = corpo.get("id")
    if not isinstance(tid, str) or not RE_ID.match(tid):
        _invalido("Campo 'id' inválido.")
    nome = corpo.get("nome")
    if not isinstance(nome, str) or not nome.strip():
        _invalido("Campo 'nome' inválido.")
    ufs = corpo.get("ufs_atendidas")
    if not isinstance(ufs, list) or len(ufs) == 0:
        _invalido("Campo 'ufs_atendidas' inválido.")
    for uf in ufs:
        if not isinstance(uf, str) or uf not in UFS_VALIDAS:
            _invalido("Campo 'ufs_atendidas' contém UF inválida.")
    if len(set(ufs)) != len(ufs):
        _invalido("Campo 'ufs_atendidas' contém UF repetida.")
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


@app.post("/transportadoras", status_code=201)
async def cadastrar_transportadora(request: Request):
    corpo = await _ler_corpo(request)
    t = _validar_transportadora(corpo)
    if t["id"] in _transportadoras:
        raise ErroAPI(409, "TRANSPORTADORA_DUPLICADA", "Já existe transportadora com este id.")
    _transportadoras[t["id"]] = t
    return JSONResponse(status_code=201, content=_copia(t))


@app.get("/transportadoras")
async def listar_transportadoras(request: Request):
    params = request.query_params
    somente_ativas = False
    if "somente_ativas" in params:
        valor = params.get("somente_ativas", "").strip().lower()
        if valor in ("true", "1", "yes", "on", "t", "y"):
            somente_ativas = True
        elif valor in ("false", "0", "no", "off", "f", "n"):
            somente_ativas = False
        else:
            _invalido("Parâmetro 'somente_ativas' inválido.")
    lista = [
        _copia(_transportadoras[k])
        for k in sorted(_transportadoras)
        if not somente_ativas or _transportadoras[k]["ativa"]
    ]
    return JSONResponse(status_code=200, content=lista)


@app.get("/transportadoras/{tid}")
async def consultar_transportadora(tid: str):
    t = _transportadoras.get(tid)
    if t is None:
        raise ErroAPI(404, "TRANSPORTADORA_NAO_ENCONTRADA", "Transportadora não encontrada.")
    return JSONResponse(status_code=200, content=_copia(t))


@app.patch("/transportadoras/{tid}")
async def alterar_ativa(tid: str, request: Request):
    corpo = await _ler_corpo(request)
    if "ativa" not in corpo or not isinstance(corpo["ativa"], bool):
        _invalido("Campo 'ativa' ausente ou inválido.")
    t = _transportadoras.get(tid)
    if t is None:
        raise ErroAPI(404, "TRANSPORTADORA_NAO_ENCONTRADA", "Transportadora não encontrada.")
    t["ativa"] = corpo["ativa"]
    return JSONResponse(status_code=200, content=_copia(t))


@app.post("/admin/reset", status_code=204)
async def reset():
    _transportadoras.clear()
    return Response(status_code=204)
