# arquivo: servicos/transportadoras/main.py
import json
import re

from fastapi import FastAPI, Request
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse, Response
from starlette.exceptions import HTTPException as StarletteHTTPException

app = FastAPI()

UFS_VALIDAS = {"SP", "RJ", "ES", "MG", "BA", "PR", "SC", "RS"}
ID_REGEX = re.compile(r"^[A-Z0-9_]{2,20}$")

_transportadoras: dict = {}


class ErroApi(Exception):
    def __init__(self, status: int, erro: str, mensagem: str):
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
    return _resposta_erro(422, "DADOS_INVALIDOS", "Dados inválidos na requisição.")


@app.exception_handler(StarletteHTTPException)
async def _tratar_http(request: Request, exc: StarletteHTTPException):
    if exc.status_code == 404:
        return _resposta_erro(404, "NAO_ENCONTRADO", "Rota não encontrada.")
    if exc.status_code == 405:
        return _resposta_erro(405, "METODO_NAO_PERMITIDO", "Método não permitido.")
    return _resposta_erro(exc.status_code, "ERRO", str(exc.detail))


def _invalido(mensagem: str = "Dados inválidos na requisição.") -> ErroApi:
    return ErroApi(422, "DADOS_INVALIDOS", mensagem)


async def _ler_json(request: Request) -> dict:
    corpo = await request.body()
    try:
        dados = json.loads(corpo)
    except Exception:
        raise _invalido("Corpo da requisição não é um JSON válido.")
    if not isinstance(dados, dict):
        raise _invalido("Corpo da requisição deve ser um objeto JSON.")
    return dados


def _eh_int(v) -> bool:
    return isinstance(v, int) and not isinstance(v, bool)


def _validar_transportadora(dados: dict) -> dict:
    campos = [
        "id",
        "nome",
        "ufs_atendidas",
        "tarifa_base_centavos",
        "tarifa_kg_centavos",
        "adicional_interestadual_pct",
        "prazo_dias_uteis",
        "dias_adicionais_interestadual",
        "peso_maximo_g",
    ]
    for c in campos:
        if c not in dados:
            raise _invalido(f"Campo obrigatório ausente: {c}.")

    tid = dados["id"]
    if not isinstance(tid, str) or not ID_REGEX.match(tid):
        raise _invalido("Campo id inválido.")

    nome = dados["nome"]
    if not isinstance(nome, str) or not nome.strip():
        raise _invalido("Campo nome inválido.")

    ufs = dados["ufs_atendidas"]
    if not isinstance(ufs, list) or len(ufs) == 0:
        raise _invalido("Campo ufs_atendidas inválido.")
    for uf in ufs:
        if not isinstance(uf, str) or uf not in UFS_VALIDAS:
            raise _invalido("UF inválida em ufs_atendidas.")
    if len(set(ufs)) != len(ufs):
        raise _invalido("UF repetida em ufs_atendidas.")

    limites = {
        "tarifa_base_centavos": (0, None),
        "tarifa_kg_centavos": (0, None),
        "adicional_interestadual_pct": (0, 100),
        "prazo_dias_uteis": (1, None),
        "dias_adicionais_interestadual": (0, None),
        "peso_maximo_g": (1, None),
    }
    for c, (minimo, maximo) in limites.items():
        v = dados[c]
        if not _eh_int(v):
            raise _invalido(f"Campo {c} deve ser inteiro.")
        if v < minimo or (maximo is not None and v > maximo):
            raise _invalido(f"Campo {c} fora dos limites.")

    return {
        "id": tid,
        "nome": nome,
        "ufs_atendidas": list(ufs),
        "tarifa_base_centavos": dados["tarifa_base_centavos"],
        "tarifa_kg_centavos": dados["tarifa_kg_centavos"],
        "adicional_interestadual_pct": dados["adicional_interestadual_pct"],
        "prazo_dias_uteis": dados["prazo_dias_uteis"],
        "dias_adicionais_interestadual": dados["dias_adicionais_interestadual"],
        "peso_maximo_g": dados["peso_maximo_g"],
        "ativa": True,
    }


def _copia(t: dict) -> dict:
    c = dict(t)
    c["ufs_atendidas"] = list(t["ufs_atendidas"])
    return c


def _parse_bool(valor: str) -> bool:
    v = valor.strip().lower()
    if v in ("true", "1", "yes", "on"):
        return True
    if v in ("false", "0", "no", "off"):
        return False
    raise _invalido("Parâmetro somente_ativas inválido.")


@app.post("/transportadoras")
async def cadastrar(request: Request):
    dados = await _ler_json(request)
    transportadora = _validar_transportadora(dados)
    if transportadora["id"] in _transportadoras:
        raise ErroApi(409, "TRANSPORTADORA_DUPLICADA", "Já existe transportadora com este id.")
    _transportadoras[transportadora["id"]] = transportadora
    return JSONResponse(status_code=201, content=_copia(transportadora))


@app.get("/transportadoras")
async def listar(request: Request):
    params = request.query_params
    somente_ativas = False
    if "somente_ativas" in params:
        somente_ativas = _parse_bool(params["somente_ativas"])
    resultado = []
    for tid in sorted(_transportadoras.keys()):
        t = _transportadoras[tid]
        if somente_ativas and not t["ativa"]:
            continue
        resultado.append(_copia(t))
    return JSONResponse(status_code=200, content=resultado)


@app.get("/transportadoras/{tid}")
async def consultar(tid: str):
    t = _transportadoras.get(tid)
    if t is None:
        raise ErroApi(404, "TRANSPORTADORA_NAO_ENCONTRADA", "Transportadora não encontrada.")
    return JSONResponse(status_code=200, content=_copia(t))


@app.patch("/transportadoras/{tid}")
async def alterar_ativa(tid: str, request: Request):
    dados = await _ler_json(request)
    if "ativa" not in dados or not isinstance(dados["ativa"], bool):
        raise _invalido("Campo ativa ausente ou com tipo errado.")
    t = _transportadoras.get(tid)
    if t is None:
        raise ErroApi(404, "TRANSPORTADORA_NAO_ENCONTRADA", "Transportadora não encontrada.")
    t["ativa"] = dados["ativa"]
    return JSONResponse(status_code=200, content=_copia(t))


@app.post("/admin/reset")
async def reset():
    _transportadoras.clear()
    return Response(status_code=204)
