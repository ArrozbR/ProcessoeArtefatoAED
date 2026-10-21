# arquivo: servicos/transportadoras/main.py
import copy
import re

from fastapi import FastAPI, Request
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse, Response
from starlette.exceptions import HTTPException as StarletteHTTPException

app = FastAPI()

UFS_VALIDAS = {"SP", "RJ", "ES", "MG", "BA", "PR", "SC", "RS"}
RE_ID = re.compile(r"[A-Z0-9_]{2,20}")

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
        return _resposta_erro(404, "ROTA_NAO_ENCONTRADA", "Rota não encontrada.")
    if exc.status_code == 405:
        return _resposta_erro(405, "METODO_NAO_PERMITIDO", "Método não permitido.")
    return _resposta_erro(exc.status_code, "ERRO", str(exc.detail))


def _invalido(mensagem):
    return ErroAPI(422, "DADOS_INVALIDOS", mensagem)


async def _ler_corpo(request):
    try:
        corpo = await request.json()
    except Exception:
        raise _invalido("Corpo da requisição ausente ou não é JSON válido.")
    if not isinstance(corpo, dict):
        raise _invalido("O corpo da requisição deve ser um objeto JSON.")
    return corpo


def _eh_int(valor):
    return isinstance(valor, int) and not isinstance(valor, bool)


def _int_campo(corpo, nome, minimo=None, maximo=None):
    if nome not in corpo:
        raise _invalido(f"Campo '{nome}' ausente.")
    valor = corpo[nome]
    if not _eh_int(valor):
        raise _invalido(f"Campo '{nome}' deve ser inteiro.")
    if minimo is not None and valor < minimo:
        raise _invalido(f"Campo '{nome}' deve ser maior ou igual a {minimo}.")
    if maximo is not None and valor > maximo:
        raise _invalido(f"Campo '{nome}' deve ser menor ou igual a {maximo}.")
    return valor


def _validar_transportadora(corpo):
    if "id" not in corpo or not isinstance(corpo["id"], str):
        raise _invalido("Campo 'id' ausente ou inválido.")
    ident = corpo["id"]
    if not RE_ID.fullmatch(ident):
        raise _invalido("Campo 'id' deve ter de 2 a 20 caracteres A-Z, 0-9 ou _.")

    if "nome" not in corpo or not isinstance(corpo["nome"], str):
        raise _invalido("Campo 'nome' ausente ou inválido.")
    nome = corpo["nome"]
    if not nome.strip():
        raise _invalido("Campo 'nome' não pode ser vazio.")

    if "ufs_atendidas" not in corpo or not isinstance(corpo["ufs_atendidas"], list):
        raise _invalido("Campo 'ufs_atendidas' ausente ou inválido.")
    ufs = corpo["ufs_atendidas"]
    if not ufs:
        raise _invalido("Campo 'ufs_atendidas' não pode ser vazio.")
    vistas = set()
    for uf in ufs:
        if not isinstance(uf, str) or uf not in UFS_VALIDAS:
            raise _invalido("Campo 'ufs_atendidas' contém UF inválida.")
        if uf in vistas:
            raise _invalido("Campo 'ufs_atendidas' contém UF repetida.")
        vistas.add(uf)

    tarifa_base = _int_campo(corpo, "tarifa_base_centavos", minimo=0)
    tarifa_kg = _int_campo(corpo, "tarifa_kg_centavos", minimo=0)
    adicional = _int_campo(corpo, "adicional_interestadual_pct", minimo=0, maximo=100)
    prazo = _int_campo(corpo, "prazo_dias_uteis", minimo=1)
    dias_adicionais = _int_campo(corpo, "dias_adicionais_interestadual", minimo=0)
    peso_maximo = _int_campo(corpo, "peso_maximo_g", minimo=1)

    return {
        "id": ident,
        "nome": nome,
        "ufs_atendidas": list(ufs),
        "tarifa_base_centavos": tarifa_base,
        "tarifa_kg_centavos": tarifa_kg,
        "adicional_interestadual_pct": adicional,
        "prazo_dias_uteis": prazo,
        "dias_adicionais_interestadual": dias_adicionais,
        "peso_maximo_g": peso_maximo,
        "ativa": True,
    }


def _parse_bool_query(valor):
    texto = valor.strip().lower()
    if texto in ("true", "1"):
        return True
    if texto in ("false", "0"):
        return False
    raise _invalido("Parâmetro 'somente_ativas' deve ser booleano.")


@app.post("/transportadoras")
async def cadastrar_transportadora(request: Request):
    corpo = await _ler_corpo(request)
    transportadora = _validar_transportadora(corpo)
    if transportadora["id"] in _transportadoras:
        raise ErroAPI(409, "TRANSPORTADORA_DUPLICADA", "Já existe transportadora com este id.")
    _transportadoras[transportadora["id"]] = transportadora
    return JSONResponse(status_code=201, content=copy.deepcopy(transportadora))


@app.get("/transportadoras")
async def listar_transportadoras(request: Request):
    somente_ativas = False
    if "somente_ativas" in request.query_params:
        somente_ativas = _parse_bool_query(request.query_params["somente_ativas"])
    resultado = []
    for ident in sorted(_transportadoras):
        t = _transportadoras[ident]
        if somente_ativas and not t["ativa"]:
            continue
        resultado.append(copy.deepcopy(t))
    return JSONResponse(status_code=200, content=resultado)


@app.get("/transportadoras/{ident}")
async def consultar_transportadora(ident: str):
    if ident not in _transportadoras:
        raise ErroAPI(404, "TRANSPORTADORA_NAO_ENCONTRADA", "Transportadora não encontrada.")
    return JSONResponse(status_code=200, content=copy.deepcopy(_transportadoras[ident]))


@app.patch("/transportadoras/{ident}")
async def alterar_ativa(ident: str, request: Request):
    corpo = await _ler_corpo(request)
    if "ativa" not in corpo or not isinstance(corpo["ativa"], bool):
        raise _invalido("Campo 'ativa' ausente ou não booleano.")
    if ident not in _transportadoras:
        raise ErroAPI(404, "TRANSPORTADORA_NAO_ENCONTRADA", "Transportadora não encontrada.")
    _transportadoras[ident]["ativa"] = corpo["ativa"]
    return JSONResponse(status_code=200, content=copy.deepcopy(_transportadoras[ident]))


@app.post("/admin/reset")
async def reset():
    _transportadoras.clear()
    return Response(status_code=204)
