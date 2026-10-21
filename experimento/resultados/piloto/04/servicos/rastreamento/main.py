# arquivo: servicos/rastreamento/main.py
import copy
import re
from datetime import date
from typing import Optional

from fastapi import FastAPI, Request
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse, Response
from starlette.exceptions import HTTPException as StarletteHTTPException

app = FastAPI()

RE_DATA = re.compile(r"[0-9]{4}-[0-9]{2}-[0-9]{2}")
ESTADOS_TERMINAIS = {"ENTREGUE", "CANCELADO", "DEVOLVIDO"}
TIPOS_CONHECIDOS = {"envio.criado", "envio.status_atualizado"}

_estado = {"processados": set(), "rastreamentos": {}}


class ErroAPI(Exception):
    def __init__(self, status, erro, mensagem):
        super().__init__(mensagem)
        self.status = status
        self.erro = erro
        self.mensagem = mensagem


def _resposta_erro(status, erro, mensagem):
    return JSONResponse(status_code=status, content={"erro": erro, "mensagem": mensagem})


@app.exception_handler(ErroAPI)
async def _tratar_erro_api(request, exc):
    return _resposta_erro(exc.status, exc.erro, exc.mensagem)


@app.exception_handler(RequestValidationError)
async def _tratar_validacao(request, exc):
    return _resposta_erro(422, "DADOS_INVALIDOS", "Dados inválidos na requisição.")


@app.exception_handler(StarletteHTTPException)
async def _tratar_http(request, exc):
    if exc.status_code == 404:
        erro = "NAO_ENCONTRADO"
    elif exc.status_code == 405:
        erro = "METODO_NAO_PERMITIDO"
    else:
        erro = "ERRO"
    return _resposta_erro(exc.status_code, erro, str(exc.detail))


def _invalido(mensagem):
    return ErroAPI(422, "DADOS_INVALIDOS", mensagem)


async def _ler_corpo(request):
    try:
        corpo = await request.json()
    except Exception:
        raise _invalido("Corpo da requisição ausente ou não é JSON válido.")
    if not isinstance(corpo, dict):
        raise _invalido("O corpo da requisição deve ser um objeto JSON.")
    return corpo


def _chave_id(valor):
    if isinstance(valor, (dict, list)):
        return repr(valor)
    return str(valor)


def _campos(dados, nomes):
    if not isinstance(dados, dict):
        raise _invalido("Campo 'dados' deve ser um objeto.")
    for nome in nomes:
        if nome not in dados:
            raise _invalido(f"Campo 'dados.{nome}' ausente.")


@app.post("/eventos")
async def receber_evento(request: Request):
    corpo = await _ler_corpo(request)
    for campo in ("id", "tipo", "ocorrido_em", "dados"):
        if campo not in corpo:
            raise _invalido(f"Campo '{campo}' ausente no envelope.")

    chave = _chave_id(corpo["id"])
    if chave in _estado["processados"]:
        return {"processado": False}

    tipo = corpo["tipo"]
    if not isinstance(tipo, str) or tipo not in TIPOS_CONHECIDOS:
        return {"processado": False}

    dados = corpo["dados"]
    if tipo == "envio.criado":
        _campos(dados, ("envio_id", "codigo_rastreio", "pedido_id", "transportadora_id",
                        "data_prevista_entrega", "data_hora"))
        codigo = dados["codigo_rastreio"]
        _estado["rastreamentos"][codigo] = {
            "codigo_rastreio": codigo,
            "envio_id": dados["envio_id"],
            "pedido_id": dados["pedido_id"],
            "transportadora_id": dados["transportadora_id"],
            "data_prevista_entrega": dados["data_prevista_entrega"],
            "historico": [{"status": "CRIADO", "data_hora": dados["data_hora"], "local": None}],
        }
    else:
        _campos(dados, ("codigo_rastreio", "status", "data_hora"))
        codigo = dados["codigo_rastreio"]
        rastreamento = _estado["rastreamentos"].get(codigo) if isinstance(codigo, str) else None
        if rastreamento is None:
            return {"processado": False}
        rastreamento["historico"].append({
            "status": dados["status"],
            "data_hora": dados["data_hora"],
            "local": dados.get("local"),
        })

    _estado["processados"].add(chave)
    return {"processado": True}


@app.get("/rastreamento/{codigo_rastreio}")
async def consultar_rastreamento(codigo_rastreio: str, data: Optional[str] = None):
    data_consulta = None
    if data is not None:
        if not RE_DATA.fullmatch(data):
            raise _invalido("Parâmetro 'data' deve estar no formato AAAA-MM-DD.")
        try:
            data_consulta = date.fromisoformat(data)
        except ValueError:
            raise _invalido("Parâmetro 'data' não é uma data válida.")

    r = _estado["rastreamentos"].get(codigo_rastreio)
    if r is None:
        raise ErroAPI(404, "RASTREIO_NAO_ENCONTRADO", "Rastreamento não encontrado.")

    status = r["historico"][-1]["status"]
    atrasado = False
    if data_consulta is not None and status not in ESTADOS_TERMINAIS:
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
        "historico": copy.deepcopy(r["historico"]),
    }


@app.post("/admin/reset", status_code=204)
async def reiniciar():
    _estado["processados"] = set()
    _estado["rastreamentos"] = {}
    return Response(status_code=204)
