# arquivo: servicos/transportadoras/main.py
import json
import re

from fastapi import FastAPI, Request
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse, Response
from starlette.exceptions import HTTPException as StarletteHTTPException

app = FastAPI()

UFS = {"SP", "RJ", "ES", "MG", "BA", "PR", "SC", "RS"}
ID_RE = re.compile(r"^[A-Z0-9_]{2,20}$")

_transportadoras = {}


class ErroAPI(Exception):
    def __init__(self, status, erro, mensagem):
        self.status = status
        self.erro = erro
        self.mensagem = mensagem


def _erro(status, erro, mensagem):
    return JSONResponse(status_code=status, content={"erro": erro, "mensagem": mensagem})


@app.exception_handler(ErroAPI)
async def _h_erro(request, exc):
    return _erro(exc.status, exc.erro, exc.mensagem)


@app.exception_handler(RequestValidationError)
async def _h_validacao(request, exc):
    return _erro(422, "DADOS_INVALIDOS", "Dados inválidos na requisição.")


@app.exception_handler(StarletteHTTPException)
async def _h_http(request, exc):
    if exc.status_code == 404:
        return _erro(404, "RECURSO_NAO_ENCONTRADO", "Rota não encontrada.")
    if exc.status_code == 405:
        return _erro(405, "METODO_NAO_PERMITIDO", "Método não permitido.")
    return _erro(exc.status_code, "ERRO", str(exc.detail))


def _invalido(msg="Dados inválidos na requisição."):
    raise ErroAPI(422, "DADOS_INVALIDOS", msg)


async def _ler_corpo(request):
    try:
        raw = await request.body()
        corpo = json.loads(raw)
    except Exception:
        _invalido("Corpo JSON inválido.")
    if not isinstance(corpo, dict):
        _invalido("Corpo deve ser um objeto JSON.")
    return corpo


def _eh_int(v):
    return isinstance(v, int) and not isinstance(v, bool)


def _parse_bool(valor):
    v = valor.strip().lower()
    if v in ("true", "1", "yes", "on"):
        return True
    if v in ("false", "0", "no", "off"):
        return False
    _invalido("Parâmetro somente_ativas inválido.")


@app.post("/admin/reset")
async def reset():
    _transportadoras.clear()
    return Response(status_code=204)


@app.post("/transportadoras")
async def cadastrar(request: Request):
    corpo = await _ler_corpo(request)

    tid = corpo.get("id")
    if not isinstance(tid, str) or not ID_RE.match(tid):
        _invalido("Campo id inválido.")
    nome = corpo.get("nome")
    if not isinstance(nome, str) or not nome.strip():
        _invalido("Campo nome inválido.")
    ufs = corpo.get("ufs_atendidas")
    if not isinstance(ufs, list) or len(ufs) == 0:
        _invalido("Campo ufs_atendidas inválido.")
    for uf in ufs:
        if not isinstance(uf, str) or uf not in UFS:
            _invalido("UF inválida em ufs_atendidas.")
    if len(set(ufs)) != len(ufs):
        _invalido("UF repetida em ufs_atendidas.")

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
        if not _eh_int(v) or v < minimo or (maximo is not None and v > maximo):
            _invalido(f"Campo {campo} inválido.")
        valores[campo] = v

    if tid in _transportadoras:
        raise ErroAPI(409, "TRANSPORTADORA_DUPLICADA", "Já existe transportadora com este id.")

    t = {
        "id": tid,
        "nome": nome,
        "ufs_atendidas": list(ufs),
        **valores,
        "ativa": True,
    }
    _transportadoras[tid] = t
    return JSONResponse(status_code=201, content=t)


@app.get("/transportadoras")
async def listar(request: Request):
    param = request.query_params.get("somente_ativas")
    somente_ativas = False
    if param is not None:
        somente_ativas = _parse_bool(param)
    itens = [_transportadoras[k] for k in sorted(_transportadoras)]
    if somente_ativas:
        itens = [t for t in itens if t["ativa"]]
    return JSONResponse(status_code=200, content=itens)


@app.get("/transportadoras/{tid}")
async def consultar(tid: str):
    t = _transportadoras.get(tid)
    if t is None:
        raise ErroAPI(404, "TRANSPORTADORA_NAO_ENCONTRADA", "Transportadora não encontrada.")
    return JSONResponse(status_code=200, content=t)


@app.patch("/transportadoras/{tid}")
async def alterar(tid: str, request: Request):
    corpo = await _ler_corpo(request)
    ativa = corpo.get("ativa")
    if not isinstance(ativa, bool):
        _invalido("Campo ativa inválido.")
    t = _transportadoras.get(tid)
    if t is None:
        raise ErroAPI(404, "TRANSPORTADORA_NAO_ENCONTRADA", "Transportadora não encontrada.")
    t["ativa"] = ativa
    return JSONResponse(status_code=200, content=t)
