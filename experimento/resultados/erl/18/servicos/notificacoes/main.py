# arquivo: servicos/notificacoes/main.py
import json
import threading

from fastapi import FastAPI, Request
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse, Response
from starlette.exceptions import HTTPException as StarletteHTTPException

app = FastAPI()

MENSAGENS = {
    "CRIADO": "Pedido {pedido_id}: envio criado. Rastreio: {codigo_rastreio}.",
    "SAIU_PARA_ENTREGA": "Pedido {pedido_id}: saiu para entrega.",
    "ENTREGUE": "Pedido {pedido_id}: entregue.",
    "FALHA_ENTREGA": "Pedido {pedido_id}: tentativa de entrega sem sucesso.",
    "DEVOLVIDO": "Pedido {pedido_id}: devolvido ao remetente.",
    "CANCELADO": "Pedido {pedido_id}: envio cancelado.",
}

_lock = threading.Lock()
_notificacoes: list = []
_eventos_processados: set = set()
_contador = {"notificacao": 0}


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
        return _erro(404, "RECURSO_NAO_ENCONTRADO", "Recurso não encontrado.")
    if exc.status_code == 405:
        return _erro(405, "METODO_NAO_PERMITIDO", "Método não permitido.")
    return _erro(exc.status_code, "ERRO", str(exc.detail))


def _invalido(mensagem: str) -> ErroAPI:
    return ErroAPI(422, "DADOS_INVALIDOS", mensagem)


async def _ler_envelope(request: Request) -> dict:
    try:
        bruto = await request.body()
        corpo = json.loads(bruto) if bruto else None
    except (ValueError, UnicodeDecodeError):
        raise _invalido("Corpo da requisição não é um JSON válido.")
    if not isinstance(corpo, dict):
        raise _invalido("Corpo da requisição deve ser um objeto JSON.")
    for campo in ("id", "tipo", "ocorrido_em", "dados"):
        if campo not in corpo:
            raise _invalido(f"Envelope sem o campo '{campo}'.")
    if not isinstance(corpo["id"], str) or not isinstance(corpo["tipo"], str):
        raise _invalido("Campos 'id' e 'tipo' devem ser texto.")
    if not isinstance(corpo["dados"], dict):
        raise _invalido("Campo 'dados' deve ser um objeto.")
    return corpo


def _registrar(tipo: str, dados: dict, criada_em) -> None:
    _contador["notificacao"] += 1
    pedido_id = dados.get("pedido_id")
    codigo = dados.get("codigo_rastreio")
    _notificacoes.append({
        "id": f"NOT-{_contador['notificacao']:06d}",
        "email": dados.get("destinatario_email"),
        "pedido_id": pedido_id,
        "codigo_rastreio": codigo,
        "tipo": tipo,
        "mensagem": MENSAGENS[tipo].format(pedido_id=pedido_id, codigo_rastreio=codigo),
        "criada_em": criada_em,
    })


@app.post("/eventos")
async def consumir_evento(request: Request):
    evento = await _ler_envelope(request)
    eid = evento["id"]
    tipo = evento["tipo"]
    dados = evento["dados"]
    with _lock:
        if eid in _eventos_processados:
            return JSONResponse(status_code=200, content={"processado": False})
        if tipo == "envio.criado":
            _registrar("CRIADO", dados, evento["ocorrido_em"])
        elif tipo == "envio.status_atualizado":
            status = dados.get("status")
            if isinstance(status, str) and status in MENSAGENS:
                _registrar(status, dados, evento["ocorrido_em"])
        else:
            return JSONResponse(status_code=200, content={"processado": False})
        _eventos_processados.add(eid)
    return JSONResponse(status_code=200, content={"processado": True})


@app.get("/notificacoes")
async def listar_notificacoes(request: Request):
    email = request.query_params.get("email")
    if email is None:
        raise _invalido("Parâmetro 'email' é obrigatório.")
    with _lock:
        itens = [dict(n) for n in _notificacoes if n["email"] == email]
    itens.sort(key=lambda n: n["id"])
    return JSONResponse(status_code=200, content=itens)


@app.post("/admin/reset")
async def reset():
    with _lock:
        _notificacoes.clear()
        _eventos_processados.clear()
        _contador["notificacao"] = 0
    return Response(status_code=204)
