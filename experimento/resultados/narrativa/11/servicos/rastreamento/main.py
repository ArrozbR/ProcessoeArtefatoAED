# arquivo: servicos/rastreamento/main.py
import copy
import re
import threading
from datetime import date
from typing import Annotated, Optional

from fastapi import FastAPI, Query, Request
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse, Response
from pydantic import BaseModel, Field
from starlette.exceptions import HTTPException as StarletteHTTPException


class ErroAPI(Exception):
    def __init__(self, status: int, codigo: str, mensagem: str):
        super().__init__(mensagem)
        self.status = status
        self.codigo = codigo
        self.mensagem = mensagem


def _erro(status: int, codigo: str, mensagem: str) -> JSONResponse:
    return JSONResponse(status_code=status, content={"erro": codigo, "mensagem": mensagem})


app = FastAPI(title="Rastreamento")


@app.exception_handler(ErroAPI)
async def _tratar_erro_api(request: Request, exc: ErroAPI):
    return _erro(exc.status, exc.codigo, exc.mensagem)


@app.exception_handler(RequestValidationError)
async def _tratar_validacao(request: Request, exc: RequestValidationError):
    partes = []
    for e in exc.errors():
        loc = ".".join(str(x) for x in e.get("loc", ()))
        partes.append(f"{loc}: {e.get('msg', '')}")
    return _erro(422, "DADOS_INVALIDOS", "; ".join(partes) or "Dados invalidos.")


@app.exception_handler(StarletteHTTPException)
async def _tratar_http(request: Request, exc: StarletteHTTPException):
    if exc.status_code == 404:
        return _erro(404, "NAO_ENCONTRADO", "Recurso nao encontrado.")
    if exc.status_code == 405:
        return _erro(405, "METODO_NAO_PERMITIDO", "Metodo nao permitido.")
    return _erro(exc.status_code, "ERRO", str(exc.detail))


_RE_DATA = re.compile(r"^[0-9]{4}-[0-9]{2}-[0-9]{2}$")
FINAIS = {"ENTREGUE", "CANCELADO", "DEVOLVIDO"}

_trava = threading.RLock()
_rastreamentos: dict = {}
_eventos_processados: set = set()


class Evento(BaseModel):
    id: Annotated[str, Field(strict=True)]
    tipo: Annotated[str, Field(strict=True)]
    ocorrido_em: Annotated[str, Field(strict=True)]
    dados: dict


def _exigir(dados: dict, chaves) -> None:
    faltando = [c for c in chaves if c not in dados]
    if faltando:
        raise ErroAPI(422, "DADOS_INVALIDOS", "Campos ausentes em dados: " + ", ".join(faltando))


@app.post("/admin/reset", status_code=204)
def reset():
    with _trava:
        _rastreamentos.clear()
        _eventos_processados.clear()
    return Response(status_code=204)


@app.post("/eventos")
def receber_evento(evento: Evento):
    with _trava:
        if evento.id in _eventos_processados:
            return {"processado": False}
        dados = evento.dados
        if evento.tipo == "envio.criado":
            _exigir(dados, ["envio_id", "codigo_rastreio", "pedido_id", "transportadora_id",
                            "data_prevista_entrega", "data_hora"])
            _rastreamentos[dados["codigo_rastreio"]] = {
                "codigo_rastreio": dados["codigo_rastreio"],
                "envio_id": dados["envio_id"],
                "pedido_id": dados["pedido_id"],
                "transportadora_id": dados["transportadora_id"],
                "data_prevista_entrega": dados["data_prevista_entrega"],
                "historico": [{"status": "CRIADO", "data_hora": dados["data_hora"], "local": None}],
            }
            _eventos_processados.add(evento.id)
            return {"processado": True}
        if evento.tipo == "envio.status_atualizado":
            _exigir(dados, ["codigo_rastreio", "status", "data_hora"])
            r = _rastreamentos.get(dados["codigo_rastreio"])
            if r is None:
                return {"processado": False}
            r["historico"].append({
                "status": dados["status"],
                "data_hora": dados["data_hora"],
                "local": dados.get("local"),
            })
            _eventos_processados.add(evento.id)
            return {"processado": True}
        return {"processado": False}


@app.get("/rastreamento/{codigo_rastreio}")
def obter_rastreamento(codigo_rastreio: str, data: Optional[str] = Query(default=None)):
    data_consulta = None
    if data is not None:
        if not _RE_DATA.match(data):
            raise ErroAPI(422, "DADOS_INVALIDOS", "data deve estar no formato AAAA-MM-DD.")
        try:
            data_consulta = date.fromisoformat(data)
        except ValueError:
            raise ErroAPI(422, "DADOS_INVALIDOS", "data invalida.")
    with _trava:
        r = _rastreamentos.get(codigo_rastreio)
        if r is None:
            raise ErroAPI(404, "RASTREIO_NAO_ENCONTRADO", f"Rastreio {codigo_rastreio} nao encontrado.")
        r = copy.deepcopy(r)
    status = r["historico"][-1]["status"]
    atrasado = False
    if data_consulta is not None and status not in FINAIS:
        try:
            prevista = date.fromisoformat(r["data_prevista_entrega"])
            atrasado = data_consulta > prevista
        except (TypeError, ValueError):
            atrasado = False
    return {
        "codigo_rastreio": r["codigo_rastreio"],
        "envio_id": r["envio_id"],
        "pedido_id": r["pedido_id"],
        "transportadora_id": r["transportadora_id"],
        "data_prevista_entrega": r["data_prevista_entrega"],
        "status": status,
        "entregue": status == "ENTREGUE",
        "atrasado": atrasado,
        "historico": r["historico"],
    }
