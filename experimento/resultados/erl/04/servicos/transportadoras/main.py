# arquivo: servicos/transportadoras/main.py
import re
from typing import Any, Optional

from fastapi import Body, FastAPI, Request
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse, Response
from starlette.exceptions import HTTPException as StarletteHTTPException

app = FastAPI()

UFS_VALIDAS = {"SP", "RJ", "ES", "MG", "BA", "PR", "SC", "RS"}
RE_ID = re.compile(r"^[A-Z0-9_]{2,20}$")

_transportadoras: dict = {}


def erro(status: int, codigo: str, mensagem: str) -> JSONResponse:
    return JSONResponse(status_code=status, content={"erro": codigo, "mensagem": mensagem})


@app.exception_handler(RequestValidationError)
async def _validacao(request: Request, exc: RequestValidationError):
    return erro(422, "DADOS_INVALIDOS", "Dados inválidos na requisição.")


@app.exception_handler(StarletteHTTPException)
async def _http(request: Request, exc: StarletteHTTPException):
    if exc.status_code == 404:
        return erro(404, "RECURSO_NAO_ENCONTRADO", "Rota não encontrada.")
    if exc.status_code == 405:
        return erro(405, "METODO_NAO_PERMITIDO", "Método não permitido.")
    return erro(exc.status_code, "ERRO", str(exc.detail))


def _eh_int(v: Any) -> bool:
    return isinstance(v, int) and not isinstance(v, bool)


def _validar_transportadora(corpo: Any) -> Optional[dict]:
    if not isinstance(corpo, dict):
        return None
    tid = corpo.get("id")
    if not isinstance(tid, str) or not RE_ID.match(tid):
        return None
    nome = corpo.get("nome")
    if not isinstance(nome, str) or not nome.strip():
        return None
    ufs = corpo.get("ufs_atendidas")
    if not isinstance(ufs, list) or len(ufs) == 0:
        return None
    vistos = set()
    for uf in ufs:
        if not isinstance(uf, str) or uf not in UFS_VALIDAS or uf in vistos:
            return None
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
        if not _eh_int(v):
            return None
        if v < minimo:
            return None
        if maximo is not None and v > maximo:
            return None
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
def cadastrar(corpo: Any = Body(default=None)):
    t = _validar_transportadora(corpo)
    if t is None:
        return erro(422, "DADOS_INVALIDOS", "Dados da transportadora inválidos.")
    if t["id"] in _transportadoras:
        return erro(409, "TRANSPORTADORA_DUPLICADA", "Já existe transportadora com este id.")
    _transportadoras[t["id"]] = t
    return JSONResponse(status_code=201, content=_copia(t))


def _parse_bool(valor: Optional[str]) -> Optional[bool]:
    if valor is None:
        return False
    v = valor.strip().lower()
    if v in ("true", "1", "yes", "on"):
        return True
    if v in ("false", "0", "no", "off"):
        return False
    return None


@app.get("/transportadoras")
def listar(somente_ativas: Optional[str] = None):
    filtro = _parse_bool(somente_ativas)
    if filtro is None:
        return erro(422, "DADOS_INVALIDOS", "Parâmetro somente_ativas inválido.")
    itens = [
        _copia(t)
        for _, t in sorted(_transportadoras.items(), key=lambda kv: kv[0])
        if (not filtro) or t["ativa"]
    ]
    return JSONResponse(status_code=200, content=itens)


@app.get("/transportadoras/{tid}")
def consultar(tid: str):
    t = _transportadoras.get(tid)
    if t is None:
        return erro(404, "TRANSPORTADORA_NAO_ENCONTRADA", "Transportadora não encontrada.")
    return JSONResponse(status_code=200, content=_copia(t))


@app.patch("/transportadoras/{tid}")
def alterar_ativa(tid: str, corpo: Any = Body(default=None)):
    if not isinstance(corpo, dict) or not isinstance(corpo.get("ativa"), bool):
        return erro(422, "DADOS_INVALIDOS", "Campo ativa ausente ou inválido.")
    t = _transportadoras.get(tid)
    if t is None:
        return erro(404, "TRANSPORTADORA_NAO_ENCONTRADA", "Transportadora não encontrada.")
    t["ativa"] = corpo["ativa"]
    return JSONResponse(status_code=200, content=_copia(t))


@app.post("/admin/reset")
def reset():
    _transportadoras.clear()
    return Response(status_code=204)
