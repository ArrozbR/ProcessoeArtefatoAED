# arquivo: servicos/transportadoras/main.py
import json
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


async def _ler_json(request: Request):
    try:
        corpo = await request.body()
        return json.loads(corpo)
    except Exception:
        raise _invalido("Corpo da requisição não é um JSON válido.")


def _eh_int(v) -> bool:
    return isinstance(v, int) and not isinstance(v, bool)


def _int_campo(corpo: dict, campo: str, minimo=None, maximo=None) -> int:
    if campo not in corpo or not _eh_int(corpo[campo]):
        raise _invalido(f"Campo '{campo}' ausente ou não é inteiro.")
    v = corpo[campo]
    if minimo is not None and v < minimo:
        raise _invalido(f"Campo '{campo}' abaixo do mínimo.")
    if maximo is not None and v > maximo:
        raise _invalido(f"Campo '{campo}' acima do máximo.")
    return v


def _parse_bool_query(valor):
    if valor is None:
        return None
    v = valor.strip().lower()
    if v in ("true", "1", "yes", "on"):
        return True
    if v in ("false", "0", "no", "off"):
        return False
    raise _invalido("Parâmetro 'somente_ativas' inválido.")


@app.post("/transportadoras", status_code=201)
async def cadastrar(request: Request):
    corpo = await _ler_json(request)
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
        if not isinstance(uf, str) or uf not in UFS:
            raise _invalido("UF inválida em 'ufs_atendidas'.")
    if len(set(ufs)) != len(ufs):
        raise _invalido("UF repetida em 'ufs_atendidas'.")

    tarifa_base = _int_campo(corpo, "tarifa_base_centavos", minimo=0)
    tarifa_kg = _int_campo(corpo, "tarifa_kg_centavos", minimo=0)
    adicional = _int_campo(corpo, "adicional_interestadual_pct", minimo=0, maximo=100)
    prazo = _int_campo(corpo, "prazo_dias_uteis", minimo=1)
    dias_adic = _int_campo(corpo, "dias_adicionais_interestadual", minimo=0)
    peso_max = _int_campo(corpo, "peso_maximo_g", minimo=1)

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
    itens = [_transportadoras[k] for k in sorted(_transportadoras)]
    if somente_ativas:
        itens = [t for t in itens if t["ativa"]]
    return JSONResponse(status_code=200, content=itens)


@app.get("/transportadoras/{tid}")
async def consultar(tid: str):
    if tid not in _transportadoras:
        raise ErroAPI(404, "TRANSPORTADORA_NAO_ENCONTRADA", "Transportadora não encontrada.")
    return JSONResponse(status_code=200, content=_transportadoras[tid])


@app.patch("/transportadoras/{tid}")
async def alterar_ativa(tid: str, request: Request):
    corpo = await _ler_json(request)
    if not isinstance(corpo, dict) or "ativa" not in corpo or not isinstance(corpo["ativa"], bool):
        raise _invalido("Campo 'ativa' ausente ou não é booleano.")
    if tid not in _transportadoras:
        raise ErroAPI(404, "TRANSPORTADORA_NAO_ENCONTRADA", "Transportadora não encontrada.")
    _transportadoras[tid]["ativa"] = corpo["ativa"]
    return JSONResponse(status_code=200, content=_transportadoras[tid])


@app.post("/admin/reset", status_code=204)
async def reset():
    _transportadoras.clear()
    return Response(status_code=204)
