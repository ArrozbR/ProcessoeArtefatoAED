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


def erro(status, codigo, mensagem):
    return JSONResponse(status_code=status, content={"erro": codigo, "mensagem": mensagem})


def invalido(mensagem="Dados inválidos."):
    return erro(422, "DADOS_INVALIDOS", mensagem)


@app.exception_handler(RequestValidationError)
async def _handler_validacao(request, exc):
    return invalido("Requisição com dados inválidos.")


@app.exception_handler(StarletteHTTPException)
async def _handler_http(request, exc):
    if exc.status_code == 404:
        return erro(404, "RECURSO_NAO_ENCONTRADO", "Recurso não encontrado.")
    if exc.status_code == 405:
        return erro(405, "METODO_NAO_PERMITIDO", "Método não permitido.")
    return erro(exc.status_code, "ERRO", str(exc.detail))


async def ler_corpo(request):
    try:
        raw = await request.body()
        dados = json.loads(raw)
    except Exception:
        return None
    if not isinstance(dados, dict):
        return None
    return dados


def eh_int(v, minimo=None, maximo=None):
    if not isinstance(v, int) or isinstance(v, bool):
        return False
    if minimo is not None and v < minimo:
        return False
    if maximo is not None and v > maximo:
        return False
    return True


def eh_texto_nao_vazio(v):
    return isinstance(v, str) and v.strip() != ""


def parse_bool(valor):
    v = valor.strip().lower()
    if v in ("true", "1", "yes", "on", "t", "y"):
        return True
    if v in ("false", "0", "no", "off", "f", "n"):
        return False
    return None


CAMPOS_INT = {
    "tarifa_base_centavos": (0, None),
    "tarifa_kg_centavos": (0, None),
    "adicional_interestadual_pct": (0, 100),
    "prazo_dias_uteis": (1, None),
    "dias_adicionais_interestadual": (0, None),
    "peso_maximo_g": (1, None),
}


def validar_transportadora(d):
    tid = d.get("id")
    if not isinstance(tid, str) or not ID_RE.match(tid):
        return False
    if not eh_texto_nao_vazio(d.get("nome")):
        return False
    ufs = d.get("ufs_atendidas")
    if not isinstance(ufs, list) or len(ufs) == 0:
        return False
    vistos = set()
    for uf in ufs:
        if not isinstance(uf, str) or uf not in UFS or uf in vistos:
            return False
        vistos.add(uf)
    for campo, (mi, ma) in CAMPOS_INT.items():
        if campo not in d or not eh_int(d[campo], mi, ma):
            return False
    return True


def copia(t):
    c = dict(t)
    c["ufs_atendidas"] = list(t["ufs_atendidas"])
    return c


@app.post("/transportadoras")
async def cadastrar(request: Request):
    d = await ler_corpo(request)
    if d is None or not validar_transportadora(d):
        return invalido("Dados da transportadora inválidos.")
    if d["id"] in _transportadoras:
        return erro(409, "TRANSPORTADORA_DUPLICADA", "Já existe transportadora com este id.")
    t = {
        "id": d["id"],
        "nome": d["nome"],
        "ufs_atendidas": list(d["ufs_atendidas"]),
        "tarifa_base_centavos": d["tarifa_base_centavos"],
        "tarifa_kg_centavos": d["tarifa_kg_centavos"],
        "adicional_interestadual_pct": d["adicional_interestadual_pct"],
        "prazo_dias_uteis": d["prazo_dias_uteis"],
        "dias_adicionais_interestadual": d["dias_adicionais_interestadual"],
        "peso_maximo_g": d["peso_maximo_g"],
        "ativa": True,
    }
    _transportadoras[t["id"]] = t
    return JSONResponse(status_code=201, content=copia(t))


@app.get("/transportadoras")
async def listar(request: Request):
    somente_ativas = False
    if "somente_ativas" in request.query_params:
        valor = parse_bool(request.query_params["somente_ativas"])
        if valor is None:
            return invalido("Parâmetro somente_ativas inválido.")
        somente_ativas = valor
    lista = [
        copia(_transportadoras[k])
        for k in sorted(_transportadoras)
        if not somente_ativas or _transportadoras[k]["ativa"]
    ]
    return JSONResponse(status_code=200, content=lista)


@app.get("/transportadoras/{tid}")
async def consultar(tid: str):
    t = _transportadoras.get(tid)
    if t is None:
        return erro(404, "TRANSPORTADORA_NAO_ENCONTRADA", "Transportadora não encontrada.")
    return JSONResponse(status_code=200, content=copia(t))


@app.patch("/transportadoras/{tid}")
async def alterar_ativa(tid: str, request: Request):
    d = await ler_corpo(request)
    if d is None or "ativa" not in d or not isinstance(d["ativa"], bool):
        return invalido("Campo ativa ausente ou inválido.")
    t = _transportadoras.get(tid)
    if t is None:
        return erro(404, "TRANSPORTADORA_NAO_ENCONTRADA", "Transportadora não encontrada.")
    t["ativa"] = d["ativa"]
    return JSONResponse(status_code=200, content=copia(t))


@app.post("/admin/reset")
async def reset():
    _transportadoras.clear()
    return Response(status_code=204)
