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
    def __init__(self, status: int, codigo: str, mensagem: str):
        self.status = status
        self.codigo = codigo
        self.mensagem = mensagem


def _resposta_erro(status: int, codigo: str, mensagem: str) -> JSONResponse:
    return JSONResponse(status_code=status, content={"erro": codigo, "mensagem": mensagem})


@app.exception_handler(ErroAPI)
async def _tratar_erro_api(request: Request, exc: ErroAPI):
    return _resposta_erro(exc.status, exc.codigo, exc.mensagem)


@app.exception_handler(RequestValidationError)
async def _tratar_validacao(request: Request, exc: RequestValidationError):
    return _resposta_erro(422, "DADOS_INVALIDOS", "Dados inválidos na requisição.")


@app.exception_handler(StarletteHTTPException)
async def _tratar_http(request: Request, exc: StarletteHTTPException):
    if exc.status_code == 404:
        return _resposta_erro(404, "NAO_ENCONTRADO", "Rota não encontrada.")
    if exc.status_code == 405:
        return _resposta_erro(405, "METODO_NAO_PERMITIDO", "Método não permitido.")
    return _resposta_erro(exc.status_code, "ERRO", str(exc.detail))


def _invalido(mensagem: str) -> ErroAPI:
    return ErroAPI(422, "DADOS_INVALIDOS", mensagem)


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


def _validar_int(corpo: dict, campo: str, minimo: int, maximo=None) -> int:
    if campo not in corpo or not _eh_int(corpo[campo]):
        raise _invalido(f"Campo '{campo}' ausente ou não é inteiro.")
    valor = corpo[campo]
    if valor < minimo or (maximo is not None and valor > maximo):
        raise _invalido(f"Campo '{campo}' fora dos limites permitidos.")
    return valor


def _validar_transportadora(corpo: dict) -> dict:
    tid = corpo.get("id")
    if not isinstance(tid, str) or not RE_ID.fullmatch(tid):
        raise _invalido("Campo 'id' deve ter de 2 a 20 caracteres entre A-Z, 0-9 e _.")
    nome = corpo.get("nome")
    if not isinstance(nome, str) or not nome.strip():
        raise _invalido("Campo 'nome' ausente ou vazio.")
    ufs = corpo.get("ufs_atendidas")
    if not isinstance(ufs, list) or len(ufs) == 0:
        raise _invalido("Campo 'ufs_atendidas' deve ser uma lista não vazia.")
    vistos = set()
    for uf in ufs:
        if not isinstance(uf, str) or uf not in UFS_VALIDAS:
            raise _invalido("Campo 'ufs_atendidas' contém UF inválida.")
        if uf in vistos:
            raise _invalido("Campo 'ufs_atendidas' contém UF repetida.")
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


@app.post("/transportadoras", status_code=201)
async def cadastrar_transportadora(request: Request):
    corpo = await _ler_corpo(request)
    transportadora = _validar_transportadora(corpo)
    if transportadora["id"] in _transportadoras:
        raise ErroAPI(409, "TRANSPORTADORA_DUPLICADA", "Já existe transportadora com este id.")
    _transportadoras[transportadora["id"]] = transportadora
    return JSONResponse(status_code=201, content=_copia(transportadora))


def _ler_bool(valor: str) -> bool:
    v = valor.strip().lower()
    if v in ("true", "1", "yes", "on"):
        return True
    if v in ("false", "0", "no", "off"):
        return False
    raise _invalido("Parâmetro 'somente_ativas' deve ser booleano.")


@app.get("/transportadoras")
async def listar_transportadoras(request: Request):
    somente_ativas = False
    if "somente_ativas" in request.query_params:
        somente_ativas = _ler_bool(request.query_params["somente_ativas"])
    resultado = []
    for tid in sorted(_transportadoras):
        t = _transportadoras[tid]
        if somente_ativas and not t["ativa"]:
            continue
        resultado.append(_copia(t))
    return JSONResponse(status_code=200, content=resultado)


@app.get("/transportadoras/{tid}")
async def consultar_transportadora(tid: str):
    t = _transportadoras.get(tid)
    if t is None:
        raise ErroAPI(404, "TRANSPORTADORA_NAO_ENCONTRADA", "Transportadora não encontrada.")
    return JSONResponse(status_code=200, content=_copia(t))


@app.patch("/transportadoras/{tid}")
async def alterar_ativa(tid: str, request: Request):
    corpo = await _ler_corpo(request)
    ativa = corpo.get("ativa")
    if "ativa" not in corpo or not isinstance(ativa, bool):
        raise _invalido("Campo 'ativa' ausente ou não é booleano.")
    t = _transportadoras.get(tid)
    if t is None:
        raise ErroAPI(404, "TRANSPORTADORA_NAO_ENCONTRADA", "Transportadora não encontrada.")
    t["ativa"] = ativa
    return JSONResponse(status_code=200, content=_copia(t))


@app.post("/admin/reset", status_code=204)
async def reset():
    _transportadoras.clear()
    return Response(status_code=204)
