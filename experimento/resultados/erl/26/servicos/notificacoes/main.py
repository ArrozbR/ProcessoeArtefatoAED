# arquivo: servicos/notificacoes/main.py
import copy

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

_estado = {"notificacoes": [], "contador": 0, "processados": set()}

_INVALIDO = object()


def _erro(status, codigo, mensagem):
    return JSONResponse(status_code=status, content={"erro": codigo, "mensagem": mensagem})


@app.exception_handler(RequestValidationError)
async def _tratar_validacao(request, exc):
    return _erro(422, "DADOS_INVALIDOS", "Dados inválidos na requisição.")


@app.exception_handler(StarletteHTTPException)
async def _tratar_http(request, exc):
    if exc.status_code == 404:
        return _erro(404, "NAO_ENCONTRADO", "Rota não encontrada.")
    if exc.status_code == 405:
        return _erro(405, "METODO_NAO_PERMITIDO", "Método não permitido.")
    return _erro(exc.status_code, "ERRO", str(exc.detail))


async def _ler_json(request):
    try:
        return await request.json()
    except Exception:
        return _INVALIDO


def _chave_id(v):
    try:
        hash(v)
        return v
    except TypeError:
        return repr(v)


def _registrar(tipo, dados, ocorrido_em):
    pedido_id = dados.get("pedido_id")
    codigo = dados.get("codigo_rastreio")
    _estado["contador"] += 1
    _estado["notificacoes"].append({
        "id": f"NOT-{_estado['contador']:06d}",
        "email": dados.get("destinatario_email"),
        "pedido_id": pedido_id,
        "codigo_rastreio": codigo,
        "tipo": tipo,
        "mensagem": MENSAGENS[tipo].format(pedido_id=pedido_id, codigo_rastreio=codigo),
        "criada_em": ocorrido_em,
    })


@app.post("/eventos")
async def eventos(request: Request):
    corpo = await _ler_json(request)
    if corpo is _INVALIDO or not isinstance(corpo, dict):
        return _erro(422, "DADOS_INVALIDOS", "Envelope inválido.")
    for campo in ("id", "tipo", "ocorrido_em", "dados"):
        if campo not in corpo:
            return _erro(422, "DADOS_INVALIDOS", f"Campo '{campo}' ausente.")
    dados = corpo["dados"]
    if not isinstance(dados, dict):
        return _erro(422, "DADOS_INVALIDOS", "Campo 'dados' inválido.")

    eid = _chave_id(corpo["id"])
    if eid in _estado["processados"]:
        return JSONResponse(status_code=200, content={"processado": False})

    tipo = corpo["tipo"]
    if tipo == "envio.criado":
        _registrar("CRIADO", dados, corpo["ocorrido_em"])
    elif tipo == "envio.status_atualizado":
        status = dados.get("status")
        if isinstance(status, str) and status in MENSAGENS:
            _registrar(status, dados, corpo["ocorrido_em"])
    else:
        return JSONResponse(status_code=200, content={"processado": False})

    _estado["processados"].add(eid)
    return JSONResponse(status_code=200, content={"processado": True})


@app.get("/notificacoes")
async def listar(request: Request):
    email = request.query_params.get("email")
    if email is None:
        return _erro(422, "DADOS_INVALIDOS", "Parâmetro 'email' é obrigatório.")
    itens = [copy.deepcopy(n) for n in _estado["notificacoes"] if n["email"] == email]
    return JSONResponse(status_code=200, content=itens)


@app.post("/admin/reset")
async def reset():
    _estado["notificacoes"] = []
    _estado["contador"] = 0
    _estado["processados"] = set()
    return Response(status_code=204)
