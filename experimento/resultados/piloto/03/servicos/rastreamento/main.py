# arquivo: servicos/rastreamento/main.py
import json
import re
from datetime import date

from fastapi import FastAPI, Request
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse, Response
from starlette.exceptions import HTTPException as StarletteHTTPException

app = FastAPI()

DATA_RE = re.compile(r"^[0-9]{4}-[0-9]{2}-[0-9]{2}$")
TERMINAIS = {"ENTREGUE", "CANCELADO", "DEVOLVIDO"}

_estado = {"rastreios": {}, "processados": set()}


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


@app.post("/admin/reset")
async def reset():
    _estado["rastreios"] = {}
    _estado["processados"] = set()
    return Response(status_code=204)


@app.post("/eventos")
async def eventos(request: Request):
    corpo = await _ler_corpo(request)
    for campo in ("id", "tipo", "ocorrido_em", "dados"):
        if campo not in corpo:
            _invalido(f"Campo {campo} ausente.")
    eid = corpo["id"]
    tipo = corpo["tipo"]
    dados = corpo["dados"]
    if not isinstance(eid, str) or not isinstance(dados, dict):
        _invalido("Envelope inválido.")

    if eid in _estado["processados"]:
        return JSONResponse(status_code=200, content={"processado": False})

    if tipo == "envio.criado":
        codigo = dados.get("codigo_rastreio")
        _estado["rastreios"][codigo] = {
            "codigo_rastreio": codigo,
            "envio_id": dados.get("envio_id"),
            "pedido_id": dados.get("pedido_id"),
            "transportadora_id": dados.get("transportadora_id"),
            "data_prevista_entrega": dados.get("data_prevista_entrega"),
            "historico": [{"status": "CRIADO", "data_hora": dados.get("data_hora"), "local": None}],
        }
        _estado["processados"].add(eid)
        return JSONResponse(status_code=200, content={"processado": True})

    if tipo == "envio.status_atualizado":
        codigo = dados.get("codigo_rastreio")
        r = _estado["rastreios"].get(codigo)
        if r is None:
            return JSONResponse(status_code=200, content={"processado": False})
        r["historico"].append({
            "status": dados.get("status"),
            "data_hora": dados.get("data_hora"),
            "local": dados.get("local"),
        })
        _estado["processados"].add(eid)
        return JSONResponse(status_code=200, content={"processado": True})

    return JSONResponse(status_code=200, content={"processado": False})


@app.get("/rastreamento/{codigo}")
async def consultar(codigo: str, request: Request):
    data_txt = request.query_params.get("data")
    data = None
    if data_txt is not None:
        if not DATA_RE.match(data_txt):
            _invalido("Parâmetro data inválido.")
        try:
            data = date.fromisoformat(data_txt)
        except ValueError:
            _invalido("Parâmetro data inválido.")

    r = _estado["rastreios"].get(codigo)
    if r is None:
        raise ErroAPI(404, "RASTREIO_NAO_ENCONTRADO", "Rastreio não encontrado.")

    status = r["historico"][-1]["status"]
    atrasado = False
    if data is not None and status not in TERMINAIS:
        try:
            prevista = date.fromisoformat(r["data_prevista_entrega"])
            atrasado = data > prevista
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
