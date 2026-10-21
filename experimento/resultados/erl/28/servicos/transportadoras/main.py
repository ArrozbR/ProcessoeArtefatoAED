# arquivo: servicos/transportadoras/main.py
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
    codigos = {404: "NAO_ENCONTRADO", 405: "METODO_NAO_PERMITIDO"}
    return _resposta_erro(exc.status_code, codigos.get(exc.status_code, "ERRO"), str(exc.detail))


def _invalido(mensagem: str = "Dados inválidos na requisição.") -> ErroAPI:
    return ErroAPI(422, "DADOS_INVALIDOS", mensagem)


def _eh_int(v) -> bool:
    return isinstance(v, int) and not isinstance(v, bool)


async def _ler_corpo(request: Request) -> dict:
    try:
        corpo = await request.json()
    except Exception:
        raise _invalido("Corpo JSON ausente ou inválido.")
    if not isinstance(corpo, dict):
        raise _invalido("O corpo deve ser um objeto JSON.")
    return corpo


def _validar_transportadora(corpo: dict) -> dict:
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

    limites = {
        "tarifa_base_centavos": (0, None),
        "tarifa_kg_centavos": (0, None),
        "adicional_interestadual_pct": (0, 100),
        "prazo_dias_uteis": (1, None),
        "dias_adicionais_interestadual": (0, None),
        "peso_maximo_g": (1, None),
    }
    valores = {}
    for campo, (minimo, maximo) in limites.items():
        v = corpo.get(campo)
        if not _eh_int(v):
            raise _invalido(f"Campo '{campo}' inválido.")
        if v < minimo or (maximo is not None and v > maximo):
            raise _invalido(f"Campo '{campo}' fora dos limites.")
        valores[campo] = v

    return {
        "id": tid,
        "nome": nome,
        "ufs_atendidas": list(ufs),
        "tarifa_base_centavos": valores["tarifa_base_centavos"],
        "tarifa_kg_centavos": valores["tarifa_kg_centavos"],
        "adicional_interestadual_pct": valores["adicional_interestadual_pct"],
        "prazo_dias_uteis": valores["prazo_dias_uteis"],
        "dias_adicionais_interestadual": valores["dias_adicionais_interestadual"],
        "peso_maximo_g": valores["peso_maximo_g"],
        "ativa": True,
    }


def _copia(t: dict) -> dict:
    c = dict(t)
    c["ufs_atendidas"] = list(t["ufs_atendidas"])
    return c


@app.post("/transportadoras")
async def cadastrar_transportadora(request: Request):
    corpo = await _ler_corpo(request)
    transportadora = _validar_transportadora(corpo)
    with _lock:
        if transportadora["id"] in _transportadoras:
            raise ErroAPI(409, "TRANSPORTADORA_DUPLICADA", "Já existe transportadora com esse id.")
        _transportadoras[transportadora["id"]] = transportadora
        resposta = _copia(transportadora)
    return JSONResponse(status_code=201, content=resposta)


@app.get("/transportadoras")
async def listar_transportadoras(somente_ativas: bool = False):
    with _lock:
        lista = [_copia(_transportadoras[k]) for k in sorted(_transportadoras)]
    if somente_ativas:
        lista = [t for t in lista if t["ativa"]]
    return JSONResponse(status_code=200, content=lista)


@app.get("/transportadoras/{tid}")
async def consultar_transportadora(tid: str):
    with _lock:
        t = _transportadoras.get(tid)
        if t is None:
            raise ErroAPI(404, "TRANSPORTADORA_NAO_ENCONTRADA", "Transportadora não encontrada.")
        return JSONResponse(status_code=200, content=_copia(t))


@app.patch("/transportadoras/{tid}")
async def alterar_ativa(tid: str, request: Request):
    corpo = await _ler_corpo(request)
    ativa = corpo.get("ativa")
    if not isinstance(ativa, bool):
        raise _invalido("Campo 'ativa' ausente ou inválido.")
    with _lock:
        t = _transportadoras.get(tid)
        if t is None:
            raise ErroAPI(404, "TRANSPORTADORA_NAO_ENCONTRADA", "Transportadora não encontrada.")
        t["ativa"] = ativa
        return JSONResponse(status_code=200, content=_copia(t))


@app.post("/admin/reset")
async def reset():
    with _lock:
        _transportadoras.clear()
    return Response(status_code=204)
