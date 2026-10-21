# arquivo: servicos/notificacoes/main.py
import copy
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


def erro(status: int, codigo: str, mensagem: str) -> JSONResponse:
    return JSONResponse(status_code=status, content={"erro": codigo, "mensagem": mensagem})


def dados_invalidos(mensagem: str = "Dados inválidos.") -> JSONResponse:
    return erro(422, "DADOS_INVALIDOS", mensagem)


@app.exception_handler(RequestValidationError)
async def _handler_validacao(request: Request, exc: RequestValidationError):
    return dados_invalidos("Requisição com dados inválidos.")


@app.exception_handler(StarletteHTTPException)
async def _handler_http(request: Request, exc: StarletteHTTPException):
    if exc.status_code == 404:
        return erro(404, "RECURSO_NAO_ENCONTRADO", "Recurso não encontrado.")
    if exc.status_code == 405:
        return erro(405, "METODO_NAO_PERMITIDO", "Método não permitido.")
    return erro(exc.status_code, "ERRO", str(exc.detail))


async def ler_json(request: Request):
    try:
        return await request.json()
    except Exception:
        return None


def _registrar(tipo_notificacao: str, dados: dict, criada_em: str):
    pedido_id = dados["pedido_id"]
    codigo = dados["codigo_rastreio"]
    email = dados["destinatario_email"]
    mensagem = MENSAGENS[tipo_notificacao].format(pedido_id=pedido_id, codigo_rastreio=codigo)
    _contador["notificacao"] += 1
    _notificacoes.append({
        "id": "NOT-%06d" % _contador["notificacao"],
        "email": email,
        "pedido_id": pedido_id,
        "codigo_rastreio": codigo,
        "tipo": tipo_notificacao,
        "mensagem": mensagem,
        "criada_em": criada_em,
    })


@app.post("/eventos")
async def consumir(request: Request):
    corpo = await ler_json(request)
    if not isinstance(corpo, dict):
        return dados_invalidos("Envelope inválido.")
    for campo in ("id", "tipo", "ocorrido_em", "dados"):
        if campo not in corpo:
            return dados_invalidos("Envelope sem o campo %s." % campo)
    evt_id = corpo["id"]
    tipo = corpo["tipo"]
    dados = corpo["dados"]
    ocorrido_em = corpo["ocorrido_em"]
    if not isinstance(evt_id, str) or not isinstance(tipo, str) or not isinstance(dados, dict):
        return dados_invalidos("Envelope com tipos inválidos.")

    with _lock:
        if evt_id in _eventos_processados:
            return JSONResponse(status_code=200, content={"processado": False})

        if tipo == "envio.criado":
            status = "CRIADO"
        elif tipo == "envio.status_atualizado":
            status = dados.get("status")
            if not isinstance(status, str):
                return dados_invalidos("Dados do evento incompletos.")
        else:
            return JSONResponse(status_code=200, content={"processado": False})

        if status in MENSAGENS:
            for campo in ("pedido_id", "codigo_rastreio", "destinatario_email"):
                if campo not in dados:
                    return dados_invalidos("Dados do evento incompletos.")
            _registrar(status, dados, ocorrido_em)

        _eventos_processados.add(evt_id)
        return JSONResponse(status_code=200, content={"processado": True})


@app.get("/notificacoes")
async def listar(request: Request):
    if "email" not in request.query_params:
        return dados_invalidos("Parâmetro email é obrigatório.")
    email = request.query_params.get("email")
    with _lock:
        itens = [copy.deepcopy(n) for n in _notificacoes if n["email"] == email]
    itens.sort(key=lambda n: n["id"])
    return JSONResponse(status_code=200, content=itens)


@app.post("/admin/reset")
async def reset():
    with _lock:
        _notificacoes.clear()
        _eventos_processados.clear()
        _contador["notificacao"] = 0
    return Response(status_code=204)
