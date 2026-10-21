# arquivo: servicos/rastreamento/main.py
import re
from datetime import date
from typing import Optional

from fastapi import FastAPI, Request
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse, Response
from starlette.exceptions import HTTPException as StarletteHTTPException

app = FastAPI()

RE_DATA = re.compile(r"[0-9]{4}-[0-9]{2}-[0-9]{2}")
STATUS_FINAIS = {"ENTREGUE", "CANCELADO", "DEVOLVIDO"}

_estado = {"rastreamentos": {}, "processados": set()}


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


def _parse_data(v):
    if not isinstance(v, str) or not RE_DATA.fullmatch(v):
        return None
    try:
        return date.fromisoformat(v)
    except ValueError:
        return None


@app.post("/admin/reset")
async def reset():
    _estado["rastreamentos"].clear()
    _estado["processados"].clear()
    return Response(status_code=204)


@app.post("/eventos")
async def receber_evento(request: Request):
    corpo = await _ler_json(request)
    for campo in ("id", "tipo", "ocorrido_em", "dados"):
        if campo not in corpo or corpo[campo] is None:
            raise _invalido(f"Campo '{campo}' ausente.")
    if not isinstance(corpo["dados"], dict):
        raise _invalido("Campo 'dados' inválido.")

    evento_id = corpo["id"]
    try:
        hash(evento_id)
    except TypeError:
        raise _invalido("Campo 'id' inválido.")
    tipo = corpo["tipo"]
    dados = corpo["dados"]

    if evento_id in _estado["processados"]:
        return JSONResponse(status_code=200, content={"processado": False})

    if tipo == "envio.criado":
        codigo = dados.get("codigo_rastreio")
        if not isinstance(codigo, str):
            return JSONResponse(status_code=200, content={"processado": False})
        _estado["rastreamentos"][codigo] = {
            "codigo_rastreio": codigo,
            "envio_id": dados.get("envio_id"),
            "pedido_id": dados.get("pedido_id"),
            "transportadora_id": dados.get("transportadora_id"),
            "data_prevista_entrega": dados.get("data_prevista_entrega"),
            "historico": [{"status": "CRIADO", "data_hora": dados.get("data_hora"), "local": None}],
        }
        _estado["processados"].add(evento_id)
        return JSONResponse(status_code=200, content={"processado": True})

    if tipo == "envio.status_atualizado":
        codigo = dados.get("codigo_rastreio")
        rastreamento = _estado["rastreamentos"].get(codigo) if isinstance(codigo, str) else None
        if rastreamento is None:
            return JSONResponse(status_code=200, content={"processado": False})
        rastreamento["historico"].append({
            "status": dados.get("status"),
            "data_hora": dados.get("data_hora"),
            "local": dados.get("local"),
        })
        _estado["processados"].add(evento_id)
        return JSONResponse(status_code=200, content={"processado": True})

    return JSONResponse(status_code=200, content={"processado": False})


@app.get("/rastreamento/{codigo_rastreio}")
async def obter_rastreamento(codigo_rastreio: str, data: Optional[str] = None):
    data_consulta = None
    if data is not None:
        data_consulta = _parse_data(data)
        if data_consulta is None:
            raise _invalido("Parâmetro 'data' inválido.")
    r = _estado["rastreamentos"].get(codigo_rastreio)
    if r is None:
        raise ErroAPI(404, "RASTREIO_NAO_ENCONTRADO", "Código de rastreio não encontrado.")
    status = r["historico"][-1]["status"]
    atrasado = False
    if data_consulta is not None and status not in STATUS_FINAIS:
        prevista = _parse_data(r["data_prevista_entrega"])
        if prevista is not None and data_consulta > prevista:
            atrasado = True
    return JSONResponse(status_code=200, content={
        "codigo_rastreio": r["codigo_rastreio"],
        "envio_id": r["envio_id"],
        "pedido_id": r["pedido_id"],
        "transportadora_id": r["transportadora_id"],
        "data_prevista_entrega": r["data_prevista_entrega"],
        "status": status,
        "entregue": status == "ENTREGUE",
        "atrasado": atrasado,
        "historico": r["historico"],
    })
