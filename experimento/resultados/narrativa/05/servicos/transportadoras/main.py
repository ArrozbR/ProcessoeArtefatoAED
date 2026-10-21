# arquivo: servicos/transportadoras/main.py
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
    return _resposta_erro(422, "DADOS_INVALIDOS", "Dados inválidos.")


@app.exception_handler(StarletteHTTPException)
async def _tratar_http(request, exc):
    codigos = {404: "NAO_ENCONTRADO", 405: "METODO_NAO_PERMITIDO"}
    return _resposta_erro(exc.status_code, codigos.get(exc.status_code, "ERRO"), str(exc.detail))


def _invalido(mensagem="Dados inválidos."):
    return ErroAPI(422, "DADOS_INVALIDOS", mensagem)


async def _ler_json(request):
    try:
        corpo = await request.json()
    except Exception:
        raise _invalido("Corpo JSON inválido.")
    if not isinstance(corpo, dict):
        raise _invalido("O corpo deve ser um objeto JSON.")
    return corpo


def _eh_inteiro(v):
    return isinstance(v, int) and not isinstance(v, bool)


def _eh_numero(v):
    return (isinstance(v, int) or isinstance(v, float)) and not isinstance(v, bool)


def _texto_nao_vazio(v):
    return isinstance(v, str) and v.strip() != ""


def _validar_transportadora(corpo):
    tid = corpo.get("id")
    if not isinstance(tid, str) or not RE_ID.fullmatch(tid):
        raise _invalido("Campo 'id' inválido.")
    if not _texto_nao_vazio(corpo.get("nome")):
        raise _invalido("Campo 'nome' inválido.")
    ufs = corpo.get("ufs_atendidas")
    if not isinstance(ufs, list) or len(ufs) == 0:
        raise _invalido("Campo 'ufs_atendidas' inválido.")
    for uf in ufs:
        if not isinstance(uf, str) or uf not in UFS_VALIDAS:
            raise _invalido("Campo 'ufs_atendidas' contém UF inválida.")
    if len(set(ufs)) != len(ufs):
        raise _invalido("Campo 'ufs_atendidas' contém UF repetida.")
    minimos = {
        "tarifa_base_centavos": 0,
        "tarifa_kg_centavos": 0,
        "prazo_dias_uteis": 1,
        "dias_adicionais_interestadual": 0,
        "peso_maximo_g": 1,
    }
    for campo, minimo in minimos.items():
        v = corpo.get(campo)
        if not _eh_inteiro(v) or v < minimo:
            raise _invalido(f"Campo '{campo}' inválido.")
    pct = corpo.get("adicional_interestadual_pct")
    if not _eh_numero(pct) or pct < 0 or pct > 100:
        raise _invalido("Campo 'adicional_interestadual_pct' inválido.")
    return {
        "id": tid,
        "nome": corpo["nome"],
        "ufs_atendidas": list(ufs),
        "tarifa_base_centavos": corpo["tarifa_base_centavos"],
        "tarifa_kg_centavos": corpo["tarifa_kg_centavos"],
        "adicional_interestadual_pct": pct,
        "prazo_dias_uteis": corpo["prazo_dias_uteis"],
        "dias_adicionais_interestadual": corpo["dias_adicionais_interestadual"],
        "peso_maximo_g": corpo["peso_maximo_g"],
        "ativa": True,
    }


@app.post("/admin/reset")
async def reset():
    _transportadoras.clear()
    return Response(status_code=204)


@app.post("/transportadoras")
async def criar_transportadora(request: Request):
    corpo = await _ler_json(request)
    transportadora = _validar_transportadora(corpo)
    if transportadora["id"] in _transportadoras:
        raise ErroAPI(409, "TRANSPORTADORA_DUPLICADA", "Já existe transportadora com esse id.")
    _transportadoras[transportadora["id"]] = transportadora
    return JSONResponse(status_code=201, content=transportadora)


@app.get("/transportadoras")
async def listar_transportadoras(somente_ativas: bool = False):
    lista = [t for _, t in sorted(_transportadoras.items(), key=lambda kv: kv[0])]
    if somente_ativas:
        lista = [t for t in lista if t["ativa"]]
    return JSONResponse(status_code=200, content=lista)


@app.get("/transportadoras/{transportadora_id}")
async def obter_transportadora(transportadora_id: str):
    t = _transportadoras.get(transportadora_id)
    if t is None:
        raise ErroAPI(404, "TRANSPORTADORA_NAO_ENCONTRADA", "Transportadora não encontrada.")
    return JSONResponse(status_code=200, content=t)


@app.patch("/transportadoras/{transportadora_id}")
async def alterar_transportadora(transportadora_id: str, request: Request):
    corpo = await _ler_json(request)
    if "ativa" not in corpo or not isinstance(corpo["ativa"], bool):
        raise _invalido("Campo 'ativa' inválido.")
    t = _transportadoras.get(transportadora_id)
    if t is None:
        raise ErroAPI(404, "TRANSPORTADORA_NAO_ENCONTRADA", "Transportadora não encontrada.")
    t["ativa"] = corpo["ativa"]
    return JSONResponse(status_code=200, content=t)
