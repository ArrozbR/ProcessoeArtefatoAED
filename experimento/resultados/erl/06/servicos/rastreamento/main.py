# arquivo: servicos/rastreamento/main.py
import copy
import re
from datetime import date
from typing import Any, Dict, Optional

from fastapi import FastAPI, Request
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse, Response
from starlette.exceptions import HTTPException as StarletteHTTPException

app = FastAPI()

RE_DATA = re.compile(r"[0-9]{4}-[0-9]{2}-[0-9]{2}")
TERMINAIS = {"ENTREGUE", "CANCELADO", "DEVOLVIDO"}

_estado: Dict[str, Any] = {"processados": set(), "rastreamentos": {}}


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


def _parse_data(s: Any) -> Optional[date]:
    if not isinstance(s, str) or not RE_DATA.fullmatch(s):
        return None
    try:
        return date(int(s[0:4]), int(s[5:7]), int(s[8:10]))
    except ValueError:
        return None


def _exigir(dados: Dict[str, Any], campos) -> None:
    for c in campos:
        if c not in dados:
            raise _invalido(f"Campo 'dados.{c}' ausente.")


@app.post("/eventos")
async def consumir_evento(request: Request):
    corpo = await _ler_corpo(request)
    if not isinstance(corpo, dict):
        raise _invalido("O envelope deve ser um objeto JSON.")
    for campo in ("id", "tipo", "ocorrido_em", "dados"):
        if campo not in corpo:
            raise _invalido(f"Campo '{campo}' ausente no envelope.")

    evento_id = corpo["id"]
    tipo = corpo["tipo"]
    dados = corpo["dados"]
    chave = str(evento_id)

    if chave in _estado["processados"]:
        return {"processado": False}

    if tipo == "envio.criado":
        if not isinstance(dados, dict):
            raise _invalido("Campo 'dados' inválido.")
        _exigir(dados, ("envio_id", "codigo_rastreio", "pedido_id", "transportadora_id",
                        "data_prevista_entrega", "data_hora"))
        _estado["rastreamentos"][dados["codigo_rastreio"]] = {
            "codigo_rastreio": dados["codigo_rastreio"],
            "envio_id": dados["envio_id"],
            "pedido_id": dados["pedido_id"],
            "transportadora_id": dados["transportadora_id"],
            "data_prevista_entrega": dados["data_prevista_entrega"],
            "historico": [{"status": "CRIADO", "data_hora": dados["data_hora"], "local": None}],
        }
        _estado["processados"].add(chave)
        return {"processado": True}

    if tipo == "envio.status_atualizado":
        if not isinstance(dados, dict):
            raise _invalido("Campo 'dados' inválido.")
        _exigir(dados, ("codigo_rastreio", "status", "data_hora"))
        r = _estado["rastreamentos"].get(dados["codigo_rastreio"])
        if r is None:
            return {"processado": False}
        r["historico"].append(
            {"status": dados["status"], "data_hora": dados["data_hora"], "local": dados.get("local")}
        )
        _estado["processados"].add(chave)
        return {"processado": True}

    return {"processado": False}


@app.get("/rastreamento/{codigo}")
async def consultar_rastreamento(codigo: str, data: Optional[str] = None):
    d = None
    if data is not None:
        d = _parse_data(data)
        if d is None:
            raise _invalido("Parâmetro 'data' inválido.")

    r = _estado["rastreamentos"].get(codigo)
    if r is None:
        raise ErroAPI(404, "RASTREIO_NAO_ENCONTRADO", "Rastreamento não encontrado.")

    status = r["historico"][-1]["status"]
    atrasado = False
    if d is not None and status not in TERMINAIS:
        prevista = _parse_data(r["data_prevista_entrega"])
        if prevista is not None and d > prevista:
            atrasado = True

    return {
        "codigo_rastreio": r["codigo_rastreio"],
        "envio_id": r["envio_id"],
        "pedido_id": r["pedido_id"],
        "transportadora_id": r["transportadora_id"],
        "data_prevista_entrega": r["data_prevista_entrega"],
        "status": status,
        "entregue": status == "ENTREGUE",
        "atrasado": atrasado,
        "historico": copy.deepcopy(r["historico"]),
    }


@app.post("/admin/reset")
async def reset():
    _estado["processados"] = set()
    _estado["rastreamentos"] = {}
    return Response(status_code=204)
