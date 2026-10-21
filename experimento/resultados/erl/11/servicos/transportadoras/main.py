# arquivo: servicos/transportadoras/main.py
import re

from fastapi import FastAPI, Request
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse, Response
from starlette.exceptions import HTTPException as StarletteHTTPException

app = FastAPI()

UFS_VALIDAS = {"SP", "RJ", "ES", "MG", "BA", "PR", "SC", "RS"}
ID_REGEX = re.compile(r"^[A-Z0-9_]{2,20}$")

_transportadoras = {}


class ErroAPI(Exception):
    def __init__(self, status, codigo, mensagem):
        super().__init__(mensagem)
        self.status = status
        self.codigo = codigo
        self.mensagem = mensagem


def _resposta_erro(status, codigo, mensagem):
    return JSONResponse(status_code=status, content={"erro": codigo, "mensagem": mensagem})


@app.exception_handler(ErroAPI)
async def _tratar_erro_api(request, exc):
    return _resposta_erro(exc.status, exc.codigo, exc.mensagem)


@app.exception_handler(RequestValidationError)
async def _tratar_validacao(request, exc):
    return _resposta_erro(422, "DADOS_INVALIDOS", "Dados inválidos na requisição.")


@app.exception_handler(StarletteHTTPException)
async def _tratar_http(request, exc):
    if exc.status_code == 404:
        return _resposta_erro(404, "NAO_ENCONTRADO", "Rota não encontrada.")
    if exc.status_code == 422:
        return _resposta_erro(422, "DADOS_INVALIDOS", "Dados inválidos na requisição.")
    return _resposta_erro(exc.status_code, "ERRO", str(exc.detail))


def _invalido(mensagem):
    return ErroAPI(422, "DADOS_INVALIDOS", mensagem)


async def _ler_corpo(request):
    try:
        corpo = await request.json()
    except Exception:
        raise _invalido("Corpo da requisição não é um JSON válido.")
    if not isinstance(corpo, dict):
        raise _invalido("Corpo da requisição deve ser um objeto JSON.")
    return corpo


def _e_int(valor):
    return type(valor) is int


def _parse_bool(texto):
    t = texto.strip().lower()
    if t in ("true", "1", "yes", "on", "t", "y"):
        return True
    if t in ("false", "0", "no", "off", "f", "n"):
        return False
    raise _invalido("Parâmetro booleano inválido.")


def _validar_transportadora(corpo):
    id_ = corpo.get("id")
    if not isinstance(id_, str) or not ID_REGEX.match(id_):
        raise _invalido("Campo 'id' inválido.")
    nome = corpo.get("nome")
    if not isinstance(nome, str) or not nome.strip():
        raise _invalido("Campo 'nome' inválido.")
    ufs = corpo.get("ufs_atendidas")
    if not isinstance(ufs, list) or len(ufs) == 0:
        raise _invalido("Campo 'ufs_atendidas' inválido.")
    vistos = set()
    for uf in ufs:
        if not isinstance(uf, str) or uf not in UFS_VALIDAS or uf in vistos:
            raise _invalido("Campo 'ufs_atendidas' inválido.")
        vistos.add(uf)
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
        if not _e_int(v) or v < minimo or (maximo is not None and v > maximo):
            raise _invalido(f"Campo '{campo}' inválido.")
        valores[campo] = v
    return {
        "id": id_,
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


@app.post("/transportadoras")
async def cadastrar_transportadora(request: Request):
    corpo = await _ler_corpo(request)
    transportadora = _validar_transportadora(corpo)
    if transportadora["id"] in _transportadoras:
        raise ErroAPI(409, "TRANSPORTADORA_DUPLICADA", "Já existe transportadora com este id.")
    _transportadoras[transportadora["id"]] = transportadora
    return JSONResponse(status_code=201, content=dict(transportadora))


@app.get("/transportadoras")
async def listar_transportadoras(request: Request):
    somente_ativas = False
    if "somente_ativas" in request.query_params:
        somente_ativas = _parse_bool(request.query_params["somente_ativas"])
    itens = []
    for id_ in sorted(_transportadoras):
        t = _transportadoras[id_]
        if somente_ativas and not t["ativa"]:
            continue
        itens.append(dict(t))
    return JSONResponse(status_code=200, content=itens)


@app.get("/transportadoras/{id_transportadora}")
async def consultar_transportadora(id_transportadora: str):
    t = _transportadoras.get(id_transportadora)
    if t is None:
        raise ErroAPI(404, "TRANSPORTADORA_NAO_ENCONTRADA", "Transportadora não encontrada.")
    return JSONResponse(status_code=200, content=dict(t))


@app.patch("/transportadoras/{id_transportadora}")
async def alterar_ativa(id_transportadora: str, request: Request):
    corpo = await _ler_corpo(request)
    ativa = corpo.get("ativa")
    if type(ativa) is not bool:
        raise _invalido("Campo 'ativa' ausente ou inválido.")
    t = _transportadoras.get(id_transportadora)
    if t is None:
        raise ErroAPI(404, "TRANSPORTADORA_NAO_ENCONTRADA", "Transportadora não encontrada.")
    t["ativa"] = ativa
    return JSONResponse(status_code=200, content=dict(t))


@app.post("/admin/reset")
async def reset():
    _transportadoras.clear()
    return Response(status_code=204)
