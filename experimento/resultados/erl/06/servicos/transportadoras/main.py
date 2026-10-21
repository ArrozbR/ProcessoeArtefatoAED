# arquivo: servicos/transportadoras/main.py
import re
from typing import Any, Dict, Optional

from fastapi import FastAPI, Request
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse, Response
from starlette.exceptions import HTTPException as StarletteHTTPException

app = FastAPI()

UFS_VALIDAS = {"SP", "RJ", "ES", "MG", "BA", "PR", "SC", "RS"}
RE_ID = re.compile(r"[A-Z0-9_]{2,20}")

_transportadoras: Dict[str, Dict[str, Any]] = {}


class ErroAPI(Exception):
    def __init__(self, status: int, erro: str, mensagem: str):
        super().__init__(mensagem)
        self.status = status
        self.erro = erro
        self.mensagem = mensagem


@app.exception_handler(ErroAPI)
async def _tratar_erro_api(request: Request, exc: ErroAPI):
    return JSONResponse(status_code=exc.status, content={"erro": exc.erro, "mensagem": exc.mensagem})


@app.exception_handler(RequestValidationError)
async def _tratar_validacao(request: Request, exc: RequestValidationError):
    return JSONResponse(
        status_code=422,
        content={"erro": "DADOS_INVALIDOS", "mensagem": "Dados inválidos na requisição."},
    )


@app.exception_handler(StarletteHTTPException)
async def _tratar_http(request: Request, exc: StarletteHTTPException):
    if exc.status_code == 404:
        erro = "NAO_ENCONTRADO"
    elif exc.status_code == 405:
        erro = "METODO_NAO_PERMITIDO"
    else:
        erro = "ERRO"
    return JSONResponse(status_code=exc.status_code, content={"erro": erro, "mensagem": str(exc.detail)})


def _invalido(mensagem: str) -> ErroAPI:
    return ErroAPI(422, "DADOS_INVALIDOS", mensagem)


async def _ler_corpo(request: Request) -> Any:
    try:
        return await request.json()
    except Exception:
        raise _invalido("Corpo da requisição não é um JSON válido.")


def _eh_int(v: Any) -> bool:
    return isinstance(v, int) and not isinstance(v, bool)


def _validar_transportadora(corpo: Any) -> Dict[str, Any]:
    if not isinstance(corpo, dict):
        raise _invalido("O corpo deve ser um objeto JSON.")

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
        if not isinstance(uf, str) or uf not in UFS_VALIDAS:
            raise _invalido("Campo 'ufs_atendidas' contém UF inválida.")
    if len(set(ufs)) != len(ufs):
        raise _invalido("Campo 'ufs_atendidas' contém UF repetida.")

    limites = [
        ("tarifa_base_centavos", 0, None),
        ("tarifa_kg_centavos", 0, None),
        ("adicional_interestadual_pct", 0, 100),
        ("prazo_dias_uteis", 1, None),
        ("dias_adicionais_interestadual", 0, None),
        ("peso_maximo_g", 1, None),
    ]
    valores = {}
    for campo, minimo, maximo in limites:
        v = corpo.get(campo)
        if not _eh_int(v) or v < minimo or (maximo is not None and v > maximo):
            raise _invalido(f"Campo '{campo}' inválido.")
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


def _copia(t: Dict[str, Any]) -> Dict[str, Any]:
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


@app.get("/transportadoras")
async def listar_transportadoras(somente_ativas: bool = False):
    itens = [_transportadoras[k] for k in sorted(_transportadoras)]
    if somente_ativas:
        itens = [t for t in itens if t["ativa"]]
    return [_copia(t) for t in itens]


@app.get("/transportadoras/{tid}")
async def consultar_transportadora(tid: str):
    t = _transportadoras.get(tid)
    if t is None:
        raise ErroAPI(404, "TRANSPORTADORA_NAO_ENCONTRADA", "Transportadora não encontrada.")
    return _copia(t)


@app.patch("/transportadoras/{tid}")
async def alterar_ativa(tid: str, request: Request):
    corpo = await _ler_corpo(request)
    if not isinstance(corpo, dict) or not isinstance(corpo.get("ativa"), bool):
        raise _invalido("Campo 'ativa' ausente ou inválido.")
    t = _transportadoras.get(tid)
    if t is None:
        raise ErroAPI(404, "TRANSPORTADORA_NAO_ENCONTRADA", "Transportadora não encontrada.")
    t["ativa"] = corpo["ativa"]
    return _copia(t)


@app.post("/admin/reset")
async def reset():
    _transportadoras.clear()
    return Response(status_code=204)
