# arquivo: servicos/transportadoras/main.py
import json
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
    def __init__(self, status, erro, mensagem):
        self.status = status
        self.erro = erro
        self.mensagem = mensagem


def _erro(status, erro, mensagem):
    return JSONResponse(status_code=status, content={"erro": erro, "mensagem": mensagem})


@app.exception_handler(ErroAPI)
async def _tratar_erro_api(request, exc):
    return _erro(exc.status, exc.erro, exc.mensagem)


@app.exception_handler(RequestValidationError)
async def _tratar_validacao(request, exc):
    return _erro(422, "DADOS_INVALIDOS", "Dados inválidos na requisição.")


@app.exception_handler(StarletteHTTPException)
async def _tratar_http(request, exc):
    if exc.status_code == 404:
        return _erro(404, "RECURSO_NAO_ENCONTRADO", "Recurso não encontrado.")
    if exc.status_code == 422:
        return _erro(422, "DADOS_INVALIDOS", "Dados inválidos na requisição.")
    return _erro(exc.status_code, "ERRO", str(exc.detail))


def _invalido(mensagem):
    raise ErroAPI(422, "DADOS_INVALIDOS", mensagem)


async def _ler_corpo(request):
    try:
        bruto = await request.body()
        corpo = json.loads(bruto)
    except Exception:
        _invalido("Corpo JSON inválido.")
    if not isinstance(corpo, dict):
        _invalido("O corpo deve ser um objeto JSON.")
    return corpo


def _eh_int(valor):
    return isinstance(valor, int) and not isinstance(valor, bool)


def _int_campo(corpo, nome, minimo=None, maximo=None):
    if nome not in corpo:
        _invalido(f"Campo '{nome}' ausente.")
    valor = corpo[nome]
    if not _eh_int(valor):
        _invalido(f"Campo '{nome}' deve ser inteiro.")
    if minimo is not None and valor < minimo:
        _invalido(f"Campo '{nome}' abaixo do mínimo.")
    if maximo is not None and valor > maximo:
        _invalido(f"Campo '{nome}' acima do máximo.")
    return valor


def _parse_bool(texto):
    t = texto.strip().lower()
    if t in ("true", "1", "yes", "on", "t", "y"):
        return True
    if t in ("false", "0", "no", "off", "f", "n"):
        return False
    _invalido("Parâmetro booleano inválido.")


@app.post("/admin/reset")
def reset():
    _transportadoras.clear()
    return Response(status_code=204)


@app.post("/transportadoras")
async def cadastrar(request: Request):
    corpo = await _ler_corpo(request)

    if "id" not in corpo:
        _invalido("Campo 'id' ausente.")
    tid = corpo["id"]
    if not isinstance(tid, str) or not RE_ID.fullmatch(tid):
        _invalido("Campo 'id' inválido.")

    if "nome" not in corpo:
        _invalido("Campo 'nome' ausente.")
    nome = corpo["nome"]
    if not isinstance(nome, str) or not nome.strip():
        _invalido("Campo 'nome' inválido.")

    if "ufs_atendidas" not in corpo:
        _invalido("Campo 'ufs_atendidas' ausente.")
    ufs = corpo["ufs_atendidas"]
    if not isinstance(ufs, list) or len(ufs) == 0:
        _invalido("Campo 'ufs_atendidas' inválido.")
    for uf in ufs:
        if not isinstance(uf, str) or uf not in UFS_VALIDAS:
            _invalido("UF inválida em 'ufs_atendidas'.")
    if len(set(ufs)) != len(ufs):
        _invalido("UF repetida em 'ufs_atendidas'.")

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
def listar(request: Request):
    parametro = request.query_params.get("somente_ativas")
    somente_ativas = False
    if parametro is not None:
        somente_ativas = _parse_bool(parametro)
    itens = [_transportadoras[k] for k in sorted(_transportadoras.keys())]
    if somente_ativas:
        itens = [t for t in itens if t["ativa"]]
    return JSONResponse(status_code=200, content=itens)


@app.get("/transportadoras/{tid}")
def consultar(tid: str):
    t = _transportadoras.get(tid)
    if t is None:
        raise ErroAPI(404, "TRANSPORTADORA_NAO_ENCONTRADA", "Transportadora não encontrada.")
    return JSONResponse(status_code=200, content=t)


@app.patch("/transportadoras/{tid}")
async def alterar_ativa(tid: str, request: Request):
    corpo = await _ler_corpo(request)
    if "ativa" not in corpo or not isinstance(corpo["ativa"], bool):
        _invalido("Campo 'ativa' ausente ou inválido.")
    t = _transportadoras.get(tid)
    if t is None:
        raise ErroAPI(404, "TRANSPORTADORA_NAO_ENCONTRADA", "Transportadora não encontrada.")
    t["ativa"] = corpo["ativa"]
    return JSONResponse(status_code=200, content=t)
