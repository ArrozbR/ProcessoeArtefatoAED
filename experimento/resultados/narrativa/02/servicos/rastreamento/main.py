# arquivo: servicos/rastreamento/main.py
import copy
import re
import threading
from datetime import date
from typing import Optional

from fastapi import FastAPI, Query
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse, Response
from pydantic import BaseModel, StrictStr
from starlette.exceptions import HTTPException as StarletteHTTPException


class ErroAPI(Exception):
    def __init__(self, status: int, codigo: str, mensagem: str):
        super().__init__(mensagem)
        self.status = status
        self.codigo = codigo
        self.mensagem = mensagem


def _resposta_erro(status: int, codigo: str, mensagem: str) -> JSONResponse:
    return JSONResponse(status_code=status, content={"erro": codigo, "mensagem": mensagem})


app = FastAPI()


@app.exception_handler(ErroAPI)
async def _tratar_erro_api(request, exc: ErroAPI):
    return _resposta_erro(exc.status, exc.codigo, exc.mensagem)


@app.exception_handler(RequestValidationError)
async def _tratar_validacao(request, exc: RequestValidationError):
    return _resposta_erro(422, "DADOS_INVALIDOS", "Dados inválidos na requisição.")


@app.exception_handler(StarletteHTTPException)
async def _tratar_http(request, exc: StarletteHTTPException):
    codigos = {404: "NAO_ENCONTRADO", 405: "METODO_NAO_PERMITIDO"}
    return _resposta_erro(exc.status_code, codigos.get(exc.status_code, "ERRO"), str(exc.detail))


STATUS_FINAIS = {"ENTREGUE", "CANCELADO", "DEVOLVIDO"}

_trava = threading.RLock()
_estado = {"rastreamentos": {}, "eventos_processados": set()}


class Evento(BaseModel):
    id: StrictStr
    tipo: StrictStr
    ocorrido_em: StrictStr
    dados: dict


def _converter_data(texto: str) -> date:
    if not re.fullmatch(r"[0-9]{4}-[0-9]{2}-[0-9]{2}", texto):
        raise ValueError("data mal formada")
    return date.fromisoformat(texto)


@app.post("/admin/reset")
def reset():
    with _trava:
        _estado["rastreamentos"] = {}
        _estado["eventos_processados"] = set()
    return Response(status_code=204)


@app.post("/eventos")
def receber_evento(evento: Evento):
    with _trava:
        if evento.id in _estado["eventos_processados"]:
            return JSONResponse(status_code=200, content={"processado": False})
        dados = evento.dados
        if evento.tipo == "envio.criado":
            codigo = dados.get("codigo_rastreio")
            if not isinstance(codigo, str):
                return JSONResponse(status_code=200, content={"processado": False})
            _estado["rastreamentos"][codigo] = {
                "codigo_rastreio": codigo,
                "envio_id": dados.get("envio_id"),
                "pedido_id": dados.get("pedido_id"),
                "transportadora_id": dados.get("transportadora_id"),
                "data_prevista_entrega": dados.get("data_prevista_entrega"),
                "historico": [{
                    "status": "CRIADO",
                    "data_hora": dados.get("data_hora", evento.ocorrido_em),
                    "local": None,
                }],
            }
        elif evento.tipo == "envio.status_atualizado":
            codigo = dados.get("codigo_rastreio")
            rastreamento = _estado["rastreamentos"].get(codigo) if isinstance(codigo, str) else None
            if rastreamento is None:
                return JSONResponse(status_code=200, content={"processado": False})
            rastreamento["historico"].append({
                "status": dados.get("status"),
                "data_hora": dados.get("data_hora", evento.ocorrido_em),
                "local": dados.get("local"),
            })
        else:
            return JSONResponse(status_code=200, content={"processado": False})
        _estado["eventos_processados"].add(evento.id)
        return JSONResponse(status_code=200, content={"processado": True})


@app.get("/rastreamento/{codigo_rastreio}")
def obter_rastreamento(codigo_rastreio: str, data: Optional[str] = Query(None)):
    data_consulta = None
    if data is not None:
        try:
            data_consulta = _converter_data(data)
        except Exception:
            raise ErroAPI(422, "DADOS_INVALIDOS", "Data mal formada.")
    with _trava:
        r = _estado["rastreamentos"].get(codigo_rastreio)
        if r is None:
            raise ErroAPI(404, "RASTREIO_NAO_ENCONTRADO", "Código de rastreio não encontrado.")
        r = copy.deepcopy(r)
    status = r["historico"][-1]["status"]
    atrasado = False
    if data_consulta is not None and status not in STATUS_FINAIS:
        try:
            prevista = date.fromisoformat(str(r["data_prevista_entrega"]))
            atrasado = data_consulta > prevista
        except Exception:
            atrasado = False
    resposta = {
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
    return JSONResponse(status_code=200, content=resposta)
