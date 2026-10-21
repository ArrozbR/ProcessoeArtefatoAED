# arquivo: servicos/rastreamento/main.py
import re
from datetime import date

from fastapi import FastAPI, Request
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse, Response
from starlette.exceptions import HTTPException as StarletteHTTPException

app = FastAPI()

RE_DATA = re.compile(r"^[0-9]{4}-[0-9]{2}-[0-9]{2}$")
TERMINAIS = {"ENTREGUE", "CANCELADO", "DEVOLVIDO"}

_estado = {"processados": set(), "rastreios": {}}


class ErroAPI(Exception):
    def __init__(self, status, erro, mensagem):
        super().__init__(mensagem)
        self.status = status
        self.erro = erro
        self.mensagem = mensagem


def _resposta_erro(status, erro, mensagem):
    return JSONResponse(status_code=status, content={"erro": erro, "mensagem": mensagem})


def _invalido(mensagem):
    return ErroAPI(422, "DADOS_INVALIDOS", mensagem)


@app.exception_handler(ErroAPI)
async def _tratar_erro_api(request, exc):
    return _resposta_erro(exc.status, exc.erro, exc.mensagem)


@app.exception_handler(RequestValidationError)
async def _tratar_validacao(request, exc):
    return _resposta_erro(422, "DADOS_INVALIDOS", "Dados inválidos na requisição.")


@app.exception_handler(StarletteHTTPException)
async def _tratar_http(request, exc):
    if exc.status_code == 404:
        return _resposta_erro(404, "RECURSO_NAO_ENCONTRADO", "Rota não encontrada.")
    if exc.status_code == 405:
        return _resposta_erro(405, "METODO_NAO_PERMITIDO", "Método não permitido.")
    return _resposta_erro(exc.status_code, "ERRO", str(exc.detail))


async def _ler_corpo(request):
    try:
        corpo = await request.json()
    except Exception:
        raise _invalido("Corpo da requisição ausente ou não é JSON válido.")
    if not isinstance(corpo, dict):
        raise _invalido("O corpo da requisição deve ser um objeto JSON.")
    return corpo


def _validar_envelope(corpo):
    for campo in ("id", "tipo", "ocorrido_em", "dados"):
        if campo not in corpo:
            raise _invalido(f"Campo '{campo}' ausente no envelope.")
    if not isinstance(corpo["id"], str) or not isinstance(corpo["tipo"], str):
        raise _invalido("Campos 'id' e 'tipo' devem ser texto.")
    if not isinstance(corpo["dados"], dict):
        raise _invalido("Campo 'dados' deve ser um objeto.")


@app.post("/admin/reset")
async def reset():
    _estado["processados"] = set()
    _estado["rastreios"] = {}
    return Response(status_code=204)


@app.post("/eventos")
async def eventos(request: Request):
    corpo = await _ler_corpo(request)
    _validar_envelope(corpo)

    eid = corpo["id"]
    tipo = corpo["tipo"]
    dados = corpo["dados"]

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
        rastreio = _estado["rastreios"].get(codigo) if isinstance(codigo, str) else None
        if rastreio is None:
            return JSONResponse(status_code=200, content={"processado": False})
        rastreio["historico"].append(
            {
                "status": dados.get("status"),
                "data_hora": dados.get("data_hora"),
                "local": dados.get("local"),
            }
        )
        _estado["processados"].add(eid)
        return JSONResponse(status_code=200, content={"processado": True})

    return JSONResponse(status_code=200, content={"processado": False})


@app.get("/rastreamento/{codigo}")
async def consultar(codigo: str, request: Request):
    data_param = request.query_params.get("data")
    data_ref = None
    if data_param is not None:
        if not RE_DATA.match(data_param):
            raise _invalido("Parâmetro 'data' deve estar no formato AAAA-MM-DD.")
        try:
            data_ref = date.fromisoformat(data_param)
        except ValueError:
            raise _invalido("Parâmetro 'data' não é uma data válida.")

    rastreio = _estado["rastreios"].get(codigo)
    if rastreio is None:
        raise ErroAPI(404, "RASTREIO_NAO_ENCONTRADO", "Rastreamento não encontrado.")

    status = rastreio["historico"][-1]["status"]
    atrasado = False
    if data_ref is not None and status not in TERMINAIS:
        try:
            prevista = date.fromisoformat(rastreio["data_prevista_entrega"])
            atrasado = data_ref > prevista
        except Exception:
            atrasado = False

    resposta = {
        "codigo_rastreio": rastreio["codigo_rastreio"],
        "envio_id": rastreio["envio_id"],
        "pedido_id": rastreio["pedido_id"],
        "transportadora_id": rastreio["transportadora_id"],
        "data_prevista_entrega": rastreio["data_prevista_entrega"],
        "status": status,
        "entregue": status == "ENTREGUE",
        "atrasado": atrasado,
        "historico": [dict(item) for item in rastreio["historico"]],
    }
    return JSONResponse(status_code=200, content=resposta)
