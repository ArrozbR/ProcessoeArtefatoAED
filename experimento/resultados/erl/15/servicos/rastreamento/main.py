# arquivo: servicos/rastreamento/main.py
import re
from datetime import date

from fastapi import FastAPI, Request
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse, Response
from starlette.exceptions import HTTPException as StarletteHTTPException

app = FastAPI()

RE_DATA = re.compile(r"[0-9]{4}-[0-9]{2}-[0-9]{2}")
TERMINAIS = {"ENTREGUE", "CANCELADO", "DEVOLVIDO"}
CAMPOS_ENVELOPE = ("id", "tipo", "ocorrido_em", "dados")

_estado = {"processados": set(), "rastreamentos": {}}


class ErroAPI(Exception):
    def __init__(self, status: int, codigo: str, mensagem: str):
        self.status = status
        self.codigo = codigo
        self.mensagem = mensagem


def _erro(status: int, codigo: str, mensagem: str) -> JSONResponse:
    return JSONResponse(status_code=status, content={"erro": codigo, "mensagem": mensagem})


@app.exception_handler(ErroAPI)
async def _tratar_erro_api(request: Request, exc: ErroAPI):
    return _erro(exc.status, exc.codigo, exc.mensagem)


@app.exception_handler(RequestValidationError)
async def _tratar_validacao(request: Request, exc: RequestValidationError):
    return _erro(422, "DADOS_INVALIDOS", "Dados inválidos na requisição.")


@app.exception_handler(StarletteHTTPException)
async def _tratar_http(request: Request, exc: StarletteHTTPException):
    if exc.status_code == 404:
        return _erro(404, "NAO_ENCONTRADO", "Rota não encontrada.")
    if exc.status_code == 405:
        return _erro(405, "METODO_NAO_PERMITIDO", "Método não permitido.")
    return _erro(exc.status_code, "ERRO", str(exc.detail))


def _invalido(mensagem: str) -> ErroAPI:
    return ErroAPI(422, "DADOS_INVALIDOS", mensagem)


async def _ler_envelope(request: Request) -> dict:
    try:
        corpo = await request.json()
    except Exception:
        raise _invalido("Corpo da requisição não é um JSON válido.")
    if not isinstance(corpo, dict):
        raise _invalido("Corpo da requisição deve ser um objeto JSON.")
    for campo in CAMPOS_ENVELOPE:
        if campo not in corpo:
            raise _invalido(f"Campo '{campo}' ausente no envelope.")
    if not isinstance(corpo["dados"], dict):
        raise _invalido("Campo 'dados' deve ser um objeto.")
    return corpo


@app.post("/admin/reset")
async def reset():
    _estado["processados"] = set()
    _estado["rastreamentos"] = {}
    return Response(status_code=204)


@app.post("/eventos")
async def receber_evento(request: Request):
    evento = await _ler_envelope(request)
    eid = evento["id"]
    tipo = evento["tipo"]
    dados = evento["dados"]

    if not isinstance(eid, (str, int)) or isinstance(eid, bool):
        eid = str(eid)
    if eid in _estado["processados"]:
        return JSONResponse(status_code=200, content={"processado": False})

    if tipo == "envio.criado":
        try:
            codigo = dados["codigo_rastreio"]
            rastreamento = {
                "codigo_rastreio": codigo,
                "envio_id": dados["envio_id"],
                "pedido_id": dados["pedido_id"],
                "transportadora_id": dados["transportadora_id"],
                "data_prevista_entrega": dados["data_prevista_entrega"],
                "historico": [
                    {"status": "CRIADO", "data_hora": dados["data_hora"], "local": None}
                ],
            }
        except (KeyError, TypeError):
            raise _invalido("Dados do evento envio.criado incompletos.")
        _estado["rastreamentos"][codigo] = rastreamento
        _estado["processados"].add(eid)
        return JSONResponse(status_code=200, content={"processado": True})

    if tipo == "envio.status_atualizado":
        try:
            codigo = dados["codigo_rastreio"]
            item = {
                "status": dados["status"],
                "data_hora": dados["data_hora"],
                "local": dados.get("local"),
            }
        except (KeyError, TypeError):
            raise _invalido("Dados do evento envio.status_atualizado incompletos.")
        rastreamento = _estado["rastreamentos"].get(codigo)
        if rastreamento is None:
            return JSONResponse(status_code=200, content={"processado": False})
        rastreamento["historico"].append(item)
        _estado["processados"].add(eid)
        return JSONResponse(status_code=200, content={"processado": True})

    return JSONResponse(status_code=200, content={"processado": False})


@app.get("/rastreamento/{codigo}")
async def consultar(codigo: str, request: Request):
    data_txt = request.query_params.get("data")
    data_ref = None
    if data_txt is not None:
        if not RE_DATA.fullmatch(data_txt):
            raise _invalido("Parâmetro 'data' deve estar no formato AAAA-MM-DD.")
        try:
            data_ref = date.fromisoformat(data_txt)
        except ValueError:
            raise _invalido("Parâmetro 'data' não é uma data válida.")

    r = _estado["rastreamentos"].get(codigo)
    if r is None:
        raise ErroAPI(404, "RASTREIO_NAO_ENCONTRADO", "Rastreamento não encontrado.")

    status = r["historico"][-1]["status"]
    atrasado = False
    if data_ref is not None and status not in TERMINAIS:
        try:
            atrasado = data_ref > date.fromisoformat(r["data_prevista_entrega"])
        except (ValueError, TypeError):
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
        "historico": [dict(i) for i in r["historico"]],
    }
    return JSONResponse(status_code=200, content=resposta)
